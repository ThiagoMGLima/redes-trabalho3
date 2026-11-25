import ctypes
import os
import socket
import tempfile
import threading
import time
import unittest

from server import Server


_AT_FDCWD = -100
_RENAME_EXCHANGE = 2


def _renameat2():
  try:
    function = ctypes.CDLL(None, use_errno=True).renameat2
  except (AttributeError, OSError):
    return None
  function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
  return function


_RENAMEAT2 = _renameat2()
CAN_EXCHANGE = _RENAMEAT2 is not None


def exchange(first, second):
  """Troca atomicamente os objetos que estão nos caminhos `first` e
  `second`, como renameat2 com RENAME_EXCHANGE (Linux): em nenhum instante
  um dos nomes deixa de existir."""
  if _RENAMEAT2(_AT_FDCWD, os.fsencode(first), _AT_FDCWD, os.fsencode(second), _RENAME_EXCHANGE) != 0:
    error = ctypes.get_errno()
    raise OSError(error, os.strerror(error), first)


class Swapper:
  """Thread que troca sem parar, com `exchange`, os objetos de dois caminhos,
  até `stop`. Ao parar, devolve cada objeto ao caminho em que estava."""

  def __init__(self, first, second):
    self._paths = (first, second)
    self._stop = threading.Event()
    self._swaps = 0
    self._thread = threading.Thread(target=self._run, daemon=True)

  def start(self):
    self._thread.start()
    return self

  def stop(self):
    self._stop.set()
    self._thread.join(5)
    if self._swaps % 2:
      exchange(*self._paths)

  def _run(self):
    while not self._stop.is_set():
      exchange(*self._paths)
      self._swaps += 1


CLEANUP_LIMIT = 10.0


def _live_threads(*ignored):
  return sorted(thread.name for thread in threading.enumerate() if thread.is_alive() and thread not in ignored)


def shut_down_within(server, timeout=CLEANUP_LIMIT):
  """Encerra `server` numa thread à parte e falha, em vez de travar a suíte,
  se o encerramento não terminar em `timeout` segundos, o que indica uma
  thread do servidor que não finalizou. O encerramento continua esperando
  todas as sessões, como na produção; só o teste deixa de esperar."""
  failures = []

  def stop():
    try:
      server.shutdown()
    except BaseException as error:
      failures.append(error)
  stopping = threading.Thread(target=stop, name="encerramento no teste", daemon=True)
  stopping.start()
  stopping.join(timeout)
  if stopping.is_alive():
    raise AssertionError(f"o encerramento do servidor não terminou em {timeout} s; threads vivas: "
                         f"{_live_threads(stopping, threading.current_thread())}")
  if failures:
    raise failures[0]


def wait_until(predicate, timeout=5.0, interval=0.01):
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    if predicate():
      return True
    time.sleep(interval)
  return predicate()


class LogRecorder:
  """Substitui o terminal do servidor: guarda cada linha registrada."""

  def __init__(self):
    self._lock = threading.Lock()
    self._lines = []

  def __call__(self, line):
    with self._lock:
      self._lines.append(str(line))

  @property
  def lines(self):
    with self._lock:
      return list(self._lines)

  def matching(self, text):
    return [line for line in self.lines if text in line]

  def wait_for(self, text, timeout=5.0):
    if not wait_until(lambda: self.matching(text), timeout):
      raise AssertionError(f"{text!r} não apareceu no registro: {self.lines}")
    return self.matching(text)[0]


class ThreadErrors:
  """Registra exceções não tratadas em threads durante um teste."""

  def __init__(self, test_case):
    self.errors = []
    previous = threading.excepthook
    threading.excepthook = lambda args: self.errors.append(args.exc_value)
    test_case.addCleanup(setattr, threading, "excepthook", previous)


def free_port():
  with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
    probe.bind(("127.0.0.1", 0))
    return probe.getsockname()[1]


class OutputReader:
  """Acumula a saída de um processo em segundo plano, inclusive prompts sem
  quebra de linha."""

  def __init__(self, stream):
    self._stream = stream
    self._lock = threading.Lock()
    self._text = ""
    self._thread = threading.Thread(target=self._read, daemon=True)
    self._thread.start()

  def _read(self):
    while chunk := self._stream.read(1):
      with self._lock:
        self._text += chunk

  @property
  def text(self):
    with self._lock:
      return self._text

  def close(self):
    self._thread.join(5)
    self._stream.close()

  def wait_for(self, text, timeout=5.0):
    if not wait_until(lambda: text in self.text, timeout):
      raise AssertionError(f"{text!r} não apareceu na saída: {self.text!r}")


PAGE = "<html><body><h1>Olá, você</h1>\r\n<p>conteúdo</p></body></html>\n".encode("utf-8")


def put(root, path, content):
  full = os.path.join(root, *path.split("/"))
  os.makedirs(os.path.dirname(full), exist_ok=True)
  with open(full, "wb") as file:
    file.write(content)


class Response:
  """Resposta lida até o fechamento da conexão, separada na primeira linha
  em branco."""

  def __init__(self, data):
    head, separator, self.body = data.partition(b"\r\n\r\n")
    if not separator:
      raise AssertionError(f"resposta sem linha em branco: {data[:200]!r}")
    lines = head.decode("ascii").split("\r\n")
    self.status_line = lines[0]
    self.header_lines = lines[1:]
    self.headers = {}
    for line in self.header_lines:
      name, _, value = line.partition(": ")
      self.headers[name.lower()] = value

  @property
  def status(self):
    return int(self.status_line.split(" ")[1])


class HttpTestCase(unittest.TestCase):
  """Servidor real numa porta livre, com a pasta raiz `www/` num diretório
  temporário."""

  def setUp(self):
    base = tempfile.TemporaryDirectory()
    self.addCleanup(base.cleanup)
    self.root = os.path.join(base.name, "www")
    os.mkdir(self.root)

  def start_server(self, root=None, **options):
    self.log = LogRecorder()
    self.server = Server("127.0.0.1", 0, log=self.log, root=root or self.root, **options)
    self.server.start()
    self.addCleanup(shut_down_within, self.server)

  def open_connection(self):
    sock = socket.create_connection(self.server.address, timeout=5)
    self.addCleanup(sock.close)
    return sock

  def read_until_closed(self, sock):
    data = bytearray()
    while chunk := sock.recv(65536):
      data += chunk
    return bytes(data)

  def exchange(self, raw):
    """Envia `raw` de uma vez, lê a resposta inteira e fecha a conexão, como
    o navegador faz depois de uma resposta com Connection: close."""
    sock = self.open_connection()
    sock.sendall(raw)
    data = self.read_until_closed(sock)
    sock.close()
    return Response(data)

  def get(self, target):
    return self.exchange(f"GET {target} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode("ascii"))

  def assertErrorPage(self, response, status_line, title):
    """A resposta é a página de erro HTML esperada, com os seis cabeçalhos
    de toda resposta, na ordem, e o tamanho exato; devolve o texto da
    página."""
    self.assertEqual(response.status_line, status_line)
    self.assertEqual(
      [line.split(":")[0] for line in response.header_lines][:6],
      ["Date", "Server", "Content-Type", "Content-Length", "Connection", "Cache-Control"])
    self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
    self.assertEqual(response.headers["content-length"], str(len(response.body)))
    self.assertEqual(response.headers["connection"], "close")
    page = response.body.decode("utf-8")
    self.assertTrue(page.startswith("<!DOCTYPE html>"), page)
    self.assertIn(title, page)
    return page

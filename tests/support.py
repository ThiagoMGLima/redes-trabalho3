import ctypes
import os
import select
import shutil
import socket
import threading
import time

import protocol as p

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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


def receive_within(sock, timeout=5.0):
  """Recebe o próximo frame, falhando se nenhum começar a chegar a tempo.
  Preserva o timeout do socket, que o protocolo torna não bloqueante."""
  previous = sock.gettimeout()
  readable, _, _ = select.select([sock], [], [], timeout)
  if not readable:
    raise AssertionError(f"nenhuma mensagem recebida em {timeout} s")
  try:
    return p.receive_message(sock, timeout)
  finally:
    sock.settimeout(previous)


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


def tcp_pair(small_buffers=False):
  """Conexão TCP local; com buffers pequenos, um frame grande só avança
  quando o outro lado lê."""
  with socket.create_server(("127.0.0.1", 0)) as listener:
    if small_buffers:
      listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    near = socket.socket()
    if small_buffers:
      near.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
    near.connect(listener.getsockname())
    far, _ = listener.accept()
  return near, far


def install_programs(directory):
  """Copia os programas do projeto para `directory`. A cópia usa as raízes
  files/ e downloads/ ao lado dela, não as do projeto."""
  os.makedirs(directory, exist_ok=True)
  for name in os.listdir(PROJECT_ROOT):
    if name.endswith(".py"):
      shutil.copy(os.path.join(PROJECT_ROOT, name), directory)
  return directory


def free_port():
  with socket.create_server(("127.0.0.1", 0)) as probe:
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

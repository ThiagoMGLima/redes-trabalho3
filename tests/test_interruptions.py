"""Interrupções de um download ativo: Sair, perda de conexão, prazo e
encerramento do servidor, observados pela fronteira TCP."""
import hashlib
import importlib.util
import os
import signal
import socket
import subprocess
import struct
import sys
import tempfile
import threading
import unittest

import protocol as p
from server import Server
from tests.support import (
  LogRecorder, OutputReader, ScriptedServerTestCase, ThreadErrors, free_port, install_programs, receive_within,
  shut_down_within, wait_until,
)
from tests.test_download import CONCLUDED, files_under, share
from tests.test_server import address_of

BIG_SIZE = 32 * 1024 * 1024


def share_sparse(root, path, size):
  """Arquivo de zeros grande o bastante para que a transferência não termine
  sozinha: os buffers TCP não comportam o arquivo inteiro."""
  full = os.path.join(root, *path.split("/"))
  os.makedirs(os.path.dirname(full), exist_ok=True)
  with open(full, "wb") as file:
    file.truncate(size)
  return full


def whole_frames(size, blocks):
  """Bytes do FILE_META de um arquivo de `size` bytes e de seus primeiros
  `blocks` blocos completos."""
  return len(p.encode(p.FILE_META, {"size": size})) + blocks * len(p.encode(p.FILE_DATA, bytes(p.CHUNK_SIZE)))


def is_open_here(path):
  """Informa se algum descritor deste processo, onde o servidor dos testes
  roda, está aberto para `path`."""
  target = os.path.realpath(path)
  for descriptor in os.listdir("/proc/self/fd"):
    try:
      if os.readlink(f"/proc/self/fd/{descriptor}") == target:
        return True
    except OSError:
      continue
  return False


class Relay:
  """Repassa uma conexão TCP entre o cliente e o servidor. O sentido cliente
  → servidor é livre; o sentido servidor → cliente entrega somente os bytes
  liberados por `allow`, então o teste decide quanto da transferência chega
  ao cliente. O lado do servidor tem buffer de recepção pequeno: quando o
  repasse para, o envio do servidor logo fica bloqueado."""

  CHUNK = 64 * 1024

  def __init__(self, test, server_address):
    self._server_address = server_address
    self._listener = socket.create_server(("127.0.0.1", 0))
    self._listener.settimeout(5)
    self.address = self._listener.getsockname()[:2]
    self._changed = threading.Condition()
    self._allowance = 0
    self._closed = False
    self._sockets = []
    self._connected = threading.Event()
    self.server_side_name = None
    threading.Thread(target=self._run, daemon=True).start()
    test.addCleanup(self.close)

  def wait_connected(self):
    if not self._connected.wait(5):
      raise AssertionError("o cliente não se conectou ao repasse")
    return self.server_side_name

  def allow(self, count=None):
    """Libera mais `count` bytes do servidor ao cliente; sem `count`, libera
    tudo daqui em diante."""
    with self._changed:
      if count is None or self._allowance is None:
        self._allowance = None
      else:
        self._allowance += count
      self._changed.notify_all()

  def close(self):
    with self._changed:
      self._closed = True
      self._changed.notify_all()
    self._listener.close()
    for sock in self._sockets:
      try:
        sock.shutdown(socket.SHUT_RDWR)
      except OSError:
        pass
      sock.close()

  def _run(self):
    try:
      client_side, _ = self._listener.accept()
    except OSError:
      return
    client_side.settimeout(None)
    server_side = socket.socket()
    server_side.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    server_side.connect(self._server_address)
    self._sockets += [client_side, server_side]
    self.server_side_name = address_of(server_side)
    self._connected.set()
    threading.Thread(target=self._to_server, args=(client_side, server_side), daemon=True).start()
    self._to_client(server_side, client_side)

  def _to_server(self, source, target):
    try:
      while data := source.recv(self.CHUNK):
        target.sendall(data)
      target.shutdown(socket.SHUT_WR)
    except OSError:
      self._abort(target)

  def _to_client(self, source, target):
    try:
      while size := self._wait_allowance():
        data = source.recv(size)
        if not data:
          target.shutdown(socket.SHUT_WR)
          return
        target.sendall(data)
        with self._changed:
          if self._allowance is not None:
            self._allowance -= len(data)
    except OSError:
      self._abort(target)

  def _wait_allowance(self):
    with self._changed:
      self._changed.wait_for(lambda: self._closed or self._allowance != 0)
      if self._closed:
        return 0
      return self.CHUNK if self._allowance is None else min(self._allowance, self.CHUNK)

  def _abort(self, target):
    try:
      target.shutdown(socket.SHUT_RDWR)
    except OSError:
      pass


@unittest.skipUnless(os.path.isdir("/proc/self/fd"), "observa os arquivos abertos pelo servidor em /proc/self/fd")
class RelayedDownloadTest(unittest.TestCase):
  """Servidor real com raiz compartilhada temporária e cliente rodando como
  no terminal, a partir de uma cópia do programa, conectado por um repasse
  que controla o andamento da transferência."""

  def start(self, **server_options):
    base = tempfile.TemporaryDirectory()
    self.addCleanup(base.cleanup)
    self.shared_root = os.path.join(base.name, "servidor", "files")
    os.makedirs(self.shared_root)
    program = install_programs(os.path.join(base.name, "cliente"))
    self.downloads_root = os.path.join(program, "downloads")
    os.mkdir(self.downloads_root)

    self.log = LogRecorder()
    self.server = Server("127.0.0.1", 0, log=self.log, shared_root=self.shared_root, **server_options)
    self.server.start()
    self.addCleanup(shut_down_within, self.server)
    self.relay = Relay(self, self.server.address)
    self.client = subprocess.Popen(
      [sys.executable, "-u", os.path.join(program, "client.py"), *map(str, self.relay.address)],
      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=base.name,
      encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    self.output = OutputReader(self.client.stdout)
    self.addCleanup(self.output.close)
    self.addCleanup(self.client.wait, 5)
    self.addCleanup(self.client.kill)
    self.addCleanup(self.client.stdin.close)
    self.client_name = self.relay.wait_connected()
    self.log.wait_for(f"Conectado: {self.client_name}")

  def connect_other(self):
    sock = socket.create_connection(self.server.address, timeout=5)
    self.addCleanup(sock.close)
    self.log.wait_for(f"Conectado: {address_of(sock)}")
    return sock

  def type_line(self, line):
    self.client.stdin.write(line + "\n")
    self.client.stdin.flush()

  def start_big_download(self, path, blocks=2):
    """Pede um arquivo grande e deixa chegar ao cliente apenas o FILE_META e
    `blocks` blocos; devolve o caminho do arquivo no servidor."""
    shared = share_sparse(self.shared_root, path, BIG_SIZE)
    self.relay.allow(whole_frames(BIG_SIZE, blocks))
    self.type_line("2")
    self.type_line(path)
    temporary_with_content = lambda: [
      name for name in files_under(self.downloads_root)
      if os.path.getsize(os.path.join(self.downloads_root, name)) == blocks * p.CHUNK_SIZE
    ]
    self.assertTrue(wait_until(temporary_with_content), files_under(self.downloads_root))
    self.assertTrue(is_open_here(shared))
    return shared

  def test_chat_then_sair_during_a_slow_transfer_releases_the_file_and_removes_the_temporary(self):
    self.start()
    other = self.connect_other()
    share(self.shared_root, "pequeno.txt", b"ainda funciona")
    shared = self.start_big_download("grande.bin")

    self.type_line("3")
    self.type_line("saindo no meio")
    self.assertEqual(receive_within(other), (p.CHAT, {"origin": "Cliente 1", "text": "saindo no meio"}))
    self.type_line("1")

    self.assertEqual(self.client.wait(5), 0)
    self.assertIn("QUIT", self.log.wait_for(f"Desconectado: {self.client_name}"))
    self.assertTrue(wait_until(lambda: not is_open_here(shared)), "o servidor não liberou o arquivo")
    self.assertEqual(files_under(self.downloads_root), [])
    self.assertNotIn(CONCLUDED, self.output.text)
    self.assertNotIn("Traceback", self.output.text)
    other.sendall(p.encode(p.FILE_REQUEST, {"path": "pequeno.txt"}))
    self.assertEqual(receive_within(other), (p.FILE_META, {"size": 14}))
    self.assertEqual(receive_within(other), (p.FILE_DATA, b"ainda funciona"))
    self.assertEqual(receive_within(other).type, p.FILE_END)
    self.assertEqual(len(self.log.matching("Desconectado:")), 1)

  def download_small(self, path, content):
    """Baixa por inteiro um arquivo pequeno, liberando exatamente seus frames."""
    sha256 = share(self.shared_root, path, content)
    self.relay.allow(sum(len(frame) for frame in (
      p.encode(p.FILE_META, {"size": len(content)}), p.encode(p.FILE_DATA, content),
      p.encode(p.FILE_END, {"sha256": sha256}),
    )))
    self.type_line("2")
    self.type_line(path)
    self.output.wait_for(CONCLUDED)

  def test_send_deadline_expiring_during_a_transfer_disconnects_and_the_client_discards_only_that_download(self):
    self.start(send_timeout=0.5)
    self.download_small("pronto.txt", b"pronto")
    shared = self.start_big_download("grande.bin")

    self.assertIn("prazo de envio", self.log.wait_for(f"Desconectado: {self.client_name}"))
    self.assertTrue(wait_until(lambda: not is_open_here(shared)), "o servidor não liberou o arquivo")
    self.relay.allow()

    self.output.wait_for("Desconectado do servidor")
    self.assertIn("Download de grande.bin interrompido", self.output.text)
    self.assertEqual(self.output.text.count(CONCLUDED), 1)
    self.assertEqual(files_under(self.downloads_root), ["pronto.txt"])
    self.client.stdin.close()
    self.assertEqual(self.client.wait(5), 0)
    self.assertNotIn("Traceback", self.output.text)


class ScriptedServerInterruptionTest(ScriptedServerTestCase):

  def start_transfer(self, path, size=3 * p.CHUNK_SIZE):
    """Pede `path`, envia FILE_META e um bloco e espera o bloco chegar ao
    temporário; devolve o caminho do temporário."""
    self.request(path)
    self.send(p.FILE_META, {"size": size})
    self.send(p.FILE_DATA, bytes(p.CHUNK_SIZE))
    written = lambda: [
      name for name in files_under(self.downloads_root)
      if os.path.getsize(self.downloaded(name)) == p.CHUNK_SIZE
    ]
    self.assertTrue(wait_until(written), files_under(self.downloads_root))
    return self.downloaded(written()[0])

  def forbid_removals_in(self, folder):
    os.chmod(folder, 0o555)
    self.addCleanup(os.chmod, folder, 0o755)

  def assertExitsAfterEnter(self):
    self.client.stdin.close()
    self.assertEqual(self.client.wait(5), 0)
    self.assertNotIn("Traceback", self.output.text)

  def assertDisconnectionDiscardsOnlyTheActiveDownload(self, end_connection, timeout=5.0, connection_open=False):
    """Um download é concluído e outro está em andamento quando a sessão
    termina: a desconexão aparece em até `timeout` segundos sem que se
    pressione Enter, o temporário some, nada mais é anunciado como concluído
    e o primeiro permanece. Com `connection_open`, o servidor não encerrou a
    conexão, e o cliente a encerra sem esperar Enter."""
    self.request("pronto.txt")
    self.send(p.FILE_META, {"size": 6})
    self.send(p.FILE_DATA, b"pronto")
    self.send(p.FILE_END, {"sha256": hashlib.sha256(b"pronto").hexdigest()})
    self.output.wait_for(CONCLUDED)
    self.start_transfer("aulas/grande.bin")

    end_connection()

    self.output.wait_for("Desconectado do servidor", timeout)
    self.assertIn("Download de aulas/grande.bin interrompido", self.output.text)
    self.assertEqual(self.output.text.count(CONCLUDED), 1)
    self.assertEqual(files_under(self.downloads_root), ["pronto.txt"])
    with open(self.downloaded("pronto.txt"), "rb") as file:
      self.assertEqual(file.read(), b"pronto")
    if connection_open:
      self.assertConnectionEndedByTheClient()
    self.assertExitsAfterEnter()

  def test_sair_with_a_request_still_pending_interrupts_the_download(self):
    """O pedido chegou ao servidor, mas o FILE_META ainda não voltou: Sair
    envia QUIT e o download pendente é marcado como interrompido, sem nada
    no destino."""
    self.request("aulas/pendente.bin")

    self.type_line("1")

    self.assertEqual(receive_within(self.server_side), (p.QUIT, {}))
    self.output.wait_for("Download de aulas/pendente.bin interrompido")
    self.assertEqual(self.client.wait(5), 0)
    self.assertNotIn(CONCLUDED, self.output.text)
    self.assertNotIn("Desconectado do servidor", self.output.text)
    self.assertEqual(files_under(self.downloads_root), [])
    self.assertNotIn("Traceback", self.output.text)

  def test_connection_closed_between_frames_before_file_end(self):
    self.assertDisconnectionDiscardsOnlyTheActiveDownload(self.server_side.close)

  def test_connection_closed_in_the_middle_of_a_block(self):
    def cut():
      self.server_side.sendall(p.encode(p.FILE_DATA, bytes(p.CHUNK_SIZE))[:100])
      self.server_side.close()
    self.assertDisconnectionDiscardsOnlyTheActiveDownload(cut)

  def test_connection_lost_with_a_reset_during_the_transfer(self):
    def reset():
      self.server_side.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
      self.server_side.close()
    self.assertDisconnectionDiscardsOnlyTheActiveDownload(reset)

  def test_receive_deadline_expiring_in_the_middle_of_a_block(self):
    """Um bloco começa a chegar e para, sem que a conexão termine: o prazo
    de recepção do cliente expira, o cliente encerra a conexão e a sessão
    acaba como numa desconexão."""
    stall = lambda: self.server_side.sendall(p.encode(p.FILE_DATA, bytes(p.CHUNK_SIZE))[:100])
    self.assertDisconnectionDiscardsOnlyTheActiveDownload(stall, timeout=p.RECEIVE_TIMEOUT + 5, connection_open=True)

  @unittest.skipIf(os.name != "posix" or os.geteuid() == 0, "requer permissões POSIX que o usuário não ignore")
  def test_failure_to_remove_the_temporary_after_a_disconnection_is_reported(self):
    temporary = self.start_transfer("aulas/grande.bin")
    self.forbid_removals_in(os.path.dirname(temporary))

    self.server_side.close()

    self.output.wait_for("Desconectado do servidor")
    self.assertIn(f"Não foi possível remover o arquivo temporário {temporary}", self.output.text)
    self.assertNotIn(CONCLUDED, self.output.text)
    self.assertFalse(os.path.lexists(self.downloaded("aulas/grande.bin")))
    self.assertExitsAfterEnter()

  @unittest.skipIf(os.name != "posix" or os.geteuid() == 0, "requer permissões POSIX que o usuário não ignore")
  def test_failure_to_remove_the_temporary_after_a_mismatch_is_reported_and_the_session_continues(self):
    temporary = self.start_transfer("aulas/grande.bin")
    self.forbid_removals_in(os.path.dirname(temporary))

    self.send(p.FILE_END, {"sha256": "0" * 64})

    self.output.wait_for("Download de aulas/grande.bin falhou")
    self.output.wait_for(f"Não foi possível remover o arquivo temporário {temporary}")
    self.send(p.CHAT, {"origin": "Servidor", "text": "a sessão continua"})
    self.output.wait_for("[Servidor] a sessão continua")
    self.assertNotIn(CONCLUDED, self.output.text)
    self.assertNotIn("Desconectado", self.output.text)
    self.assertFalse(os.path.lexists(self.downloaded("aulas/grande.bin")))
    self.type_line("1")
    self.assertEqual(receive_within(self.server_side), (p.QUIT, {}))
    self.assertExitsAfterEnter()


# Inicia o comando seguinte com um limite para o tamanho dos arquivos
# gravados, como num disco cheio: acima dele, a gravação falha com EFBIG em
# vez de encerrar o processo com SIGXFSZ.
LIMITED_FILE_SIZE = (
  "import os, resource, signal, sys; signal.signal(signal.SIGXFSZ, signal.SIG_IGN); "
  "resource.setrlimit(resource.RLIMIT_FSIZE, (int(sys.argv[1]), int(sys.argv[1]))); os.execv(sys.argv[2], sys.argv[2:])"
)


@unittest.skipUnless(importlib.util.find_spec("resource") and hasattr(signal, "SIGXFSZ"), "requer RLIMIT_FSIZE")
class LocalWriteFailureTest(ScriptedServerTestCase):
  """O cliente não consegue gravar arquivos de mais de 10.000 bytes, e o
  teste faz o papel do servidor."""

  def client_command(self, command):
    return [sys.executable, "-c", LIMITED_FILE_SIZE, "10000", *command]

  def test_local_write_failure_ends_the_session_reporting_it_and_removing_the_temporary(self):
    """A Q7 da #5 não definiu política própria para a falha de gravação
    local, e a sessão termina, como antes. O fim é o de qualquer sessão: a
    falha e a interrupção do download são informadas, o temporário é
    removido, a desconexão aparece e a conexão é encerrada sem esperar
    Enter."""
    self.request("aulas/grande.bin")

    self.server_side.sendall(p.encode(p.FILE_META, {"size": 2 * p.CHUNK_SIZE})
                             + p.encode(p.FILE_DATA, bytes(p.CHUNK_SIZE)) * 2)

    self.output.wait_for("Desconectado do servidor")
    self.assertIn("Falha local ao gravar o download de aulas/grande.bin", self.output.text)
    self.assertIn("Download de aulas/grande.bin interrompido", self.output.text)
    self.assertNotIn(CONCLUDED, self.output.text)
    self.assertEqual(files_under(self.downloads_root), [])
    self.assertConnectionEndedByTheClient()
    self.client.stdin.close()
    self.assertEqual(self.client.wait(5), 0)
    self.assertNotIn("Traceback", self.output.text)


def stalled_download(test, address, path, wait_connected):
  """Participante que pede um arquivo grande, recebe o FILE_META e o
  primeiro bloco e deixa de ler: o envio do servidor fica bloqueado no meio
  da transferência. `wait_connected` espera o registro da conexão."""
  sock = socket.socket()
  test.addCleanup(sock.close)
  sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
  sock.settimeout(5)
  sock.connect(address)
  wait_connected(f"Conectado: {address_of(sock)}")
  sock.sendall(p.encode(p.FILE_REQUEST, {"path": path}))
  test.assertEqual(receive_within(sock), (p.FILE_META, {"size": BIG_SIZE}))
  test.assertEqual(receive_within(sock).type, p.FILE_DATA)
  return sock


def read_until_closed(sock):
  """Lê o que o servidor já havia enviado até o fim da conexão; um reset
  também é fim."""
  try:
    while sock.recv(65536):
      pass
  except ConnectionResetError:
    pass


@unittest.skipUnless(os.path.isdir("/proc/self/fd"), "observa os arquivos abertos pelo servidor em /proc/self/fd")
class ServerInterruptionTest(unittest.TestCase):
  """Servidor real com raiz compartilhada temporária e prazo de envio longo:
  um envio bloqueado só termina se for interrompido. Os participantes se
  conectam diretamente por TCP."""

  def setUp(self):
    thread_errors = ThreadErrors(self)
    self.addCleanup(lambda: self.assertEqual(thread_errors.errors, []))
    self.threads_before = set(threading.enumerate())
    base = tempfile.TemporaryDirectory()
    self.addCleanup(base.cleanup)
    self.shared_root = base.name

  def start_server(self):
    self.log = LogRecorder()
    self.server = Server("127.0.0.1", 0, log=self.log, shared_root=self.shared_root, send_timeout=60)
    self.server.start()
    self.addCleanup(shut_down_within, self.server)

  def connect(self):
    sock = socket.create_connection(self.server.address, timeout=5)
    self.addCleanup(sock.close)
    self.log.wait_for(f"Conectado: {address_of(sock)}")
    return sock

  def stalled_download(self, path):
    """Devolve o participante parado e o caminho do arquivo no servidor,
    que está aberto para o envio."""
    shared = share_sparse(self.shared_root, path, BIG_SIZE)
    sock = stalled_download(self, self.server.address, path, self.log.wait_for)
    self.assertTrue(is_open_here(shared))
    return sock, shared

  def assertNoThreadLeft(self):
    self.assertEqual(set(threading.enumerate()) - self.threads_before, set())

  def shutdown_without_waiting(self):
    """Encerra o servidor numa thread à parte. O encerramento precisa
    terminar muito antes do prazo de envio de 60 s, porque um envio
    bloqueado não pode ser esperado, e, quando termina, as threads do
    servidor já finalizaram."""
    left = []
    def stop():
      self.server.shutdown()
      started = set(threading.enumerate()) - self.threads_before - {threading.current_thread()}
      left.extend(thread for thread in started if thread.is_alive())
    stopping = threading.Thread(target=stop, daemon=True)
    stopping.start()
    stopping.join(5)
    self.assertFalse(stopping.is_alive(), "o encerramento esperou as transferências")
    self.assertEqual(left, [], "threads do servidor ainda rodavam quando o encerramento terminou")

  def test_shutdown_with_active_downloads_interrupts_them_without_waiting(self):
    """Dois participantes com downloads presos no meio e um ocioso."""
    self.start_server()
    downloads = [self.stalled_download(f"grande {number}.bin") for number in (1, 2)]
    idle = self.connect()
    address = self.server.address

    self.shutdown_without_waiting()

    for sock, shared in downloads:
      self.assertFalse(is_open_here(shared))
      self.assertIn("servidor encerrado", self.log.wait_for(f"Desconectado: {address_of(sock)}"))
      read_until_closed(sock)
    self.assertIn("servidor encerrado", self.log.wait_for(f"Desconectado: {address_of(idle)}"))
    read_until_closed(idle)
    self.assertNoThreadLeft()
    with self.assertRaises(ConnectionRefusedError):
      socket.create_connection(address, timeout=1).close()

  def test_quit_and_a_reset_during_a_download_clean_up_once_and_free_the_session(self):
    """O QUIT chega à thread de atendimento enquanto a escritora, bloqueada
    no meio do arquivo, falha com o reset; um chat de outro participante é
    encaminhado ao mesmo tempo. As sessões encerradas não ocupam vagas nem
    recebem encaminhamentos: depois delas, nove participantes novos cabem
    no limite de dez e recebem o chat."""
    self.start_server()
    other = self.connect()
    for attempt in range(5):
      with self.subTest(attempt=attempt):
        sock, shared = self.stalled_download(f"grande {attempt}.bin")
        name = address_of(sock)

        other.sendall(p.encode(p.CHAT, {"text": f"durante a saída {attempt}"}))
        sock.sendall(p.encode(p.QUIT, {}))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        sock.close()

        self.log.wait_for(f"Desconectado: {name}")
        self.assertTrue(wait_until(lambda: not is_open_here(shared)), "o servidor não liberou o arquivo")
        self.log.wait_for(f"durante a saída {attempt}")

    newcomers = [self.connect() for _ in range(9)]
    other.sendall(p.encode(p.CHAT, {"text": "todos aqui"}))
    for newcomer in newcomers:
      self.assertEqual(receive_within(newcomer).content["text"], "todos aqui")
    self.assertEqual(self.log.matching("Recusado:"), [])
    self.assertEqual(len(self.log.matching("Desconectado:")), 5)

  def test_shutdown_racing_with_quit_during_a_download_cleans_up_once(self):
    for attempt in range(3):
      with self.subTest(attempt=attempt):
        self.start_server()
        sock, shared = self.stalled_download("grande.bin")
        name = address_of(sock)
        barrier = threading.Barrier(2)

        def leave():
          barrier.wait()
          try:
            sock.sendall(p.encode(p.QUIT, {}))
          except OSError:
            pass
        leaving = threading.Thread(target=leave)
        leaving.start()
        barrier.wait()
        self.shutdown_without_waiting()
        leaving.join()

        self.assertEqual(len(self.log.matching(f"Desconectado: {name}")), 1)
        self.assertFalse(is_open_here(shared))
        read_until_closed(sock)
        self.assertNoThreadLeft()


@unittest.skipUnless(hasattr(signal, "SIGINT") and os.name == "posix", "Ctrl+C simulado com SIGINT")
class ServerProcessInterruptionTest(unittest.TestCase):
  """O servidor roda como no terminal, a partir de uma cópia do programa com
  um arquivo grande em files/; a entrada padrão é a do operador."""

  def start_server_process(self):
    base = tempfile.TemporaryDirectory()
    self.addCleanup(base.cleanup)
    program = install_programs(os.path.join(base.name, "servidor"))
    share_sparse(os.path.join(program, "files"), "grande.bin", BIG_SIZE)
    port = free_port()
    process = subprocess.Popen(
      [sys.executable, "-u", os.path.join(program, "server.py"), str(port)],
      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=base.name,
      encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    output = OutputReader(process.stdout)
    self.addCleanup(output.close)
    self.addCleanup(process.wait, 5)
    self.addCleanup(process.kill)
    self.addCleanup(process.stdin.close)
    output.wait_for(str(port))
    return process, output, port

  def test_eof_or_ctrl_c_with_an_active_download_ends_the_server_without_waiting_for_the_transfer(self):
    """Se o servidor esperasse a transferência, a sessão só terminaria pelo
    prazo de envio, e não pelo encerramento."""
    stops = {"EOF": lambda process: process.stdin.close(), "Ctrl+C": lambda process: process.send_signal(signal.SIGINT)}
    for stop_name, stop in stops.items():
      with self.subTest(stop_name):
        process, output, port = self.start_server_process()
        sock = stalled_download(self, ("127.0.0.1", port), "grande.bin", output.wait_for)
        name = address_of(sock)

        stop(process)

        self.assertEqual(process.wait(5), 0)
        output.wait_for(f"Desconectado: {name}")
        [line] = [line for line in output.text.splitlines() if f"Desconectado: {name}" in line]
        self.assertIn("servidor encerrado", line)
        read_until_closed(sock)
        with self.assertRaises(ConnectionRefusedError):
          socket.create_connection(("127.0.0.1", port), timeout=1).close()
        self.assertNotIn("Traceback", output.text)


if __name__ == "__main__":
  unittest.main()

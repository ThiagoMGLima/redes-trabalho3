"""Interrupções de um download ativo: Sair, perda de conexão, prazo e
encerramento do servidor, observados pela fronteira TCP."""
import hashlib
import os
import socket
import subprocess
import struct
import sys
import tempfile
import threading
import unittest

import protocol as p
from server import Server
from tests.support import LogRecorder, OutputReader, install_programs, receive_within, wait_until
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
    self.addCleanup(self.server.shutdown)
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


class ScriptedServerInterruptionTest(unittest.TestCase):
  """O cliente roda como no terminal, a partir de uma cópia do programa, e o
  teste faz o papel do servidor na conexão TCP local, decidindo o que é
  enviado e quando a conexão termina."""

  def setUp(self):
    base = tempfile.TemporaryDirectory()
    self.addCleanup(base.cleanup)
    program = install_programs(os.path.join(base.name, "cliente"))
    self.downloads_root = os.path.join(program, "downloads")
    os.mkdir(self.downloads_root)

    listener = socket.create_server(("127.0.0.1", 0))
    self.addCleanup(listener.close)
    listener.settimeout(5)
    self.client = subprocess.Popen(
      [sys.executable, "-u", os.path.join(program, "client.py"), "127.0.0.1", str(listener.getsockname()[1])],
      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=base.name,
      encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    self.output = OutputReader(self.client.stdout)
    self.addCleanup(self.output.close)
    self.addCleanup(self.client.wait, 5)
    self.addCleanup(self.client.kill)
    self.addCleanup(self.client.stdin.close)
    self.server_side, _ = listener.accept()
    self.addCleanup(self.server_side.close)

  def type_line(self, line):
    self.client.stdin.write(line + "\n")
    self.client.stdin.flush()

  def downloaded(self, path):
    return os.path.join(self.downloads_root, *path.split("/"))

  def request(self, path):
    self.type_line("2")
    self.type_line(path)
    self.assertEqual(receive_within(self.server_side), (p.FILE_REQUEST, {"path": path}))

  def send(self, msg_type, content):
    self.server_side.sendall(p.encode(msg_type, content))

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

  def assertDisconnectionDiscardsOnlyTheActiveDownload(self, end_connection):
    """Um download é concluído e outro está em andamento quando a conexão
    termina: a desconexão aparece sem que se pressione Enter, o temporário
    some, nada mais é anunciado como concluído e o primeiro permanece."""
    self.request("pronto.txt")
    self.send(p.FILE_META, {"size": 6})
    self.send(p.FILE_DATA, b"pronto")
    self.send(p.FILE_END, {"sha256": hashlib.sha256(b"pronto").hexdigest()})
    self.output.wait_for(CONCLUDED)
    self.start_transfer("aulas/grande.bin")

    end_connection()

    self.output.wait_for("Desconectado do servidor")
    self.assertIn("Download de aulas/grande.bin interrompido", self.output.text)
    self.assertEqual(self.output.text.count(CONCLUDED), 1)
    self.assertEqual(files_under(self.downloads_root), ["pronto.txt"])
    with open(self.downloaded("pronto.txt"), "rb") as file:
      self.assertEqual(file.read(), b"pronto")
    self.assertExitsAfterEnter()

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


if __name__ == "__main__":
  unittest.main()

import hashlib
import os
import socket
import tempfile
import unittest

import protocol as p
from server import Server
from tests.support import LogRecorder, receive_within


class ServerDownloadTest(unittest.TestCase):
  """Pedidos de arquivo a um servidor real, cuja raiz compartilhada é um
  diretório temporário."""

  def setUp(self):
    shared = tempfile.TemporaryDirectory()
    self.addCleanup(shared.cleanup)
    self.shared_root = shared.name
    self.log = LogRecorder()
    self.server = Server("127.0.0.1", 0, log=self.log, shared_root=self.shared_root)
    self.server.start()
    self.addCleanup(self.server.shutdown)

  def connect(self):
    sock = socket.create_connection(self.server.address, timeout=5)
    self.addCleanup(sock.close)
    host, port = sock.getsockname()[:2]
    self.log.wait_for(f"Conectado: {host}:{port}")
    return sock

  def request(self, sock, path):
    sock.sendall(p.encode(p.FILE_REQUEST, {"path": path}))

  def share(self, path, content):
    """Coloca um arquivo na raiz compartilhada e devolve seu SHA-256."""
    full = os.path.join(self.shared_root, *path.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as file:
      file.write(content)
    return hashlib.sha256(content).hexdigest()

  def receive_download(self, sock):
    """Recebe FILE_META, os blocos e FILE_END de um pedido; devolve o tamanho
    anunciado, os blocos e o hash anunciado."""
    meta = receive_within(sock)
    self.assertEqual(meta.type, p.FILE_META, meta)
    blocks = []
    while (message := receive_within(sock)).type == p.FILE_DATA:
      blocks.append(message.content)
    self.assertEqual(message.type, p.FILE_END, message)
    return meta.content["size"], blocks, message.content["sha256"]

  def test_missing_file_gets_not_found_and_the_session_keeps_accepting_operations(self):
    client = self.connect()
    other = self.connect()

    self.request(client, "ausente.txt")

    error = receive_within(client)
    self.assertEqual(error.type, p.ERROR)
    self.assertEqual((error.content["operation"], error.content["code"]), ("FILE_REQUEST", "NOT_FOUND"))
    self.assertTrue(error.content["message"])
    client.sendall(p.encode(p.CHAT, {"text": "ainda aqui"}))
    self.assertEqual(receive_within(other), (p.CHAT, {"origin": "Cliente 1", "text": "ainda aqui"}))
    self.assertEqual(self.log.matching("Desconectado:"), [])

  def test_file_is_sent_as_meta_8_kib_blocks_and_end_with_its_sha256(self):
    content = os.urandom(2 * 8192 + 3616)
    sha256 = self.share("dados.bin", content)
    client = self.connect()

    self.request(client, "dados.bin")

    size, blocks, announced = self.receive_download(client)
    self.assertEqual(size, 20000)
    self.assertEqual([len(block) for block in blocks], [8192, 8192, 3616])
    self.assertEqual(b"".join(blocks), content)
    self.assertEqual(announced, sha256)


if __name__ == "__main__":
  unittest.main()

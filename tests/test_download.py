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


if __name__ == "__main__":
  unittest.main()

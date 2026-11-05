"""Limites de clientes e filas do servidor, observados pela fronteira TCP."""
import socket
import unittest

import protocol as p
from tests.support import receive_within
from tests.test_server import ServerTestCase, address_of


class ClientLimitTest(ServerTestCase):
  def connect_ten(self, server, log):
    return [self.connect(server, log) for _ in range(10)]

  def assertChatReachesTheOthers(self, sender, others, text):
    sender.sendall(p.encode(p.CHAT, {"text": text}))
    for other in others:
      message = receive_within(other)
      self.assertEqual((message.type, message.content["text"]), (p.CHAT, text))

  def test_eleventh_connection_is_closed_at_once_and_logged_while_the_ten_keep_working(self):
    server, log = self.start_server()
    clients = self.connect_ten(server, log)

    extra = socket.create_connection(server.address, timeout=5)
    self.addCleanup(extra.close)

    self.assertClosedByServer(extra)
    self.assertIn("10", log.wait_for(f"Recusado: {address_of(extra)}"))
    self.assertEqual(log.matching(f"Conectado: {address_of(extra)}"), [])
    self.assertChatReachesTheOthers(clients[0], clients[1:], "os dez continuam")
    self.assertEqual(log.matching("Desconectado:"), [])


if __name__ == "__main__":
  unittest.main()

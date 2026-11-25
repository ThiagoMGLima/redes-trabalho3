"""Limite de conexões simultâneas do servidor, observado pela fronteira TCP."""
import socket
import unittest

from tests.test_server import ServerTestCase, address_of


class ConnectionLimitTest(ServerTestCase):
  def connect_ten(self, server, log):
    return [self.connect(server, log) for _ in range(10)]

  def test_eleventh_connection_is_closed_at_once_and_logged_while_the_ten_stay_open(self):
    server, log = self.start_server()
    clients = self.connect_ten(server, log)

    extra = socket.create_connection(server.address, timeout=5)
    self.addCleanup(extra.close)

    self.assertClosedByServer(extra)
    self.assertIn("10", log.wait_for(f"Recusado: {address_of(extra)}"))
    self.assertEqual(log.matching(f"Conectado: {address_of(extra)}"), [])
    for client in clients:
      self.assertStillOpen(client)
    self.assertEqual(log.matching("Desconectado:"), [])

  def test_a_new_connection_is_accepted_again_after_a_client_leaves(self):
    server, log = self.start_server()
    clients = self.connect_ten(server, log)
    refused = socket.create_connection(server.address, timeout=5)
    self.addCleanup(refused.close)
    self.assertClosedByServer(refused)
    log.wait_for(f"Recusado: {address_of(refused)}")

    leaving = clients.pop()
    address = address_of(leaving)
    leaving.close()
    log.wait_for(f"Desconectado: {address}")
    newcomer = self.connect(server, log)

    self.assertEqual(log.matching(f"Recusado: {address_of(newcomer)}"), [])
    self.assertStillOpen(newcomer)


if __name__ == "__main__":
  unittest.main()

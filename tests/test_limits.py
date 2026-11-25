"""Limite de conexões simultâneas do servidor, observado pela fronteira TCP."""
import socket
import time
import unittest

from server import MAX_CONNECTIONS
from tests.test_http import Response
from tests.test_server import ServerTestCase, address_of


class ConnectionLimitTest(ServerTestCase):
  def connect_all(self, server, log):
    return [self.connect(server, log) for _ in range(MAX_CONNECTIONS)]

  def test_limit_is_64_simultaneous_connections(self):
    self.assertEqual(MAX_CONNECTIONS, 64)

  def test_connection_above_the_limit_gets_503_with_an_error_page_and_the_others_stay_open(self):
    server, log = self.start_server()
    clients = self.connect_all(server, log)

    extra = socket.create_connection(server.address, timeout=5)
    self.addCleanup(extra.close)
    extra.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
    time.sleep(0.3)
    data = bytearray()
    while chunk := extra.recv(65536):
      data += chunk
    response = Response(bytes(data))

    self.assertEqual(response.status_line, "HTTP/1.1 503 Service Unavailable")
    self.assertEqual(response.headers["content-length"], str(len(response.body)))
    self.assertIn("Servidor ocupado", response.body.decode("utf-8"))
    self.assertIn(str(MAX_CONNECTIONS), log.wait_for(f"Recusado: {address_of(extra)}"))
    self.assertEqual(log.matching(f"Conectado: {address_of(extra)}"), [])
    for client in clients:
      self.assertStillOpen(client)
    self.assertEqual(log.matching("Desconectado:"), [])

  def test_a_new_connection_is_accepted_again_after_a_client_leaves(self):
    server, log = self.start_server()
    clients = self.connect_all(server, log)
    refused = socket.create_connection(server.address, timeout=5)
    self.addCleanup(refused.close)
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

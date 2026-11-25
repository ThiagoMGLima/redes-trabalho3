"""Conexões simultâneas pela fronteira HTTP: um cliente lento não atrasa os
outros, e o servidor se mantém estável quando um cliente para de ler, sai no
meio da resposta ou quando é encerrado durante uma transferência."""
import os
import socket
import time
import unittest

from tests.support import PAGE, HttpTestCase, Response, ThreadErrors, put, shut_down_within

LARGE = 16 * 1024 * 1024
SHORT_DEADLINE = 0.5


class ConcurrencyTestCase(HttpTestCase):
  def setUp(self):
    super().setUp()
    self.large = os.urandom(LARGE)
    put(self.root, "grande.png", self.large)
    put(self.root, "pagina.html", PAGE)
    self.thread_errors = ThreadErrors(self)

  def stalled_download(self, target="/grande.png"):
    """Pede `target` por um socket de buffer de recepção pequeno e lê só o
    começo da resposta: o servidor fica bloqueado no envio até o cliente
    voltar a ler."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self.addCleanup(sock.close)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    sock.settimeout(5)
    sock.connect(self.server.address)
    sock.sendall(f"GET {target} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode("ascii"))
    first = sock.recv(1024)
    self.assertTrue(first.startswith(b"HTTP/1.1 200 OK"), first)
    return sock, first


class SimultaneousConnectionsTest(ConcurrencyTestCase):
  def test_slow_download_does_not_delay_another_client_and_both_threads_run_at_once(self):
    self.start_server()
    slow, first = self.stalled_download()

    fast = self.get("/pagina.html")

    self.assertEqual(fast.body, PAGE)
    self.log.wait_for('"GET /pagina.html HTTP/1.1" 200')
    self.assertEqual(self.log.matching('"GET /grande.png'), [], "o download lento terminou antes da hora")
    self.assertTrue(self.log.matching("Conectado") and len(self.log.matching("Conectado")) == 2)

    rest = self.read_until_closed(slow)
    self.assertEqual(Response(first + rest).body, self.large)
    slow_line = self.log.wait_for('"GET /grande.png HTTP/1.1" 200')
    fast_line = self.log.matching('"GET /pagina.html')[0]
    self.assertIn("conexão 1", slow_line)
    self.assertIn("conexão 2", fast_line)
    self.assertLess(self.log.lines.index(fast_line), self.log.lines.index(slow_line))
    self.assertEqual(self.thread_errors.errors, [])

  def test_several_slow_downloads_and_fast_requests_proceed_independently(self):
    self.start_server()
    slow = [self.stalled_download() for _ in range(3)]

    for _ in range(5):
      self.assertEqual(self.get("/pagina.html").body, PAGE)

    for sock, first in slow:
      self.assertEqual(Response(first + self.read_until_closed(sock)).body, self.large)


class SendDeadlineTest(ConcurrencyTestCase):
  def test_client_that_stops_reading_is_disconnected_by_the_send_deadline_alone(self):
    self.start_server(send_timeout=SHORT_DEADLINE)
    self.stalled_download()

    self.assertIn("prazo de envio expirado", self.log.wait_for("Desconectado", timeout=10))
    self.assertEqual(self.get("/pagina.html").body, PAGE)
    self.assertEqual(self.thread_errors.errors, [])

  def test_default_send_deadline_is_30_seconds(self):
    from server import SEND_TIMEOUT
    self.assertEqual(SEND_TIMEOUT, 30.0)


class ClientLeavingMidResponseTest(ConcurrencyTestCase):
  def test_client_closing_in_the_middle_of_the_response_ends_only_its_connection(self):
    self.start_server()
    other, first = self.stalled_download()
    leaving, _ = self.stalled_download()

    leaving.close()

    self.assertIn("conexão 2", self.log.wait_for("falha na conexão", timeout=10))
    self.assertEqual(self.get("/pagina.html").body, PAGE)
    self.assertEqual(Response(first + self.read_until_closed(other)).body, self.large)
    self.assertEqual(self.thread_errors.errors, [])


class ShutdownDuringTransferTest(ConcurrencyTestCase):
  def test_shutdown_with_a_stalled_transfer_ends_it_without_waiting(self):
    self.start_server()
    sock, _ = self.stalled_download()

    started = time.monotonic()
    shut_down_within(self.server, timeout=5)

    self.assertLess(time.monotonic() - started, 3)
    self.assertIn("servidor encerrado", self.log.wait_for("Desconectado"))
    self.assertEqual(self.thread_errors.errors, [])


if __name__ == "__main__":
  unittest.main()

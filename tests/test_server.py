import socket
import time
import unittest

import protocol as p
from server import Server
from tests.support import LogRecorder


def address_of(sock):
  host, port = sock.getsockname()[:2]
  return f"{host}:{port}"


class ServerTestCase(unittest.TestCase):
  def start_server(self, **options):
    log = LogRecorder()
    server = Server("127.0.0.1", 0, log=log, **options)
    server.start()
    self.addCleanup(server.shutdown)
    return server, log

  def connect(self, server, log):
    sock = socket.create_connection(server.address, timeout=5)
    self.addCleanup(sock.close)
    log.wait_for(f"Conectado: {address_of(sock)}")
    return sock

  def assertClosedByServer(self, sock):
    """O servidor fechou a conexão sem enviar nenhum byte (nem ERROR).

    Bytes que o servidor não chegou a ler podem transformar o fechamento em
    reset; nos dois casos nada foi enviado ao cliente."""
    try:
      data = sock.recv(1)
    except ConnectionResetError:
      data = b""
    self.assertEqual(data, b"")

  def assertStillOpen(self, sock):
    sock.settimeout(0.1)
    try:
      with self.assertRaises(socket.timeout):
        sock.recv(1)
    finally:
      sock.settimeout(5)


class SessionLifecycleTest(ServerTestCase):
  def test_quit_ends_the_session_and_both_events_are_logged(self):
    server, log = self.start_server()
    client = self.connect(server, log)

    client.sendall(p.encode(p.QUIT, {}))

    self.assertClosedByServer(client)
    line = log.wait_for(f"Desconectado: {address_of(client)}")
    self.assertIn("QUIT", line)

  def test_connections_and_disconnections_are_not_announced_to_other_clients(self):
    server, log = self.start_server()
    staying = self.connect(server, log)
    leaving = self.connect(server, log)

    leaving.sendall(p.encode(p.QUIT, {}))
    self.assertClosedByServer(leaving)
    log.wait_for(f"Desconectado: {address_of(leaving)}")
    self.connect(server, log)

    self.assertStillOpen(staying)
    self.assertEqual(len(log.matching(f"Conectado: {address_of(leaving)}")), 1)
    self.assertEqual(len(log.matching(f"Desconectado: {address_of(leaving)}")), 1)


class BrokenFrameTest(ServerTestCase):
  def assertOnlyThisSessionEnds(self, data, expected_reason):
    server, log = self.start_server()
    other = self.connect(server, log)
    affected = self.connect(server, log)

    affected.sendall(data)

    self.assertClosedByServer(affected)
    line = log.wait_for(f"Desconectado: {address_of(affected)}")
    self.assertIn(expected_reason, line)
    self.assertStillOpen(other)
    other.sendall(p.encode(p.QUIT, {}))
    self.assertClosedByServer(other)
    self.assertIn("QUIT", log.wait_for(f"Desconectado: {address_of(other)}"))

  def test_unknown_type_ends_only_the_affected_session(self):
    self.assertOnlyThisSessionEnds(bytes([200]) + (2).to_bytes(4, "big") + b"{}", "tipo desconhecido")

  def test_length_above_limit_ends_only_the_affected_session(self):
    self.assertOnlyThisSessionEnds(bytes([p.CHAT]) + (64 * 1024 + 1).to_bytes(4, "big"), "limite")

  def test_invalid_json_ends_only_the_affected_session(self):
    self.assertOnlyThisSessionEnds(bytes([p.CHAT]) + (5).to_bytes(4, "big") + b"{oops", "JSON inválido")

  def test_end_inside_header_or_content_is_logged_as_incomplete_frame(self):
    frame = p.encode(p.CHAT, {"text": "metade"})
    for cut in (2, len(frame) - 1):
      with self.subTest(cut=cut):
        server, log = self.start_server()
        client = self.connect(server, log)
        name = address_of(client)

        client.sendall(frame[:cut])
        client.shutdown(socket.SHUT_WR)

        self.assertClosedByServer(client)
        self.assertIn("frame incompleto", log.wait_for(f"Desconectado: {name}"))

  def test_end_between_frames_ends_the_session_without_incomplete_frame(self):
    server, log = self.start_server()
    client = self.connect(server, log)
    name = address_of(client)

    client.shutdown(socket.SHUT_WR)

    self.assertClosedByServer(client)
    self.assertNotIn("incompleto", log.wait_for(f"Desconectado: {name}"))


class DeadlineTest(ServerTestCase):
  TIMEOUT = 0.3

  def test_frame_not_finished_within_deadline_ends_only_the_affected_session(self):
    server, log = self.start_server(receive_timeout=self.TIMEOUT)
    other = self.connect(server, log)
    affected = self.connect(server, log)

    affected.sendall(p.encode(p.CHAT, {"text": "nunca termina"})[:7])
    start = time.monotonic()

    self.assertClosedByServer(affected)
    self.assertLess(time.monotonic() - start, self.TIMEOUT + 1)
    self.assertIn("prazo", log.wait_for(f"Desconectado: {address_of(affected)}"))
    self.assertStillOpen(other)
    other.sendall(p.encode(p.QUIT, {}))
    self.assertClosedByServer(other)

  def test_idle_session_without_frame_start_is_kept(self):
    server, log = self.start_server(receive_timeout=self.TIMEOUT)
    client = self.connect(server, log)

    time.sleep(self.TIMEOUT * 3)

    self.assertStillOpen(client)
    self.assertEqual(log.matching(f"Desconectado: {address_of(client)}"), [])
    client.sendall(p.encode(p.QUIT, {}))
    self.assertClosedByServer(client)


if __name__ == "__main__":
  unittest.main()

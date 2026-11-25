"""Conexões do servidor observadas pela fronteira TCP: aceitação, thread
dedicada por conexão, desconexão e encerramento."""
import os
import signal
import socket
import subprocess
import sys
import unittest

from server import Server
from tests.support import LogRecorder, OutputReader, free_port, shut_down_within

SERVER_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server.py")


def address_of(sock):
  host, port = sock.getsockname()[:2]
  return f"{host}:{port}"


class ServerTestCase(unittest.TestCase):
  def start_server(self, host="127.0.0.1", **options):
    log = LogRecorder()
    server = Server(host, 0, log=log, **options)
    server.start()
    self.addCleanup(shut_down_within, server)
    return server, log

  def connect(self, server, log, host="127.0.0.1"):
    sock = socket.create_connection((host, server.address[1]), timeout=5)
    self.addCleanup(sock.close)
    log.wait_for(f"Conectado: {address_of(sock)}")
    return sock

  def assertClosedByServer(self, sock):
    """O servidor fechou a conexão. Bytes que o servidor não chegou a ler
    podem transformar o fechamento em reset."""
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


class ConnectionTest(ServerTestCase):
  def test_server_listens_on_every_ipv4_interface_by_default(self):
    server, log = self.start_server(host="")

    self.assertEqual(server.address[0], "0.0.0.0")
    self.connect(server, log)

  def test_each_accepted_connection_is_served_by_its_own_dedicated_thread(self):
    server, log = self.start_server()
    first = self.connect(server, log)
    second = self.connect(server, log)

    first_line = log.wait_for(f"Conectado: {address_of(first)}")
    second_line = log.wait_for(f"Conectado: {address_of(second)}")
    self.assertIn("thread conexão 1", first_line)
    self.assertIn("thread conexão 2", second_line)
    self.assertStillOpen(first)
    self.assertStillOpen(second)

  def test_client_closing_its_connection_ends_only_that_connection(self):
    server, log = self.start_server()
    staying = self.connect(server, log)
    leaving = self.connect(server, log)
    address = address_of(leaving)

    leaving.close()

    self.assertIn("encerrada pelo cliente", log.wait_for(f"Desconectado: {address}"))
    self.assertStillOpen(staying)
    self.connect(server, log)


class ShutdownTest(ServerTestCase):
  def test_shutdown_stops_accepting_and_ends_existing_connections(self):
    server, log = self.start_server()
    first = self.connect(server, log)
    second = self.connect(server, log)
    address = server.address

    server.shutdown()

    self.assertClosedByServer(first)
    self.assertClosedByServer(second)
    for client in (first, second):
      self.assertIn("servidor encerrado", log.wait_for(f"Desconectado: {address_of(client)}"))
    with self.assertRaises(ConnectionRefusedError):
      socket.create_connection(address, timeout=1).close()


class ServerProcessTestCase(unittest.TestCase):
  """O servidor roda como no terminal."""

  def run_server_process(self, *args):
    process = subprocess.Popen(
      [sys.executable, "-u", SERVER_SCRIPT, *args],
      stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
      encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    output = OutputReader(process.stdout)
    self.addCleanup(output.close)
    self.addCleanup(process.wait, 5)
    self.addCleanup(process.kill)
    return process, output

  def start_server_process(self):
    port = free_port()
    process, output = self.run_server_process(str(port))
    output.wait_for(f"porta TCP {port}")
    return process, output, port

  def connect(self, port, output):
    client = socket.create_connection(("127.0.0.1", port), timeout=5)
    self.addCleanup(client.close)
    output.wait_for(f"Conectado: {address_of(client)}")
    return client


class StartupMessageTest(ServerProcessTestCase):
  def test_startup_shows_the_port_the_root_folder_and_the_addresses_to_open(self):
    process, output, port = self.start_server_process()

    output.wait_for("Ctrl+C encerra")
    text = output.text
    self.assertIn(f"porta TCP {port}", text)
    self.assertIn(f"Pasta raiz: {os.path.join(os.path.dirname(SERVER_SCRIPT), 'www')}", text)
    self.assertIn(f"http://localhost:{port}/", text)
    self.assertRegex(text, rf"Na rede: (http://\d+\.\d+\.\d+\.\d+:{port}/|sem endereço de rede encontrado)")
    process.send_signal(signal.SIGINT)
    self.assertEqual(process.wait(5), 0)


class PortTest(ServerProcessTestCase):
  def test_without_a_port_the_server_uses_8080(self):
    process, output = self.run_server_process()

    output.wait_for("porta TCP 8080")
    if process.poll() is None:
      process.send_signal(signal.SIGINT)
    process.wait(5)

  def test_port_outside_1025_to_65535_is_refused_with_a_clear_message(self):
    for port in ("80", "1024", "65536", "oito"):
      with self.subTest(port=port):
        process, output = self.run_server_process(port)

        self.assertNotEqual(process.wait(5), 0)
        output.close()
        self.assertIn("porta", output.text)
        self.assertNotIn("Traceback", output.text)

  def test_port_in_use_is_reported_and_the_program_ends(self):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
      busy.bind(("", 0))
      busy.listen()
      port = busy.getsockname()[1]
      process, output = self.run_server_process(str(port))

      self.assertEqual(process.wait(5), 1)
    output.close()
    self.assertIn(f"Não foi possível escutar na porta TCP {port}", output.text)


class CtrlCTest(ServerProcessTestCase):
  def test_ctrl_c_stops_accepting_and_ends_existing_connections(self):
    process, output, port = self.start_server_process()
    client = self.connect(port, output)

    process.send_signal(signal.SIGINT)

    self.assertEqual(process.wait(5), 0)
    try:
      self.assertEqual(client.recv(1), b"")
    except ConnectionResetError:
      pass
    with self.assertRaises(ConnectionRefusedError):
      socket.create_connection(("127.0.0.1", port), timeout=1).close()
    output.wait_for(f"Desconectado: {address_of(client)}")
    output.wait_for("Servidor encerrado")
    self.assertNotIn("Traceback", output.text)


if __name__ == "__main__":
  unittest.main()

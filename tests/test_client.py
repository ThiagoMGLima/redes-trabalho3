import os
import subprocess
import sys
import unittest

from server import Server
from tests.support import LogRecorder, OutputReader, wait_until

CLIENT_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "client.py")
PROMPT = "Escolha uma opção"


class ClientSessionTest(unittest.TestCase):
  """O cliente roda como no terminal, conectado a um servidor real local."""

  def setUp(self):
    self.log = LogRecorder()
    self.server = Server("127.0.0.1", 0, log=self.log)
    self.server.start()
    self.addCleanup(self.server.shutdown)
    self.client = subprocess.Popen(
      [sys.executable, "-u", CLIENT_SCRIPT, "127.0.0.1", str(self.server.address[1])],
      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    self.output = OutputReader(self.client.stdout)
    self.addCleanup(self.output.close)
    self.addCleanup(self.client.wait, 5)
    self.addCleanup(self.client.kill)
    self.addCleanup(self.client.stdin.close)

  def type_line(self, line):
    self.client.stdin.write(line + "\n")
    self.client.stdin.flush()

  def wait_for_prompts(self, count):
    if not wait_until(lambda: self.output.text.count(PROMPT) >= count, 5):
      raise AssertionError(f"menu não exibido {count} vez(es): {self.output.text!r}")

  def test_menu_offers_sair_arquivo_and_chat_and_sair_sends_quit(self):
    self.wait_for_prompts(1)
    for option in ("Sair", "Arquivo", "Chat"):
      self.assertIn(option, self.output.text)

    self.type_line("1")

    self.assertEqual(self.client.wait(5), 0)
    self.assertIn("QUIT", self.log.wait_for("Desconectado:"))

  def test_arquivo_and_chat_return_to_the_menu_without_ending_the_session(self):
    self.wait_for_prompts(1)

    self.type_line("2")
    self.wait_for_prompts(2)
    self.type_line("3")
    self.wait_for_prompts(3)

    self.assertEqual(self.log.matching("Desconectado:"), [])
    self.type_line("1")
    self.assertEqual(self.client.wait(5), 0)
    self.assertIn("QUIT", self.log.wait_for("Desconectado:"))

  def test_client_reports_disconnection_when_the_server_ends_the_session(self):
    self.wait_for_prompts(1)
    self.log.wait_for("Conectado:")

    self.server.shutdown()

    self.output.wait_for("Desconectado do servidor")
    self.client.stdin.close()
    self.assertEqual(self.client.wait(5), 0)
    self.assertNotIn("Traceback", self.output.text)


if __name__ == "__main__":
  unittest.main()

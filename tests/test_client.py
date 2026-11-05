import os
import socket
import subprocess
import sys
import unittest

import protocol as p
from server import Server
from tests.support import LogRecorder, OutputReader, receive_within, wait_until

CLIENT_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "client.py")
PROMPT = "Escolha uma opção"
CHAT_PROMPT = "Mensagem"
FILE_PROMPT = "Caminho do arquivo"


class ClientSessionTest(unittest.TestCase):
  """O cliente roda como no terminal, conectado a um servidor real local."""

  def setUp(self):
    self.log = LogRecorder()
    self.server = Server("127.0.0.1", 0, log=self.log)
    self.server.start()
    self.addCleanup(self.server.shutdown)
    self.client = subprocess.Popen(
      [sys.executable, "-u", CLIENT_SCRIPT, "127.0.0.1", str(self.server.address[1])],
      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
      encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
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

  def connect_peer(self):
    """Outro participante, conectado diretamente por TCP depois do cliente;
    o cliente é, portanto, o Cliente 1."""
    self.log.wait_for("Conectado:")
    peer = socket.create_connection(self.server.address, timeout=5)
    self.addCleanup(peer.close)
    self.log.wait_for(f"Conectado: 127.0.0.1:{peer.getsockname()[1]}")
    return peer

  def test_menu_offers_sair_arquivo_and_chat_and_sair_sends_quit(self):
    self.wait_for_prompts(1)
    for option in ("Sair", "Arquivo", "Chat"):
      self.assertIn(option, self.output.text)

    self.type_line("1")

    self.assertEqual(self.client.wait(5), 0)
    self.assertIn("QUIT", self.log.wait_for("Desconectado:"))

  def test_arquivo_asks_for_a_path_and_returns_to_the_menu_without_ending_the_session(self):
    self.wait_for_prompts(1)

    self.type_line("2")
    self.output.wait_for(FILE_PROMPT)
    self.type_line("ausente.txt")
    self.wait_for_prompts(2)

    self.assertEqual(self.log.matching("Desconectado:"), [])
    self.type_line("1")
    self.assertEqual(self.client.wait(5), 0)
    self.assertIn("QUIT", self.log.wait_for("Desconectado:"))

  def test_chat_sends_one_message_confirms_it_as_voce_and_returns_to_the_menu(self):
    self.wait_for_prompts(1)
    peer = self.connect_peer()

    self.type_line("3")
    self.output.wait_for(CHAT_PROMPT)
    self.type_line("  olá, turma 😀 ")

    self.assertEqual(receive_within(peer), (p.CHAT, {"origin": "Cliente 1", "text": "  olá, turma 😀 "}))
    self.log.wait_for("[Cliente 1]   olá, turma 😀 ")
    self.output.wait_for("[Você]   olá, turma 😀 ")
    self.wait_for_prompts(2)
    self.assertEqual(self.log.matching("Desconectado:"), [])

  def test_text_outside_the_rules_is_refused_locally_without_sending(self):
    self.wait_for_prompts(1)
    peer = self.connect_peer()
    refused = ["", "   ", "com\ttabulação", "escape \x1b[31m", "😀" * 256 + "a", "a" * 1023 + "é"]

    for text in refused + ["😀" * 256]:
      self.type_line("3")
      self.type_line(text)

    self.assertEqual(receive_within(peer), (p.CHAT, {"origin": "Cliente 1", "text": "😀" * 256}))
    self.wait_for_prompts(len(refused) + 2)
    self.assertEqual(self.output.text.count("Mensagem recusada"), len(refused))
    self.assertEqual(self.output.text.count("[Você]"), 1)

  def test_received_messages_appear_on_a_new_line_while_waiting_for_the_keyboard(self):
    self.wait_for_prompts(1)
    peer = self.connect_peer()

    peer.sendall(p.encode(p.CHAT, {"text": "chegou no menu"}))
    self.output.wait_for("\n[Cliente 2] chegou no menu\n")

    self.type_line("3")
    self.output.wait_for(CHAT_PROMPT)
    self.client.stdin.write("parcial")
    self.client.stdin.flush()
    self.server.broadcast("aviso 😀")
    self.output.wait_for("\n[Servidor] aviso 😀\n")

    self.type_line("mente digitado")
    self.assertEqual(receive_within(peer).content["origin"], "Servidor")
    self.assertEqual(receive_within(peer), (p.CHAT, {"origin": "Cliente 1", "text": "parcialmente digitado"}))
    self.output.wait_for("[Você] parcialmente digitado")

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

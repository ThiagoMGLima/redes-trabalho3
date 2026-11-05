"""Limites de clientes e filas do servidor, observados pela fronteira TCP."""
import select
import socket
import time
import unittest

import protocol as p
from server import Session
from tests.support import ThreadErrors, receive_within, tcp_pair, wait_until
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

  def test_a_new_connection_is_accepted_again_after_a_client_leaves(self):
    server, log = self.start_server()
    clients = self.connect_ten(server, log)
    refused = socket.create_connection(server.address, timeout=5)
    self.addCleanup(refused.close)
    self.assertClosedByServer(refused)
    log.wait_for(f"Recusado: {address_of(refused)}")

    leaving = clients.pop()
    leaving.sendall(p.encode(p.QUIT, {}))
    self.assertClosedByServer(leaving)
    log.wait_for(f"Desconectado: {address_of(leaving)}")
    newcomer = self.connect(server, log)

    self.assertChatReachesTheOthers(newcomer, clients, "entrei na vaga")
    self.assertChatReachesTheOthers(clients[0], clients[1:] + [newcomer], "bem-vindo")
    self.assertEqual(log.matching(f"Recusado: {address_of(newcomer)}"), [])


class OutgoingQueueTest(unittest.TestCase):
  """Fila de saída de uma sessão sobre uma conexão TCP local; `peer` é o
  destinatário, que não lê."""

  def setUp(self):
    thread_errors = ThreadErrors(self)
    self.addCleanup(lambda: self.assertEqual(thread_errors.errors, []))

  def open_session(self):
    near, peer = tcp_pair(small_buffers=True)
    self.addCleanup(peer.close)
    closes = []
    session = Session(near, "lento", send_timeout=30, receive_timeout=30,
                      on_close=lambda session, reason: closes.append(reason))
    session.start()
    self.addCleanup(session.join, 5)
    self.addCleanup(session.close, "fim do teste")
    return session, peer, closes

  def hold_the_writer(self, session, peer):
    """Um frame maior que os buffers começa a chegar e fica preso porque o
    destinatário não lê: a partir daí, tudo o que for enviado fica pendente."""
    session.send(p.FILE_DATA, b"\0" * (64 * 1024))
    readable, _, _ = select.select([peer], [], [], 5)
    self.assertTrue(readable, "o primeiro frame não começou a ser enviado")

  def test_queue_holds_64_pending_messages_and_one_more_disconnects_the_recipient(self):
    session, peer, closes = self.open_session()
    self.hold_the_writer(session, peer)

    for number in range(64):
      session.send(p.CHAT, {"origin": "Servidor", "text": str(number)})
    self.assertFalse(wait_until(lambda: closes, timeout=0.2))

    start = time.monotonic()
    session.send(p.CHAT, {"origin": "Servidor", "text": "65ª"})
    self.assertLess(time.monotonic() - start, 1)

    self.assertTrue(wait_until(lambda: closes, timeout=5))
    self.assertEqual(len(closes), 1)
    self.assertIn("fila", closes[0])
    self.assertIn("64", closes[0])
    session.join(5)
    self.assertFalse(session.is_alive())


if __name__ == "__main__":
  unittest.main()

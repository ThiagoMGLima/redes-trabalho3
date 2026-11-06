"""Limites de clientes e filas do servidor, observados pela fronteira TCP."""
import select
import socket
import time
import unittest

import protocol as p
from server import Session
from tests.support import ThreadErrors, finish_within, receive_within, tcp_pair, wait_until
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


@unittest.skipUnless(hasattr(socket, "TCP_MAXSEG"), "requer TCP_MAXSEG para limitar o buffer de envio do servidor")
class SlowRecipientTest(ServerTestCase):
  """Um destinatário que não lê o socket não segura o chat dos demais."""

  TEXT = "x" * 1000
  MAX_MESSAGES = 5000

  def connect_slow_reader(self, server, log):
    """Cliente que nunca lê. Buffer de recepção e segmentos pequenos fazem o
    envio do servidor para ele travar após poucas mensagens."""
    sock = socket.socket()
    self.addCleanup(sock.close)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_MAXSEG, 536)
    sock.settimeout(5)
    sock.connect(server.address)
    log.wait_for(f"Conectado: {address_of(sock)}")
    return sock

  def chat_until_disconnected(self, slow, sender, other, log, pause=0.0):
    """Envia chats um a um até o servidor desconectar `slow`; o outro
    destinatário precisa receber cada um antes do próximo envio. Devolve o
    maior tempo de entrega observado no outro destinatário."""
    slowest = 0.0
    for number in range(self.MAX_MESSAGES):
      text = f"{number} {self.TEXT}"
      start = time.monotonic()
      sender.sendall(p.encode(p.CHAT, {"text": text}))
      self.assertEqual(receive_within(other).content["text"], text)
      slowest = max(slowest, time.monotonic() - start)
      if log.matching(f"Desconectado: {address_of(slow)}"):
        return slowest
      time.sleep(pause)
    self.fail(f"o destinatário que não lê continuou conectado após {self.MAX_MESSAGES} mensagens")

  def assertSeesTheEnd(self, sock):
    try:
      while sock.recv(65536):
        pass
    except ConnectionResetError:
      pass

  def assertTheOthersKeepChatting(self, sender, other, log):
    sender.sendall(p.encode(p.CHAT, {"text": "ainda conversando"}))
    self.assertEqual(receive_within(other).content["text"], "ainda conversando")
    other.sendall(p.encode(p.CHAT, {"text": "também"}))
    self.assertEqual(receive_within(sender).content["text"], "também")
    self.assertEqual(len(log.matching("Desconectado:")), 1)

  def test_full_queue_disconnects_only_the_recipient_that_does_not_read(self):
    server, log = self.start_server(send_timeout=60)
    slow = self.connect_slow_reader(server, log)
    sender = self.connect(server, log)
    other = self.connect(server, log)

    self.chat_until_disconnected(slow, sender, other, log)

    self.assertIn("fila", log.wait_for(f"Desconectado: {address_of(slow)}"))
    self.assertSeesTheEnd(slow)
    self.assertTheOthersKeepChatting(sender, other, log)

  def test_blocked_send_expires_by_the_deadline_without_delaying_the_others(self):
    send_timeout = 0.8
    server, log = self.start_server(send_timeout=send_timeout)
    slow = self.connect_slow_reader(server, log)
    sender = self.connect(server, log)
    other = self.connect(server, log)

    # Com uma mensagem a cada 25 ms, menos de 64 aguardam durante o prazo:
    # quem desconecta é o prazo de envio, não a fila cheia.
    slowest = self.chat_until_disconnected(slow, sender, other, log, pause=0.025)

    self.assertIn("prazo de envio", log.wait_for(f"Desconectado: {address_of(slow)}"))
    self.assertLess(slowest, send_timeout / 2)
    self.assertSeesTheEnd(slow)
    self.assertTheOthersKeepChatting(sender, other, log)


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
    self.addCleanup(finish_within, session)
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

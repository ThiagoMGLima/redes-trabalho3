import gc
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import weakref

import protocol as p
from server import Server, Session
from tests.support import (
  LogRecorder, OutputReader, ThreadErrors, finish_within, free_port, receive_within, shut_down_within, tcp_pair,
  wait_until,
)

SERVER_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server.py")


def address_of(sock):
  host, port = sock.getsockname()[:2]
  return f"{host}:{port}"


class ServerTestCase(unittest.TestCase):
  def start_server(self, **options):
    log = LogRecorder()
    server = Server("127.0.0.1", 0, log=log, **options)
    server.start()
    self.addCleanup(shut_down_within, server)
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


class ShutdownTest(ServerTestCase):
  def test_shutdown_stops_accepting_and_ends_existing_sessions(self):
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
  """O servidor roda como no terminal; a entrada padrão é a do operador."""

  def start_server_process(self):
    port = free_port()
    process = subprocess.Popen(
      [sys.executable, "-u", SERVER_SCRIPT, str(port)],
      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
      encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    output = OutputReader(process.stdout)
    self.addCleanup(output.close)
    self.addCleanup(process.wait, 5)
    self.addCleanup(process.kill)
    self.addCleanup(process.stdin.close)
    output.wait_for(str(port))
    return process, output, port

  def connect(self, port, output):
    client = socket.create_connection(("127.0.0.1", port), timeout=5)
    self.addCleanup(client.close)
    output.wait_for(f"Conectado: {address_of(client)}")
    return client

  def type_line(self, process, line):
    process.stdin.write(line + "\n")
    process.stdin.flush()


class OperatorBroadcastTest(ServerProcessTestCase):
  def test_operator_message_reaches_every_connected_client_with_origin_servidor(self):
    process, output, port = self.start_server_process()
    first = self.connect(port, output)
    second = self.connect(port, output)

    self.type_line(process, "  Aviso: ação às 10h 😀 ")

    expected = (p.CHAT, {"origin": "Servidor", "text": "  Aviso: ação às 10h 😀 "})
    self.assertEqual(receive_within(first), expected)
    self.assertEqual(receive_within(second), expected)

  def test_operator_text_outside_the_rules_is_refused_locally_and_not_sent(self):
    process, output, port = self.start_server_process()
    client = self.connect(port, output)
    refused = ["", "   ", "com\ttabulação", "escape \x1b[31m", "😀" * 256 + "a"]

    for text in refused:
      self.type_line(process, text)
    self.type_line(process, "😀" * 256)

    self.assertEqual(receive_within(client), (p.CHAT, {"origin": "Servidor", "text": "😀" * 256}))
    if not wait_until(lambda: output.text.count("Mensagem recusada") == len(refused)):
      raise AssertionError(f"recusas não exibidas ao operador: {output.text!r}")

  def test_operator_input_that_is_not_utf_8_is_refused_locally_and_the_server_keeps_running(self):
    """Bytes que não formam texto UTF-8 digitados pelo operador recebem a
    recusa local; o servidor e as sessões continuam."""
    process, output, port = self.start_server_process()
    client = self.connect(port, output)

    process.stdin.buffer.write(b"ol\xe1\n\xff\xfe\n")
    process.stdin.buffer.flush()
    self.type_line(process, "depois da recusa")

    self.assertEqual(receive_within(client), (p.CHAT, {"origin": "Servidor", "text": "depois da recusa"}))
    if not wait_until(lambda: output.text.count("Mensagem recusada") == 2):
      raise AssertionError(f"recusas não exibidas ao operador: {output.text!r}")
    self.assertIsNone(process.poll())
    self.assertNotIn("Traceback", output.text)


class OperatorEofTest(ServerProcessTestCase):
  def test_eof_on_operator_input_stops_accepting_and_ends_existing_sessions(self):
    process, output, port = self.start_server_process()
    client = self.connect(port, output)

    process.stdin.close()

    self.assertEqual(process.wait(5), 0)
    try:
      self.assertEqual(client.recv(1), b"")
    except ConnectionResetError:
      pass
    with self.assertRaises(ConnectionRefusedError):
      socket.create_connection(("127.0.0.1", port), timeout=1).close()
    output.wait_for("EOF na entrada do operador")
    output.wait_for(f"Desconectado: {address_of(client)}")
    self.assertNotIn("Traceback", output.text)


@unittest.skipUnless(hasattr(signal, "SIGINT") and os.name == "posix", "Ctrl+C simulado com SIGINT")
class CtrlCTest(ServerProcessTestCase):
  def test_ctrl_c_stops_accepting_and_ends_existing_sessions(self):
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
    self.assertNotIn("Traceback", output.text)


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


class ChatTest(ServerTestCase):
  def chat(self, sock, content):
    sock.sendall(p.encode(p.CHAT, content))

  def test_client_chat_reaches_the_server_terminal_and_the_other_clients_without_echo(self):
    server, log = self.start_server()
    sender = self.connect(server, log)
    first = self.connect(server, log)
    second = self.connect(server, log)

    self.chat(sender, {"text": "olá, turma"})

    expected = (p.CHAT, {"origin": "Cliente 1", "text": "olá, turma"})
    self.assertEqual(receive_within(first), expected)
    self.assertEqual(receive_within(second), expected)
    log.wait_for("[Cliente 1] olá, turma")
    self.assertStillOpen(sender)

  def test_each_connection_gets_a_new_identifier_never_reused_after_reconnection(self):
    server, log = self.start_server()
    first = self.connect(server, log)
    second = self.connect(server, log)

    self.chat(first, {"text": "primeiro"})
    self.chat(second, {"text": "segundo"})
    self.assertEqual(receive_within(second).content["origin"], "Cliente 1")
    self.assertEqual(receive_within(first).content["origin"], "Cliente 2")

    first.sendall(p.encode(p.QUIT, {}))
    self.assertClosedByServer(first)
    log.wait_for(f"Desconectado: {address_of(first)}")
    reconnected = self.connect(server, log)
    self.chat(reconnected, {"text": "voltei"})

    self.assertEqual(receive_within(second), (p.CHAT, {"origin": "Cliente 3", "text": "voltei"}))
    self.assertIn("Cliente 3", log.wait_for(f"Conectado: {address_of(reconnected)}"))

  def test_client_cannot_choose_the_displayed_origin(self):
    server, log = self.start_server()
    sender = self.connect(server, log)
    other = self.connect(server, log)

    self.chat(sender, {"origin": "Servidor", "text": "sou o servidor"})

    self.assertEqual(receive_within(other), (p.CHAT, {"origin": "Cliente 1", "text": "sou o servidor"}))
    log.wait_for("[Cliente 1] sou o servidor")
    self.assertEqual(log.matching("[Servidor]"), [])

  def test_text_from_1_to_1024_utf8_bytes_is_forwarded_unchanged(self):
    server, log = self.start_server()
    sender = self.connect(server, log)
    other = self.connect(server, log)
    accepted = ["a", "😀" * 256, "é" * 512, "  ação  com   espaços 👍🏽 ", "👨‍👩‍👧 família"]

    for text in accepted:
      self.chat(sender, {"text": text})

    self.assertEqual([receive_within(other).content["text"] for _ in accepted], accepted)
    self.assertStillOpen(sender)

  def assertRefusedKeepingTheSession(self, frames, code):
    """Cada frame recebe ERROR com `code`; nada é encaminhado e a sessão
    continua: a próxima mensagem válida é a primeira que o outro recebe."""
    server, log = self.start_server()
    sender = self.connect(server, log)
    other = self.connect(server, log)

    for frame in frames:
      sender.sendall(frame)
      error = receive_within(sender)
      self.assertEqual(error.type, p.ERROR)
      self.assertEqual((error.content["operation"], error.content["code"]), ("CHAT", code))
      self.assertTrue(error.content["message"])
    self.chat(sender, {"text": "válida"})

    self.assertEqual(receive_within(other), (p.CHAT, {"origin": "Cliente 1", "text": "válida"}))
    self.assertEqual(log.matching("Desconectado:"), [])

  def test_refused_text_gets_invalid_chat_and_the_session_is_kept(self):
    refused = [
      "",
      "   ",
      "com\ttabulação",
      "linha 1\nlinha 2",
      "linha 1 linha 2",
      "escape \x1b[31m",
      "😀" * 256 + "a",
      "a" * 1023 + "é",
    ]
    self.assertRefusedKeepingTheSession([p.encode(p.CHAT, {"text": text}) for text in refused], "INVALID_CHAT")

  def test_valid_json_with_missing_or_invalid_fields_gets_invalid_request(self):
    contents = [{}, {"mensagem": "oi"}, {"text": None}, {"text": 42}, {"text": ["oi"]}, ["oi"], "oi", 7, None]
    self.assertRefusedKeepingTheSession([p.encode(p.CHAT, content) for content in contents], "INVALID_REQUEST")

  def test_text_that_cannot_be_encoded_as_utf8_gets_invalid_chat(self):
    payload = b'{"text": "\\ud800"}'
    self.assertRefusedKeepingTheSession([bytes([p.CHAT]) + len(payload).to_bytes(4, "big") + payload], "INVALID_CHAT")


class ControlMessageTest(ServerTestCase):
  def test_quit_that_is_not_an_empty_object_gets_invalid_request_and_the_session_is_kept(self):
    """QUIT exige um objeto vazio; JSON válido com outro conteúdo é uma
    recusa normal, e um QUIT vazio em seguida encerra a sessão."""
    server, log = self.start_server()
    client = self.connect(server, log)

    for content in [{"motivo": "tchau"}, [], "sair", None, 0]:
      client.sendall(p.encode(p.QUIT, content))
      error = receive_within(client)
      self.assertEqual(error.type, p.ERROR, content)
      self.assertEqual((error.content["operation"], error.content["code"]), ("QUIT", p.INVALID_REQUEST))
      self.assertTrue(error.content["message"])
    self.assertEqual(log.matching("Desconectado:"), [])

    client.sendall(p.encode(p.QUIT, {}))
    self.assertClosedByServer(client)
    self.assertIn("QUIT", log.wait_for(f"Desconectado: {address_of(client)}"))

  def test_messages_that_only_the_server_sends_are_ignored_and_logged_as_such(self):
    """FILE_META, FILE_DATA, FILE_END e ERROR vindos de um cliente são
    ignorados: o registro diz isso, nada é respondido e a sessão continua."""
    server, log = self.start_server()
    sender = self.connect(server, log)
    other = self.connect(server, log)
    sent = {
      "FILE_META": {"size": 3}, "FILE_DATA": b"abc", "FILE_END": {"sha256": "0" * 64},
      "ERROR": {"operation": "CHAT", "code": p.INVALID_CHAT, "message": "x"},
    }

    for name, content in sent.items():
      sender.sendall(p.encode(getattr(p, name), content))
    sender.sendall(p.encode(p.CHAT, {"text": "depois"}))

    self.assertEqual(receive_within(other), (p.CHAT, {"origin": "Cliente 1", "text": "depois"}))
    for name in sent:
      self.assertIn("só o servidor envia", log.wait_for(f"{name} de Cliente 1 ignorado"))
    self.assertEqual(log.matching("ainda não suportado"), [])
    self.assertStillOpen(sender)


class Content(dict):
  """Conteúdo de controle que pode ser observado por referência fraca."""


class SessionTest(unittest.TestCase):
  """Uma sessão do servidor sobre uma conexão TCP local; `peer` faz o papel
  do cliente."""

  TIMEOUT = 0.3

  def setUp(self):
    self.thread_errors = ThreadErrors(self)
    self.expected_thread_errors = []
    self.addCleanup(lambda: self.assertEqual(self.thread_errors.errors, self.expected_thread_errors))

  def open_session(self, small_buffers=False, **options):
    near, peer = tcp_pair(small_buffers)
    self.addCleanup(peer.close)
    closes = []
    session = Session(
      near,
      "teste",
      send_timeout=options.pop("send_timeout", self.TIMEOUT),
      receive_timeout=options.pop("receive_timeout", self.TIMEOUT),
      on_message=options.pop("on_message", None),
      on_close=lambda session, reason: closes.append(reason),
    )
    session.start()
    self.addCleanup(finish_within, session)
    self.addCleanup(session.close, "fim do teste")
    return session, peer, closes

  def assertFinished(self, session):
    session.join(5)
    self.assertFalse(session.is_alive())

  def assertPeerSeesEnd(self, peer):
    peer.settimeout(5)
    try:
      while peer.recv(65536):
        pass
    except ConnectionResetError:
      pass

  def test_messages_from_many_threads_are_written_whole_and_in_order(self):
    session, peer, _ = self.open_session(send_timeout=5)
    block = bytes(range(256)) * 32
    rounds = 8  # 4 produtores x 8 x 2 = 64 mensagens: cabem na fila mesmo sem leitura

    def produce(origin):
      for number in range(rounds):
        session.send(p.CHAT, {"origin": origin, "text": str(number)})
        session.send(p.FILE_DATA, block)
    producers = [threading.Thread(target=produce, args=(f"Cliente {n}",)) for n in range(4)]
    for producer in producers:
      producer.start()
    for producer in producers:
      producer.join()

    received = [p.receive_message(peer, timeout=5) for _ in range(4 * rounds * 2)]
    for origin in (f"Cliente {n}" for n in range(4)):
      texts = [m.content["text"] for m in received if m.type == p.CHAT and m.content["origin"] == origin]
      self.assertEqual(texts, [str(number) for number in range(rounds)])
    self.assertTrue(all(m.content == block for m in received if m.type == p.FILE_DATA))

  def test_send_deadline_ends_only_this_session(self):
    blocked, blocked_peer, blocked_closes = self.open_session(small_buffers=True)
    other, other_peer, other_closes = self.open_session()

    start = time.monotonic()
    for _ in range(4):
      blocked.send(p.FILE_DATA, b"\0" * (64 * 1024))

    self.assertTrue(wait_until(lambda: blocked_closes, timeout=5))
    self.assertLess(time.monotonic() - start, self.TIMEOUT + 1)
    self.assertEqual(len(blocked_closes), 1)
    self.assertIn("prazo de envio", blocked_closes[0])
    self.assertFinished(blocked)
    other.send(p.CHAT, {"origin": "Servidor", "text": "ainda aqui"})
    self.assertEqual(p.receive_message(other_peer, timeout=5).content["text"], "ainda aqui")
    self.assertEqual(other_closes, [])

  def test_cleanup_triggered_by_both_threads_at_once_runs_once(self):
    for attempt in range(5):
      with self.subTest(attempt=attempt):
        session, peer, closes = self.open_session(small_buffers=True)

        peer.sendall(p.encode(p.CHAT, {"text": "incompleto"})[:3])
        for _ in range(4):
          session.send(p.FILE_DATA, b"\0" * (64 * 1024))

        self.assertFinished(session)
        self.assertEqual(len(closes), 1)
        self.assertIn("prazo", closes[0])
        self.assertPeerSeesEnd(peer)

  def test_concurrent_close_calls_release_once(self):
    session, peer, closes = self.open_session()
    barrier = threading.Barrier(8)

    def close():
      barrier.wait()
      session.close("encerramento concorrente")
    closers = [threading.Thread(target=close) for _ in range(8)]
    for closer in closers:
      closer.start()
    peer.close()
    for closer in closers:
      closer.join()

    self.assertFinished(session)
    self.assertEqual(len(closes), 1)

  def test_close_discards_pending_messages(self):
    session, peer, closes = self.open_session(small_buffers=True, send_timeout=5)
    for _ in range(4):
      session.send(p.FILE_DATA, b"\0" * (64 * 1024))
    pending = [Content(origin="Servidor", text=str(number)) for number in range(50)]
    released = [weakref.ref(content) for content in pending]
    for content in pending:
      session.send(p.CHAT, content)
    del pending, content

    start = time.monotonic()
    session.close("QUIT")
    self.assertFinished(session)
    self.assertLess(time.monotonic() - start, 1)

    gc.collect()
    self.assertEqual([ref for ref in released if ref() is not None], [])
    peer.settimeout(5)
    received = b""
    try:
      while chunk := peer.recv(65536):
        received += chunk
    except ConnectionResetError:
      pass
    self.assertNotIn(b"Servidor", received)
    self.assertEqual(closes, ["QUIT"])

  def file_to_send(self):
    """Arquivo aberto em modo binário, como o servidor o entrega a
    `send_file`."""
    file = tempfile.TemporaryFile()
    self.addCleanup(file.close)
    file.write(bytes(range(256)) * 64)
    file.seek(0)
    return file

  def test_close_releases_the_socket_and_a_file_still_waiting_in_the_queue(self):
    """O arquivo foi entregue à sessão, mas a escritora, bloqueada no meio de
    uma mensagem anterior, ainda não começou a enviá-lo."""
    session, peer, closes = self.open_session(small_buffers=True, send_timeout=5)
    for _ in range(4):
      session.send(p.FILE_DATA, b"\0" * (64 * 1024))
    waiting = self.file_to_send()
    session.send_file(waiting)

    session.close("QUIT")

    self.assertFinished(session)
    self.assertTrue(waiting.closed)
    self.assertEqual(session.sock.fileno(), -1)
    self.assertEqual(closes, ["QUIT"])

  def test_file_handed_over_after_close_is_closed_without_being_sent(self):
    """Um pedido atendido enquanto outra thread encerra a sessão: o arquivo
    aberto para ele é liberado mesmo assim."""
    session, peer, closes = self.open_session()
    session.close("QUIT")
    self.assertFinished(session)

    late = self.file_to_send()
    session.send_file(late)

    self.assertTrue(late.closed)
    self.assertPeerSeesEnd(peer)
    self.assertEqual(closes, ["QUIT"])

  def test_unexpected_failure_handling_a_message_still_runs_the_cleanup(self):
    failure = RuntimeError("falha inesperada no atendimento")
    def fail(session, message):
      raise failure
    session, peer, closes = self.open_session(on_message=fail)

    peer.sendall(p.encode(p.CHAT, {"text": "oi"}))

    self.assertFinished(session)
    self.assertEqual(len(closes), 1)
    self.assertPeerSeesEnd(peer)
    self.expected_thread_errors.append(failure)

  def test_unexpected_failure_writing_a_message_still_runs_the_cleanup(self):
    session, peer, closes = self.open_session()

    session.send(p.CHAT, {"text": object()})

    self.assertFinished(session)
    self.assertEqual(len(closes), 1)
    self.assertPeerSeesEnd(peer)
    self.assertEqual([type(error) for error in self.thread_errors.errors], [TypeError])
    self.expected_thread_errors = self.thread_errors.errors

  def test_messages_sent_after_close_are_ignored(self):
    session, peer, closes = self.open_session()
    session.close("QUIT")
    self.assertFinished(session)

    session.send(p.CHAT, {"origin": "Servidor", "text": "tarde demais"})

    self.assertPeerSeesEnd(peer)
    self.assertEqual(closes, ["QUIT"])


if __name__ == "__main__":
  unittest.main()

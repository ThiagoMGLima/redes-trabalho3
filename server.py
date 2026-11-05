import argparse
import queue
import select
import socket
import sys
import threading

import protocol as p

HOST = ""
PORT = 50007


def terminal_log(line):
  print(line, flush=True)


def parse_port(text):
  try:
    port = int(text)
  except ValueError:
    raise argparse.ArgumentTypeError("a porta deve ser um número")
  if not 1024 < port <= 65535:
    raise argparse.ArgumentTypeError("a porta deve estar entre 1025 e 65535")
  return port


class Session:
  """Atendimento de uma conexão aceita.

  A thread de atendimento é a única leitora do socket e a auxiliar escritora
  é a única que escreve nele; as demais threads entregam mensagens pela fila
  da sessão com `send`. `close` é idempotente e pode ser chamado por qualquer
  thread: a primeira chamada descarta a fila, interrompe as duas threads e
  notifica `on_close` uma única vez. O socket é fechado pela thread de
  atendimento depois que a escritora termina.
  """

  _STOP = object()

  def __init__(self, sock, name, *, send_timeout=p.SEND_TIMEOUT, receive_timeout=p.RECEIVE_TIMEOUT,
               on_message=None, on_close=None):
    self.sock = sock
    self.name = name
    self._send_timeout = send_timeout
    self._receive_timeout = receive_timeout
    self._on_message = on_message
    self._on_close = on_close
    self._lock = threading.Lock()
    self._closed = False
    self._outgoing = queue.Queue()
    self._reader = threading.Thread(target=self._read_loop, name=f"atendimento {name}")
    self._writer = threading.Thread(target=self._write_loop, name=f"escritora {name}")

  def start(self):
    self._writer.start()
    self._reader.start()

  def send(self, msg_type, content):
    with self._lock:
      if self._closed:
        return
      self._outgoing.put((msg_type, content))

  def close(self, reason):
    with self._lock:
      if self._closed:
        return
      self._closed = True
      self._discard_outgoing()
      self._outgoing.put(self._STOP)
    try:
      self.sock.shutdown(socket.SHUT_RDWR)
    except OSError:
      pass
    if self._on_close is not None:
      self._on_close(self, reason)

  def join(self, timeout=None):
    self._reader.join(timeout)

  def is_alive(self):
    return self._reader.is_alive() or self._writer.is_alive()

  def _discard_outgoing(self):
    while True:
      try:
        self._outgoing.get_nowait()
      except queue.Empty:
        return

  def _read_loop(self):
    reason = "falha inesperada no atendimento"
    try:
      reason = self._receive_until_end()
    finally:
      self.close(reason)
      self._writer.join()
      self.sock.close()

  def _receive_until_end(self):
    while True:
      try:
        message = p.receive_message(self.sock, self._receive_timeout)
      except p.IncompleteFrame:
        return "frame incompleto"
      except p.InvalidFrame as error:
        return f"frame inválido: {error}"
      except p.FrameTimeout:
        return "prazo de recepção expirado"
      except OSError as error:
        return f"falha na conexão: {error}"
      if message is None:
        return "conexão encerrada pelo cliente"
      if message.type == p.QUIT:
        return "QUIT"
      if self._on_message is not None:
        self._on_message(self, message)

  def _write_loop(self):
    reason = "falha inesperada no envio"
    try:
      reason = self._send_until_stopped()
    finally:
      self.close(reason)

  def _send_until_stopped(self):
    while True:
      item = self._outgoing.get()
      if item is self._STOP:
        return "sessão encerrada"
      try:
        p.send_message(self.sock, *item, timeout=self._send_timeout)
      except p.FrameTimeout:
        return "prazo de envio expirado"
      except OSError as error:
        return f"falha no envio: {error}"


class Server:
  def __init__(self, host=HOST, port=PORT, *, send_timeout=p.SEND_TIMEOUT, receive_timeout=p.RECEIVE_TIMEOUT,
               log=terminal_log):
    self._host = host
    self._port = port
    self._send_timeout = send_timeout
    self._receive_timeout = receive_timeout
    self._log = log
    self._lock = threading.Lock()
    self._sessions = set()
    self._stopping = False

  @property
  def address(self):
    return self._listener.getsockname()[:2]

  def start(self):
    self._listener = socket.create_server((self._host, self._port))
    self._wake_reader, self._wake_writer = socket.socketpair()
    self._acceptor = threading.Thread(target=self._accept_loop, name="aceitação")
    self._acceptor.start()

  def wait(self):
    """Bloqueia até o encerramento; acorda periodicamente para que Ctrl+C
    seja atendido em qualquer plataforma."""
    while self._acceptor.is_alive():
      self._acceptor.join(0.5)

  def shutdown(self):
    with self._lock:
      if self._stopping:
        return
      self._stopping = True
    self._wake_writer.send(b"\0")
    self._acceptor.join()
    self._listener.close()
    self._wake_reader.close()
    self._wake_writer.close()
    with self._lock:
      sessions = list(self._sessions)
    for session in sessions:
      session.close("servidor encerrado")
    for session in sessions:
      session.join()

  def _accept_loop(self):
    while True:
      readable, _, _ = select.select([self._listener, self._wake_reader], [], [])
      if self._wake_reader in readable:
        return
      try:
        sock, address = self._listener.accept()
      except OSError:
        continue
      self._open_session(sock, f"{address[0]}:{address[1]}")

  def _open_session(self, sock, name):
    session = Session(
      sock,
      name,
      send_timeout=self._send_timeout,
      receive_timeout=self._receive_timeout,
      on_message=self._handle_message,
      on_close=self._end_session,
    )
    with self._lock:
      self._sessions.add(session)
    self._log(f"Conectado: {name}")
    session.start()

  def _end_session(self, session, reason):
    with self._lock:
      self._sessions.discard(session)
    self._log(f"Desconectado: {session.name} ({reason})")

  def _handle_message(self, session, message):
    self._log(f"{p.TYPE_NAMES[message.type]} de {session.name} ignorado: ainda não suportado")


def main():
  parser = argparse.ArgumentParser(description="Servidor TCP de chat coletivo e transferência de arquivos")
  parser.add_argument("port", nargs="?", type=parse_port, default=PORT, help=f"porta TCP (padrão {PORT})")
  args = parser.parse_args()

  server = Server(HOST, args.port)
  try:
    server.start()
  except OSError as error:
    terminal_log(f"Não foi possível escutar na porta TCP {args.port}: {error}")
    sys.exit(1)
  terminal_log(f"Servidor escutando na porta TCP {server.address[1]}. Ctrl+C encerra.")
  try:
    server.wait()
  except KeyboardInterrupt:
    terminal_log("Ctrl+C recebido: encerrando o servidor")
  finally:
    server.shutdown()
  terminal_log("Servidor encerrado")


if __name__ == "__main__":
  main()

import argparse
import select
import socket
import threading

import protocol as p

HOST = ""
PORT = 50007


def terminal_log(line):
  print(line, flush=True)


class Session:
  """Atendimento de uma conexão aceita."""

  def __init__(self, sock, name, *, receive_timeout=p.RECEIVE_TIMEOUT, on_message=None, on_close=None):
    self.sock = sock
    self.name = name
    self._receive_timeout = receive_timeout
    self._on_message = on_message
    self._on_close = on_close
    self._lock = threading.Lock()
    self._closed = False
    self._reader = threading.Thread(target=self._read_loop, name=f"atendimento {name}")

  def start(self):
    self._reader.start()

  def close(self, reason):
    with self._lock:
      if self._closed:
        return
      self._closed = True
    try:
      self.sock.shutdown(socket.SHUT_RDWR)
    except OSError:
      pass
    if self._on_close is not None:
      self._on_close(self, reason)

  def join(self, timeout=None):
    self._reader.join(timeout)

  def _read_loop(self):
    try:
      self.close(self._receive_until_end())
    finally:
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


class Server:
  def __init__(self, host=HOST, port=PORT, *, receive_timeout=p.RECEIVE_TIMEOUT, log=terminal_log):
    self._host = host
    self._port = port
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

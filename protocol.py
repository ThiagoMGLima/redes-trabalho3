import json
import select
import struct
import time
from collections import namedtuple


QUIT = 1
FILE_REQUEST = 2
CHAT = 3
FILE_META = 4
FILE_DATA = 5
FILE_END = 6
ERROR = 7

TYPE_NAMES = {
  QUIT: "QUIT",
  FILE_REQUEST: "FILE_REQUEST",
  CHAT: "CHAT",
  FILE_META: "FILE_META",
  FILE_DATA: "FILE_DATA",
  FILE_END: "FILE_END",
  ERROR: "ERROR",
}

HEADER = struct.Struct("!BI")
MAX_CONTENT = 64 * 1024
CHUNK_SIZE = 8 * 1024

SEND_TIMEOUT = 5.0
RECEIVE_TIMEOUT = 10.0

Message = namedtuple("Message", "type content")


class ProtocolError(Exception):
  pass


class InvalidFrame(ProtocolError):
  pass


class IncompleteFrame(ProtocolError):
  pass


class FrameTimeout(ProtocolError):
  pass


def encode(msg_type, content):
  if msg_type not in TYPE_NAMES:
    raise ValueError(f"tipo desconhecido {msg_type}")
  if msg_type == FILE_DATA:
    payload = bytes(content)
  else:
    payload = json.dumps(content, ensure_ascii=False).encode("utf-8")
  if len(payload) > MAX_CONTENT:
    raise ValueError(f"conteúdo de {len(payload)} bytes excede o limite de {MAX_CONTENT}")
  return HEADER.pack(msg_type, len(payload)) + payload


def decode(msg_type, payload):
  if msg_type == FILE_DATA:
    return payload
  try:
    return json.loads(payload.decode("utf-8"))
  except (UnicodeDecodeError, ValueError, RecursionError) as error:
    raise InvalidFrame(f"JSON inválido em {TYPE_NAMES[msg_type]}: {error}") from None


def send_message(sock, msg_type, content, timeout=SEND_TIMEOUT):
  frame = memoryview(encode(msg_type, content))
  _make_nonblocking(sock)
  deadline = time.monotonic() + timeout
  while frame:
    _wait(sock, deadline, writing=True)
    try:
      sent = sock.send(frame)
    except BlockingIOError:
      continue
    frame = frame[sent:]


def receive_message(sock, timeout=RECEIVE_TIMEOUT):
  _make_nonblocking(sock)
  first = _recv_some(sock, HEADER.size, deadline=None)
  if not first:
    return None
  deadline = time.monotonic() + timeout
  header = first + _recv_exact(sock, HEADER.size - len(first), deadline)
  msg_type, length = HEADER.unpack(header)
  if msg_type not in TYPE_NAMES:
    raise InvalidFrame(f"tipo desconhecido {msg_type}")
  if length > MAX_CONTENT:
    raise InvalidFrame(f"comprimento {length} acima do limite de {MAX_CONTENT} bytes")
  payload = _recv_exact(sock, length, deadline)
  return Message(msg_type, decode(msg_type, payload))


def _make_nonblocking(sock):
  if sock.gettimeout() != 0.0:
    sock.setblocking(False)


def _wait(sock, deadline, writing=False):
  if deadline is None:
    remaining = None
  else:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
      raise FrameTimeout("prazo do frame expirado")
  watched = ([], [sock]) if writing else ([sock], [])
  readable, writable, _ = select.select(*watched, [], remaining)
  if not readable and not writable:
    raise FrameTimeout("prazo do frame expirado")


def _recv_some(sock, size, deadline):
  while True:
    _wait(sock, deadline)
    try:
      return sock.recv(size)
    except BlockingIOError:
      continue


def _recv_exact(sock, size, deadline):
  data = bytearray()
  while len(data) < size:
    chunk = _recv_some(sock, size - len(data), deadline)
    if not chunk:
      raise IncompleteFrame(f"stream terminou após {len(data)} de {size} bytes")
    data += chunk
  return bytes(data)

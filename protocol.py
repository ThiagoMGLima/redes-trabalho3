import json
import struct
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

RECEIVE_TIMEOUT = 10.0

Message = namedtuple("Message", "type content")


class ProtocolError(Exception):
  pass


class InvalidFrame(ProtocolError):
  pass


class IncompleteFrame(ProtocolError):
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


def receive_message(sock, timeout=RECEIVE_TIMEOUT):
  first = sock.recv(HEADER.size)
  if not first:
    return None
  header = first + _recv_exact(sock, HEADER.size - len(first))
  msg_type, length = HEADER.unpack(header)
  if msg_type not in TYPE_NAMES:
    raise InvalidFrame(f"tipo desconhecido {msg_type}")
  if length > MAX_CONTENT:
    raise InvalidFrame(f"comprimento {length} acima do limite de {MAX_CONTENT} bytes")
  payload = _recv_exact(sock, length)
  return Message(msg_type, decode(msg_type, payload))


def _recv_exact(sock, size):
  data = bytearray()
  while len(data) < size:
    chunk = sock.recv(size - len(data))
    if not chunk:
      raise IncompleteFrame(f"stream terminou após {len(data)} de {size} bytes")
    data += chunk
  return bytes(data)

"""Protocolo de aplicação sobre a conexão TCP persistente.

Cada mensagem é um frame com cabeçalho de 5 bytes (tipo em 1 byte e
comprimento do conteúdo em 4 bytes sem sinal, em ordem de rede) seguido do
conteúdo. Controles são objetos JSON em UTF-8; FILE_DATA leva os bytes
originais do arquivo. O comprimento conta bytes já codificados.
"""
import json
import os
import select
import struct
import time
import unicodedata
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

MAX_CHAT_BYTES = 1024

NOT_FOUND = "NOT_FOUND"
PATH_FORBIDDEN = "PATH_FORBIDDEN"
NOT_REGULAR = "NOT_REGULAR"
ACCESS_ERROR = "ACCESS_ERROR"
INVALID_CHAT = "INVALID_CHAT"
INVALID_REQUEST = "INVALID_REQUEST"
BUSY = "BUSY"

Message = namedtuple("Message", "type content")


class ProtocolError(Exception):
  pass


class InvalidFrame(ProtocolError):
  pass


class IncompleteFrame(ProtocolError):
  pass


class FrameTimeout(ProtocolError):
  pass


class InvalidChatText(ValueError):
  """Texto recusado pelas regras do chat coletivo; a mensagem traz o motivo."""


class ForbiddenPath(ValueError):
  """Caminho de arquivo com sintaxe proibida; a mensagem traz o motivo."""


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


def check_chat_text(text):
  """Aceita de 1 a MAX_CHAT_BYTES bytes de texto UTF-8 em uma única linha,
  preservado como veio. Levanta InvalidChatText para texto vazio, somente
  espaços, com caracteres de controle ou quebra de linha, acima do limite ou
  que não possa ser codificado em UTF-8; nunca trunca."""
  try:
    size = len(text.encode("utf-8"))
  except UnicodeEncodeError:
    raise InvalidChatText("o texto não pode ser codificado em UTF-8") from None
  if not text:
    raise InvalidChatText("a mensagem está vazia")
  if text.isspace():
    raise InvalidChatText("a mensagem contém somente espaços")
  if size > MAX_CHAT_BYTES:
    raise InvalidChatText(f"a mensagem tem {size} bytes; o limite é {MAX_CHAT_BYTES}")
  if any(unicodedata.category(char) in ("Cc", "Zl", "Zp") for char in text):
    raise InvalidChatText("a mensagem contém caracteres de controle ou quebra de linha")


def check_relative_path(path):
  """Aceita um caminho relativo à raiz, com `/` como separador. Levanta
  ForbiddenPath para caminho vazio ou absoluto, texto que não possa ser
  codificado em UTF-8, componentes vazios, `.` ou `..`, barra invertida,
  dois-pontos e caracteres de controle (inclusive NUL) ou de quebra de
  linha, como no chat. A verificação é feita sobre o texto recebido, antes
  de qualquer normalização, para que uma entrada proibida nunca vire outra
  aceita."""
  if not path:
    raise ForbiddenPath("o caminho está vazio")
  try:
    path.encode("utf-8")
  except UnicodeEncodeError:
    raise ForbiddenPath("o caminho não pode ser codificado em UTF-8") from None
  if path.startswith("/"):
    raise ForbiddenPath("caminhos absolutos não são permitidos")
  if "\\" in path or ":" in path:
    raise ForbiddenPath("o caminho não pode conter barra invertida nem dois-pontos")
  if any(unicodedata.category(char) in ("Cc", "Zl", "Zp") for char in path):
    raise ForbiddenPath("o caminho contém caracteres de controle ou quebra de linha")
  if any(part in ("", ".", "..") for part in path.split("/")):
    raise ForbiddenPath("o caminho contém componentes vazios, '.' ou '..'")


def check_inside(root, target):
  """Levanta ForbiddenPath se `target`, depois de resolvido, não for a
  própria raiz `root` resolvida nem estiver abaixo dela. A contenção é
  verificada pela relação entre diretórios, componente a componente: um
  prefixo textual semelhante, como `files-outro/` diante de `files/`, não
  prova contenção."""
  root = os.path.realpath(root)
  try:
    inside = os.path.commonpath([root, os.path.realpath(target)]) == root
  except ValueError:
    inside = False
  if not inside:
    raise ForbiddenPath("o caminho resolvido sai da raiz autorizada")


def send_message(sock, msg_type, content, timeout=SEND_TIMEOUT):
  """Envia o frame inteiro em até `timeout` segundos, prazo total que não é
  reiniciado por envios parciais; ao expirar, levanta FrameTimeout.

  O socket passa a operar em modo não bloqueante: os prazos são controlados
  aqui."""
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
  """Recebe exatamente um frame e devolve Message(tipo, conteúdo), ou None se
  o stream terminar entre frames.

  A espera pelo início do frame não tem prazo; depois do primeiro byte,
  cabeçalho e conteúdo devem chegar em até `timeout` segundos no total.
  Levanta InvalidFrame (tipo desconhecido, comprimento acima do limite, JSON
  inválido), IncompleteFrame (stream terminou no meio do frame) ou
  FrameTimeout. Nunca procura outro cabeçalho para recuperar o alinhamento.
  O socket passa a operar em modo não bloqueante."""
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

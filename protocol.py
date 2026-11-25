"""Protocolo HTTP do servidor: regras que não dependem do socket.

Interpreta a requisição recebida, monta o cabeçalho da resposta e aplica a
política de caminhos da pasta raiz, herdada do Trabalho 2, junto com a
porta da linha de comando.
"""
import argparse
import os
import time
import unicodedata
from collections import namedtuple


DEFAULT_PORT = 8080

# A requisição termina na primeira linha em branco: o CRLF que fecha a
# última linha de cabeçalho seguido de outro CRLF.
HEAD_END = b"\r\n\r\n"
LINE_END = "\r\n"

SERVER_NAME = "ServidorRedes/1.0"
INDEX = "index.html"

REASONS = {
  200: "OK",
}

# Content-Type pela extensão, sem diferenciar maiúsculas de minúsculas. Os
# tipos de texto declaram UTF-8 para que o navegador não precise adivinhar a
# codificação de páginas sem <meta charset>, como o modelo do enunciado.
CONTENT_TYPES = {
  "html": "text/html; charset=utf-8",
  "htm": "text/html; charset=utf-8",
  "css": "text/css; charset=utf-8",
  "js": "text/javascript; charset=utf-8",
  "txt": "text/plain; charset=utf-8",
  "json": "application/json",
  "png": "image/png",
  "jpg": "image/jpeg",
  "jpeg": "image/jpeg",
  "gif": "image/gif",
  "svg": "image/svg+xml",
  "ico": "image/x-icon",
  "webp": "image/webp",
  "pdf": "application/pdf",
}
UNKNOWN_TYPE = "application/octet-stream"

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

Request = namedtuple("Request", "method target version headers")

NOT_FOUND = "NOT_FOUND"
PATH_FORBIDDEN = "PATH_FORBIDDEN"
NOT_REGULAR = "NOT_REGULAR"
ACCESS_ERROR = "ACCESS_ERROR"

# Uma pasta é aberta sem seguir links, e o descritor obtido é o ponto de
# partida da operação seguinte; o caminho não é resolvido de novo pelo nome.
# O_PATH, onde existe, dispensa a permissão de leitura, como o exame por nome.
FOLDER_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_PATH", 0)


class ForbiddenPath(ValueError):
  """Caminho com sintaxe proibida; a mensagem traz o motivo."""


class BadRequest(ValueError):
  """Requisição que não segue a sintaxe do HTTP; a mensagem traz o motivo."""


def parse_request(head):
  """Interpreta os bytes da requisição até a linha em branco, exclusive.

  A primeira linha é a linha de requisição: método, alvo e versão separados
  por um espaço. As seguintes são cabeçalhos `Nome: valor`; o nome é
  guardado em minúsculas, porque não diferencia maiúsculas de minúsculas.
  Os bytes são lidos como ISO-8859-1, que aceita qualquer octeto, para que
  a sintaxe seja verificada sem falha de decodificação."""
  lines = head.decode("iso-8859-1").split(LINE_END)
  parts = lines[0].split(" ")
  if len(parts) != 3:
    raise BadRequest("a linha de requisição não tem método, alvo e versão")
  method, target, version = parts
  headers = {}
  for line in lines[1:]:
    name, separator, value = line.partition(":")
    if not separator:
      raise BadRequest(f"cabeçalho sem dois-pontos: {line!r}")
    headers[name.lower()] = value.strip(" \t")
  return Request(method, target, version, headers)


def resource_path(target):
  """Caminho do recurso na pasta raiz, relativo a ela, para o alvo da
  requisição: sem a barra inicial, e o índice para a raiz."""
  if target == "/":
    return INDEX
  return target.removeprefix("/")


def content_type(path):
  """Tipo do conteúdo pela extensão do último componente do caminho; sem
  extensão conhecida, conteúdo binário genérico."""
  extension = os.path.splitext(path)[1].lower().removeprefix(".")
  return CONTENT_TYPES.get(extension, UNKNOWN_TYPE)


def http_date(moment=None):
  """Data no formato IMF-fixdate da RFC 9110, sempre em GMT, com nomes de
  dia e de mês em inglês, que não dependem do idioma do sistema."""
  t = time.gmtime(moment)
  return (f"{_WEEKDAYS[t.tm_wday]}, {t.tm_mday:02d} {_MONTHS[t.tm_mon - 1]} {t.tm_year} "
          f"{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d} GMT")


def response_head(status, content_type, length):
  """Linha de status, cabeçalhos e a linha em branco que os encerra, como
  texto ASCII já codificado em bytes. O corpo é enviado em seguida, sem
  passar por aqui."""
  lines = [
    f"HTTP/1.1 {status} {REASONS[status]}",
    f"Date: {http_date()}",
    f"Server: {SERVER_NAME}",
    f"Content-Type: {content_type}",
    f"Content-Length: {length}",
    "Connection: close",
    "Cache-Control: no-store",
  ]
  return (LINE_END.join(lines) + LINE_END + LINE_END).encode("ascii")


def parse_port(text):
  """Porta TCP da linha de comando, de 1025 a 65535."""
  try:
    port = int(text)
  except ValueError:
    raise argparse.ArgumentTypeError("a porta deve ser um número")
  if not 1024 < port <= 65535:
    raise argparse.ArgumentTypeError("a porta deve estar entre 1025 e 65535")
  return port


def _breaks_the_line(text):
  """Informa se `text` tem caractere de controle (inclusive tabulação,
  quebra de linha, Escape e NUL) ou separador de linha ou de parágrafo do
  Unicode."""
  return any(unicodedata.category(char) in ("Cc", "Zl", "Zp") for char in text)


def check_relative_path(path):
  """Aceita um caminho relativo à raiz, com `/` como separador. Levanta
  ForbiddenPath para caminho vazio ou absoluto, texto que não possa ser
  codificado em UTF-8, componentes vazios, `.` ou `..`, barra invertida,
  dois-pontos e caracteres de controle (inclusive NUL) ou de quebra de
  linha. A verificação é feita sobre o texto recebido, antes
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
  if _breaks_the_line(path):
    raise ForbiddenPath("o caminho contém caracteres de controle ou quebra de linha")
  if any(part in ("", ".", "..") for part in path.split("/")):
    raise ForbiddenPath("o caminho contém componentes vazios, '.' ou '..'")


def check_inside(root, target):
  """Levanta ForbiddenPath se `target`, depois de resolvido, não for a
  própria raiz `root` resolvida nem estiver abaixo dela. A contenção é
  verificada pela relação entre diretórios, componente a componente: um
  prefixo textual semelhante, como `www-outro/` diante de `www/`, não
  prova contenção."""
  root = os.path.realpath(root)
  try:
    inside = os.path.commonpath([root, os.path.realpath(target)]) == root
  except ValueError:
    inside = False
  if not inside:
    raise ForbiddenPath("o caminho resolvido sai da raiz autorizada")

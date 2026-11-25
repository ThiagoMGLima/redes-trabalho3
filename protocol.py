"""Protocolo de aplicação do servidor: regras que não dependem do socket.

Por ora, a porta da linha de comando e a política de caminhos da pasta raiz,
herdadas do Trabalho 2.
"""
import argparse
import os
import unicodedata


DEFAULT_PORT = 8080

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

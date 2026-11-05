import argparse
import hashlib
import os
import socket
import stat
import sys
import tempfile
import threading

import protocol as p

HOST = "127.0.0.1"
PORT = 50007
DOWNLOADS_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")

MENU = """
Menu:
  1 - Sair
  2 - Arquivo
  3 - Chat"""
PROMPT = "Escolha uma opção: "
CHAT_PROMPT = "Mensagem: "
FILE_PROMPT = "Caminho do arquivo: "
OPTIONS = {
  "1": "sair", "sair": "sair",
  "2": "arquivo", "arquivo": "arquivo",
  "3": "chat", "chat": "chat",
}


_output_lock = threading.Lock()


def show(text):
  """Escreve uma linha inteira no terminal; a principal e a receptora não
  intercalam suas linhas."""
  with _output_lock:
    print(text, flush=True)


def parse_port(text):
  try:
    port = int(text)
  except ValueError:
    raise argparse.ArgumentTypeError("a porta deve ser um número")
  if not 1024 < port <= 65535:
    raise argparse.ArgumentTypeError("a porta deve estar entre 1025 e 65535")
  return port


def check_destination(root, path):
  """Destino local de `path`, um caminho já aceito por
  p.check_relative_path, sob a raiz de downloads `root`. Levanta
  p.ForbiddenPath se a raiz, uma pasta existente do caminho ou o próprio
  destino for link simbólico; pastas que ainda não existem são aceitas."""
  parts = path.split("/")
  download_folder(root, parts[:-1])
  destination = os.path.join(root, *parts)
  if os.path.islink(destination):
    raise p.ForbiddenPath(f"{path} é um link simbólico")
  return destination


def download_folder(root, names, create=False):
  """Percorre, sem seguir links, a raiz de downloads `root` e as pastas
  `names` sob ela, nessa ordem, e devolve a última. Uma pasta que falta é
  criada quando `create` é verdadeiro; senão, devolve None. Cada pasta,
  existente ou recém-criada, é examinada antes de passar à seguinte, e a
  última precisa estar contida na raiz. Levanta p.ForbiddenPath para link
  simbólico ou saída da raiz."""
  folder = root
  for depth in range(len(names) + 1):
    if depth:
      folder = os.path.join(folder, names[depth - 1])
    if create:
      try:
        os.mkdir(folder)
      except FileExistsError:
        pass
    try:
      mode = os.lstat(folder).st_mode
    except FileNotFoundError:
      return None
    if stat.S_ISLNK(mode):
      where = "/".join(names[:depth]) if depth else "a raiz de downloads"
      raise p.ForbiddenPath(f"{where} é um link simbólico")
  p.check_inside(root, folder)
  return folder


class DownloadFailed(Exception):
  """Download não publicado; a mensagem traz o motivo."""


class Download:
  """Download ativo, do pedido até a publicação. A receptora grava cada bloco
  recebido em um temporário na pasta do destino e calcula o SHA-256 nos
  blocos; o destino final só recebe o arquivo verificado."""

  def __init__(self, path, destination, downloads_root):
    self.path = path
    self.destination = destination
    self.started = False
    self._downloads_root = downloads_root
    self._failure = None
    self._size = None
    self._received = 0
    self._digest = hashlib.sha256()
    self._temp = None
    self._temp_path = None

  def start(self, size):
    """Cria as pastas que faltam sob a raiz de downloads e o temporário. Se
    o caminho tiver deixado de ser permitido desde o pedido, nada é gravado
    e o download falha ao receber FILE_END."""
    self.started = True
    self._size = size
    try:
      folder = download_folder(self._downloads_root, self.path.split("/")[:-1], create=True)
    except p.ForbiddenPath as error:
      self._failure = f"caminho inválido ou proibido: {error}"
      return
    descriptor, self._temp_path = tempfile.mkstemp(prefix=".", suffix=".parcial", dir=folder)
    self._temp = os.fdopen(descriptor, "wb")

  def write(self, block):
    if self._failure is not None:
      return
    self._temp.write(block)
    self._temp.flush()
    self._digest.update(block)
    self._received += len(block)

  def finish(self, sha256):
    """Exige a quantidade anunciada em FILE_META e o SHA-256 anunciado em
    FILE_END, fecha o temporário e o publica no destino sem sobrescrever.
    Levanta DownloadFailed com o motivo; o temporário é sempre removido."""
    if self._failure is not None:
      raise DownloadFailed(self._failure)
    try:
      self._temp.close()
      if self._received != self._size:
        raise DownloadFailed(f"recebidos {self._received} bytes, mas o servidor anunciou {self._size}")
      if sha256 != self._digest.hexdigest():
        raise DownloadFailed("o SHA-256 anunciado pelo servidor não corresponde ao conteúdo recebido")
      try:
        os.link(self._temp_path, self.destination)
      except FileExistsError:
        raise DownloadFailed(f"já existe algo em {self.destination}; o objeto existente foi preservado") from None
    finally:
      os.unlink(self._temp_path)


class Client:
  """Sessão do participante. A thread principal cuida do teclado e dos
  envios; a receptora é a única leitora do socket e grava os downloads."""

  def __init__(self, sock, downloads_root=DOWNLOADS_ROOT):
    self._sock = sock
    self._downloads_root = downloads_root
    self._download = None
    self._leaving = threading.Event()
    self._disconnected = threading.Event()
    self._receiver = threading.Thread(target=self._receive_loop, name="receptora")

  def run(self):
    self._receiver.start()
    try:
      self._menu_loop()
    except EOFError:
      if not self._disconnected.is_set():
        self._quit()
    finally:
      self._leaving.set()
      try:
        self._sock.shutdown(socket.SHUT_RDWR)
      except OSError:
        pass
      self._receiver.join()
      self._sock.close()

  def _menu_loop(self):
    """Fim da entrada (EOF) em qualquer pergunta equivale a Sair."""
    while not self._disconnected.is_set():
      show(MENU)
      choice = input(PROMPT).strip().lower()
      if self._disconnected.is_set():
        return
      option = OPTIONS.get(choice)
      if option == "sair":
        self._quit()
        return
      if option == "chat":
        self._chat()
      elif option == "arquivo":
        self._request_file()
      else:
        show("Opção inválida.")

  def _chat(self):
    """Envia uma única mensagem ao chat coletivo e volta ao menu. A
    confirmação [Você] indica o envio, não a entrega aos demais."""
    text = input(CHAT_PROMPT)
    if self._disconnected.is_set():
      return
    try:
      p.check_chat_text(text)
    except p.InvalidChatText as error:
      show(f"Mensagem recusada: {error}")
      return
    try:
      p.send_message(self._sock, p.CHAT, {"text": text})
    except (p.ProtocolError, OSError) as error:
      show(f"Não foi possível enviar a mensagem: {error}")
      return
    show(f"[Você] {text}")

  def _request_file(self):
    """Pede um arquivo da raiz compartilhada e volta ao menu; a receptora
    grava o download, então o menu continua disponível durante a
    transferência."""
    path = input(FILE_PROMPT)
    if self._disconnected.is_set():
      return
    try:
      p.check_relative_path(path)
      destination = check_destination(self._downloads_root, path)
    except p.ForbiddenPath as error:
      show(f"Caminho inválido ou proibido: {error}")
      return
    if os.path.lexists(destination):
      show(f"Download recusado: já existe algo em {destination}; o objeto existente foi preservado.")
      return
    self._download = Download(path, destination, self._downloads_root)
    try:
      p.send_message(self._sock, p.FILE_REQUEST, {"path": path})
    except (p.ProtocolError, OSError) as error:
      self._download = None
      show(f"Não foi possível solicitar o arquivo: {error}")
      return
    show(f"Arquivo {path} solicitado.")

  def _quit(self):
    self._leaving.set()
    try:
      p.send_message(self._sock, p.QUIT, {})
    except (p.ProtocolError, OSError):
      pass
    show("Sessão encerrada.")

  def _show_received(self, line):
    """Exibe sem esperar o teclado: em nova linha, mesmo com um prompt ou
    texto parcialmente digitado na linha atual."""
    show("\n" + line)

  def _receive_error(self, error):
    """Recusa normal do servidor: a sessão continua. Uma recusa de
    FILE_REQUEST encerra o download que ainda aguardava FILE_META."""
    download = self._download
    if error.get("operation") == "FILE_REQUEST" and download is not None and not download.started:
      self._download = None
    self._show_received(f"Erro do servidor em {error.get('operation')}: {error.get('message')} ({error.get('code')})")

  def _receive_download(self, message):
    download = self._download
    if download is None:
      return
    fields = message.content if isinstance(message.content, dict) else {}
    if message.type == p.FILE_META:
      download.start(fields.get("size"))
    elif message.type == p.FILE_DATA:
      download.write(message.content)
    else:
      try:
        download.finish(fields.get("sha256"))
      except DownloadFailed as error:
        outcome = f"Download de {download.path} falhou: {error}. Nada foi publicado."
      else:
        outcome = f"Download concluído: {download.path} publicado em {download.destination}"
      self._download = None
      self._show_received(outcome)

  def _receive_loop(self):
    while True:
      try:
        message = p.receive_message(self._sock)
      except (p.ProtocolError, OSError):
        message = None
      if message is None:
        break
      if message.type == p.CHAT and isinstance(message.content, dict):
        self._show_received(f"[{message.content.get('origin')}] {message.content.get('text')}")
      elif message.type == p.ERROR and isinstance(message.content, dict):
        self._receive_error(message.content)
      elif message.type in (p.FILE_META, p.FILE_DATA, p.FILE_END):
        self._receive_download(message)
    self._disconnected.set()
    if not self._leaving.is_set():
      show("\nDesconectado do servidor. Pressione Enter para sair.")


def main():
  parser = argparse.ArgumentParser(description="Cliente TCP de chat coletivo e transferência de arquivos")
  parser.add_argument("host", nargs="?", default=HOST, help=f"endereço do servidor (padrão {HOST})")
  parser.add_argument("port", nargs="?", type=parse_port, default=PORT, help=f"porta TCP (padrão {PORT})")
  args = parser.parse_args()

  try:
    sock = socket.create_connection((args.host, args.port))
  except OSError as error:
    show(f"Não foi possível conectar a {args.host}:{args.port}: {error}")
    sys.exit(1)
  show(f"Conectado ao servidor {args.host}:{args.port}")
  try:
    Client(sock).run()
  except KeyboardInterrupt:
    show("\nCliente encerrado.")


if __name__ == "__main__":
  main()

import argparse
import hashlib
import os
import socket
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


class DownloadFailed(Exception):
  """Download não publicado; a mensagem traz o motivo."""


class TemporaryNotRemoved(Exception):
  """O temporário de um download não pôde ser removido; a mensagem traz o
  caminho e o motivo."""


class Download:
  """Download ativo, do pedido até a publicação. A receptora grava cada bloco
  recebido em um temporário na pasta do destino e calcula o SHA-256 nos
  blocos; o destino final só recebe o arquivo verificado."""

  def __init__(self, path, destination):
    self.path = path
    self.destination = destination
    self.started = False
    self._size = None
    self._received = 0
    self._digest = hashlib.sha256()
    self._temp = None
    self._temp_path = None

  def start(self, size):
    self.started = True
    self._size = size
    folder = os.path.dirname(self.destination)
    os.makedirs(folder, exist_ok=True)
    descriptor, self._temp_path = tempfile.mkstemp(prefix=".", suffix=".parcial", dir=folder)
    self._temp = os.fdopen(descriptor, "wb")

  def write(self, block):
    self._temp.write(block)
    self._temp.flush()
    self._digest.update(block)
    self._received += len(block)

  def finish(self, sha256):
    """Exige a quantidade anunciada em FILE_META e o SHA-256 anunciado em
    FILE_END, fecha o temporário e o publica no destino sem sobrescrever.
    Levanta DownloadFailed com o motivo. Publicado ou não, o temporário
    continua lá até `discard`."""
    self._temp.close()
    if self._received != self._size:
      raise DownloadFailed(f"recebidos {self._received} bytes, mas o servidor anunciou {self._size}")
    if sha256 != self._digest.hexdigest():
      raise DownloadFailed("o SHA-256 anunciado pelo servidor não corresponde ao conteúdo recebido")
    try:
      os.link(self._temp_path, self.destination)
    except FileExistsError:
      raise DownloadFailed(f"já existe algo em {self.destination}; o objeto existente foi preservado") from None

  def discard(self):
    """Fecha e remove o temporário, se houver, ao fim do download: concluído,
    falho ou interrompido. Levanta TemporaryNotRemoved se a remoção falhar."""
    if self._temp is None:
      return
    self._temp.close()
    try:
      os.unlink(self._temp_path)
    except OSError as error:
      raise TemporaryNotRemoved(
        f"Não foi possível remover o arquivo temporário {self._temp_path}: {error.strerror}"
      ) from None


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
    except p.ForbiddenPath as error:
      show(f"Caminho inválido ou proibido: {error}")
      return
    destination = os.path.join(self._downloads_root, *path.split("/"))
    if os.path.lexists(destination):
      show(f"Download recusado: já existe algo em {destination}; o objeto existente foi preservado.")
      return
    self._download = Download(path, destination)
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
      self._end_download(outcome)

  def _interrupt_download(self):
    """Fim da sessão com download ativo, por Sair ou desconexão: nada é
    publicado e não há retomada."""
    if self._download is not None:
      self._end_download(
        f"Download de {self._download.path} interrompido: nada foi publicado. "
        "Solicite-o novamente para baixá-lo desde o início."
      )

  def _end_download(self, outcome):
    """Encerra o download ativo: descarta o temporário e exibe `outcome`
    seguido, se a remoção falhar, da falha."""
    download, self._download = self._download, None
    report = [outcome]
    try:
      download.discard()
    except TemporaryNotRemoved as error:
      report.append(str(error))
    self._show_received("\n".join(report))

  def _receive_loop(self):
    """Recebe até a conexão terminar. A sessão acaba quando a receptora
    para, por qualquer motivo: o download ativo é interrompido e, se a saída
    não foi pedida, a desconexão é exibida sem esperar o teclado."""
    try:
      self._receive_until_end()
    finally:
      self._disconnected.set()
      self._interrupt_download()
      if not self._leaving.is_set():
        show("\nDesconectado do servidor. Pressione Enter para sair.")

  def _receive_until_end(self):
    while True:
      try:
        message = p.receive_message(self._sock)
      except (p.ProtocolError, OSError):
        return
      if message is None:
        return
      if message.type == p.CHAT and isinstance(message.content, dict):
        self._show_received(f"[{message.content.get('origin')}] {message.content.get('text')}")
      elif message.type == p.ERROR and isinstance(message.content, dict):
        self._receive_error(message.content)
      elif message.type in (p.FILE_META, p.FILE_DATA, p.FILE_END):
        self._receive_download(message)


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

import argparse
import errno
import hashlib
import os
import secrets
import socket
import stat
import sys
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


class DestinationTaken(Exception):
  """Já existe algo no destino local ou no lugar de uma de suas pastas; o
  objeto existente é preservado."""


def check_destination(root, path):
  """Destino local de `path`, um caminho já aceito por
  p.check_relative_path, sob a raiz de downloads `root`. Levanta
  p.ForbiddenPath se a raiz, uma pasta existente do caminho ou o próprio
  destino for link simbólico, e DestinationTaken se já houver algo no
  destino ou algo que não é pasta no lugar de uma delas; pastas que ainda
  não existem são aceitas. Uma falha ao examinar o caminho, como falta de
  permissão, segue como OSError."""
  parts = path.split("/")
  destination = os.path.join(root, *parts)
  folder = open_download_folder(root, parts[:-1])
  if folder is None:
    return destination
  try:
    mode = os.stat(parts[-1], dir_fd=folder, follow_symlinks=False).st_mode
  except FileNotFoundError:
    return destination
  finally:
    os.close(folder)
  if stat.S_ISLNK(mode):
    raise p.ForbiddenPath(f"{path} é um link simbólico")
  raise DestinationTaken(f"já existe algo em {destination}; o objeto existente foi preservado")


# Uma pasta é aberta sem seguir links, e o descritor obtido é o ponto de
# partida da operação seguinte; o caminho não é resolvido de novo pelo nome.
# O_PATH, onde existe, dispensa a permissão de leitura, como o exame por nome.
_FOLDER_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_PATH", 0)


def open_download_folder(root, names, create=False):
  """Abre, sem seguir links, a raiz de downloads `root` e as pastas `names`
  sob ela, cada uma a partir do descritor da anterior, e devolve o
  descritor da última, que o chamador fecha. Uma pasta que falta é criada
  quando `create` é verdadeiro; senão, devolve None. Cada pasta, existente
  ou recém-criada, é examinada ao ser aberta, antes de passar à seguinte, e
  a última precisa estar contida na raiz. Um link que surja depois numa
  pasta já aberta não é seguido. Levanta p.ForbiddenPath para link
  simbólico ou saída da raiz e DestinationTaken se algo que não é pasta
  ocupar o lugar de uma delas; uma falha ao examinar ou criar uma pasta
  segue como OSError."""
  folder = None
  try:
    for depth in range(len(names) + 1):
      name = names[depth - 1] if depth else root
      shown = "/".join(names[:depth]) if depth else "a raiz de downloads"
      if create:
        try:
          os.mkdir(name, dir_fd=folder)
        except FileExistsError:
          pass
      try:
        inner = _open_folder(name, folder, shown, os.path.join(root, *names[:depth]))
      except FileNotFoundError:
        if create:
          raise
        inner = None
      if folder is not None:
        os.close(folder)
      folder = inner
      if folder is None:
        return None
    p.check_inside(root, os.path.join(root, *names))
  except BaseException:
    if folder is not None:
      os.close(folder)
    raise
  return folder


def _open_folder(name, parent, shown, location):
  """Descritor da pasta `name`, relativa ao descritor `parent` (ou caminho
  completo, sem `parent`), aberta sem seguir links. `shown` e `location`
  nomeiam a pasta nas recusas."""
  try:
    return os.open(name, _FOLDER_FLAGS, dir_fd=parent)
  except OSError as error:
    failure = error
  try:
    mode = os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode
  except OSError:
    raise failure from None
  if stat.S_ISLNK(mode):
    raise p.ForbiddenPath(f"{shown} é um link simbólico")
  if not stat.S_ISDIR(mode):
    raise DestinationTaken(f"já existe algo em {location} que não é uma pasta; o objeto existente foi preservado")
  raise failure


def _identity(status):
  return status.st_dev, status.st_ino


def _create_temporary(folder):
  """Cria na pasta de descritor `folder` um temporário oculto de nome
  aleatório, sem nunca abrir um objeto que já exista; devolve o descritor
  e o nome."""
  for _ in range(100):
    name = f".{secrets.token_hex(6)}.parcial"
    try:
      return os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600,
                     dir_fd=folder), name
    except FileExistsError:
      continue
  raise FileExistsError(errno.EEXIST, "nenhum nome livre para o temporário")


def _failure_reason(error):
  """Motivo, para o participante, de uma falha ao abrir ou verificar as
  pastas do destino."""
  if isinstance(error, p.ForbiddenPath):
    return f"caminho inválido ou proibido: {error}"
  if isinstance(error, DestinationTaken):
    return str(error)
  return f"não foi possível acessar a pasta de destino ({error.strerror})"


class DownloadFailed(Exception):
  """Download não concluído; a mensagem traz o motivo. Salvo em
  PublicationNotUndone, nada do download ficou publicado."""


class PublicationNotUndone(DownloadFailed):
  """A conferência que segue a publicação falhou e a publicação não pôde
  ser desfeita: o arquivo publicado pode continuar no destino."""


class TemporaryNotRemoved(Exception):
  """O temporário de um download não pôde ser removido; a mensagem traz o
  caminho e o motivo."""


class Download:
  """Download ativo, do pedido até a publicação. A receptora grava cada bloco
  recebido em um temporário na pasta do destino e calcula o SHA-256 nos
  blocos; o destino final só recebe o arquivo verificado. A pasta do destino
  é aberta ao começar a transferência, e o temporário e a publicação partem
  desse descritor, sem resolver o caminho de novo pelo nome."""

  def __init__(self, path, destination, downloads_root):
    self.path = path
    self.destination = destination
    self.started = False
    self._downloads_root = downloads_root
    self._folders = path.split("/")[:-1]
    self._name = path.split("/")[-1]
    self._failure = None
    self._size = None
    self._received = 0
    self._digest = hashlib.sha256()
    self._folder = None
    self._temp = None
    self._temp_name = None
    self._identity = None

  def start(self, size):
    """Cria as pastas que faltam sob a raiz de downloads e o temporário. Se
    o caminho tiver deixado de ser permitido desde o pedido, nada é gravado
    e o download falha ao receber FILE_END."""
    self.started = True
    self._size = size
    try:
      self._folder = open_download_folder(self._downloads_root, self._folders, create=True)
      descriptor, self._temp_name = _create_temporary(self._folder)
    except (p.ForbiddenPath, DestinationTaken, OSError) as error:
      self._failure = _failure_reason(error)
      return
    self._identity = _identity(os.fstat(descriptor))
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
    Antes de publicar, confirma que a pasta aberta no começo ainda é a que
    se alcança a partir da raiz de downloads sem passar por links e que o
    temporário é o verificado; depois, confere a publicação com
    `_check_publication`. Levanta DownloadFailed com o motivo, inclusive
    uma falha local ao publicar, ou PublicationNotUndone. Publicado ou não,
    o temporário continua lá até `discard`."""
    if self._failure is not None:
      raise DownloadFailed(self._failure)
    self._temp.close()
    if self._received != self._size:
      raise DownloadFailed(f"recebidos {self._received} bytes, mas o servidor anunciou {self._size}")
    if sha256 != self._digest.hexdigest():
      raise DownloadFailed("o SHA-256 anunciado pelo servidor não corresponde ao conteúdo recebido")
    try:
      self._confirm(self._temp_name)
      os.link(self._temp_name, self._name, src_dir_fd=self._folder, dst_dir_fd=self._folder, follow_symlinks=False)
    except FileExistsError:
      raise DownloadFailed(f"já existe algo em {self.destination}; o objeto existente foi preservado") from None
    except OSError as error:
      raise DownloadFailed(f"não foi possível publicar em {self.destination} ({error.strerror})") from None
    self._check_publication()

  def _check_publication(self):
    """Conferência que segue a publicação: a pasta aberta no começo ainda é
    a pasta do destino e o nome publicado guarda o temporário verificado.
    Se não, a publicação é desfeita, mas o nome publicado só é removido se
    ainda guardar o que o próprio link publicou: o temporário verificado
    ou, se o temporário foi trocado logo antes do link, o objeto que agora
    ocupa o nome do temporário. Qualquer outro objeto no destino foi posto
    lá depois, por outro programa, e é preservado. Levanta DownloadFailed
    com o motivo, ou PublicationNotUndone se a remoção ou a própria
    conferência falhar."""
    moved = self._folder_moved()
    try:
      published = self._identity_at(self._name)
      if moved is None and published == self._identity:
        return
      linked = {self._identity, self._identity_at(self._temp_name)}
    except OSError as error:
      raise PublicationNotUndone(
        f"não foi possível conferir a publicação em {self.destination} ({error.strerror}); "
        "o arquivo publicado pode continuar lá"
      ) from None
    if published is None:
      raise DownloadFailed(moved or "o arquivo publicado foi removido logo depois da publicação")
    if published not in linked:
      raise DownloadFailed(f"{moved or 'o destino foi substituído logo depois da publicação'}; "
                           "o objeto que ocupa o destino foi preservado")
    reason = moved or "o temporário foi substituído durante a transferência"
    try:
      os.unlink(self._name, dir_fd=self._folder)
    except OSError as error:
      raise PublicationNotUndone(f"{reason}; não foi possível desfazer a publicação ({error.strerror}), "
                                 "e o arquivo publicado continua na pasta em que foi publicado") from None
    raise DownloadFailed(reason)

  def discard(self):
    """Ao fim do download, concluído ou não: fecha o temporário e a pasta do
    destino e remove o temporário, se o nome ainda for dele; um objeto posto
    em seu lugar é preservado. Levanta TemporaryNotRemoved se a remoção
    falhar."""
    if self._folder is None:
      return
    try:
      if self._temp is not None:
        self._temp.close()
      try:
        if self._temp_name is not None and self._is_temporary(self._temp_name):
          os.unlink(self._temp_name, dir_fd=self._folder)
      except OSError as error:
        temporary = os.path.join(os.path.dirname(self.destination), self._temp_name)
        raise TemporaryNotRemoved(
          f"Não foi possível remover o arquivo temporário {temporary}: {error.strerror}"
        ) from None
    finally:
      os.close(self._folder)
      self._folder = None

  def _confirm(self, name):
    """Levanta DownloadFailed se a pasta aberta no começo deixou de ser a
    pasta do destino ou se `name`, nela, não é o temporário verificado."""
    moved = self._folder_moved()
    if moved is not None:
      raise DownloadFailed(moved)
    if not self._is_temporary(name):
      raise DownloadFailed("o temporário foi substituído ou removido durante a transferência")

  def _is_temporary(self, name):
    return self._identity_at(name) == self._identity

  def _identity_at(self, name):
    """Identidade do objeto `name` na pasta aberta no começo, sem seguir
    links, ou None se não houver nada com esse nome."""
    try:
      return _identity(os.stat(name, dir_fd=self._folder, follow_symlinks=False))
    except FileNotFoundError:
      return None

  def _folder_moved(self):
    """Motivo pelo qual a pasta aberta no começo deixou de ser a pasta do
    destino alcançada a partir da raiz sem passar por links, ou None."""
    try:
      folder = open_download_folder(self._downloads_root, self._folders)
    except (p.ForbiddenPath, DestinationTaken, OSError) as error:
      return _failure_reason(error)
    if folder is None:
      return "a pasta de destino deixou de existir durante a transferência"
    try:
      same = _identity(os.fstat(folder)) == _identity(os.fstat(self._folder))
    finally:
      os.close(folder)
    return None if same else "a pasta de destino foi trocada durante a transferência"


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
    transferência. Com um download ativo (pedido pendente, transferência ou
    verificação final), recusa localmente sem perguntar o caminho: só a
    receptora encerra o download ativo, depois da publicação, da falha ou da
    interrupção."""
    active = self._download
    if active is not None:
      show(f"Já existe um download ativo ({active.path}); aguarde sua conclusão antes de pedir outro arquivo.")
      return
    path = input(FILE_PROMPT)
    if self._disconnected.is_set():
      return
    try:
      p.check_relative_path(path)
      destination = check_destination(self._downloads_root, path)
    except p.ForbiddenPath as error:
      show(f"Caminho inválido ou proibido: {error}")
      return
    except DestinationTaken as error:
      show(f"Download recusado: {error}.")
      return
    except OSError as error:
      show(f"Download recusado: não foi possível verificar o destino ({error.strerror}).")
      return
    self._download = Download(path, destination, self._downloads_root)
    try:
      p.send_message(self._sock, p.FILE_REQUEST, {"path": path})
    except ValueError as error:
      # p.encode recusou o frame antes de enviar qualquer byte.
      self._download = None
      show(f"Download recusado: o pedido não cabe em uma mensagem ({error}).")
      return
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
      self._finish_download(download, fields.get("sha256"))

  def _finish_download(self, download, sha256):
    """FILE_END: verifica e publica e então encerra o download com o
    resultado. O download deixa de estar ativo mesmo que algo falhe no
    caminho."""
    report = []
    try:
      try:
        download.finish(sha256)
      except PublicationNotUndone as error:
        report.append(f"Download de {download.path} falhou: {error}.")
      except DownloadFailed as error:
        report.append(f"Download de {download.path} falhou: {error}. Nada foi publicado.")
      else:
        report.append(f"Download concluído: {download.path} publicado em {download.destination}")
    finally:
      self._end_download(download, report)

  def _interrupt_download(self):
    """Fim da sessão com download ativo, por Sair ou desconexão: nada é
    publicado e não há retomada."""
    download = self._download
    if download is not None:
      self._end_download(download, [
        f"Download de {download.path} interrompido: nada foi publicado. "
        "Solicite-o novamente para baixá-lo desde o início."
      ])

  def _end_download(self, download, report):
    """Único fim de `download`, o download ativo, seja concluído, falho ou
    interrompido: ele deixa de estar ativo, o temporário é descartado e as
    linhas de `report` são exibidas, seguidas da falha ao remover o
    temporário, se houver."""
    self._download = None
    try:
      download.discard()
    except TemporaryNotRemoved as error:
      report.append(str(error))
    if report:
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
  # Bytes digitados que não formam UTF-8 viram substitutos isolados, que o
  # menu não reconhece e p.check_chat_text e p.check_relative_path recusam,
  # em vez de interromper input() com exceção.
  sys.stdin.reconfigure(errors="surrogateescape")

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

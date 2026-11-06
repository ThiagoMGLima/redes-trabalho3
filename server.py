import argparse
import collections
import errno
import hashlib
import os
import queue
import select
import socket
import stat
import sys
import threading

import protocol as p

HOST = ""
SERVER_ORIGIN = "Servidor"
SHARED_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files")
MAX_CLIENTS = 10
MAX_PENDING = 64


def terminal_log(line):
  print(line, flush=True)


class FileRefused(Exception):
  """Recusa normal de um FILE_REQUEST: `code` é o código estável do ERROR e
  a mensagem é a explicação legível."""

  def __init__(self, code, message):
    super().__init__(message)
    self.code = code


def open_shared_file(root, path):
  """Abre para leitura binária o arquivo `path`, relativo à raiz
  compartilhada `root`. Levanta FileRefused com PATH_FORBIDDEN para
  sintaxe proibida ou link simbólico na raiz, em qualquer pasta do caminho
  ou no arquivo, NOT_FOUND para arquivo inexistente, NOT_REGULAR para
  pasta, FIFO, dispositivo ou outro objeto que não seja arquivo regular e
  ACCESS_ERROR para falta de permissão ou outra falha de acesso, que não se
  confunde com ausência.

  Cada componente é examinado em ordem, sem seguir links: cada pasta é
  aberta a partir do descritor da anterior, e o arquivo a partir do
  descritor da última. Assim um link é recusado antes de se considerar a
  ausência do que viria depois dele, um objeto não regular nunca é aberto e
  uma pasta trocada por um link depois de examinada não é seguida, porque o
  caminho não é resolvido de novo pelo nome. Por fim, o caminho resolvido
  precisa estar contido na raiz pela relação entre diretórios."""
  try:
    p.check_relative_path(path)
  except p.ForbiddenPath as error:
    raise FileRefused(p.PATH_FORBIDDEN, f"Caminho inválido ou proibido: {error}") from None
  parts = path.split("/")
  shown = _shown(path)
  folder = _open_folder(root, None, "a raiz compartilhada", shown)
  try:
    for depth, part in enumerate(parts[:-1], start=1):
      inner = _open_folder(part, folder, _shown("/".join(parts[:depth])), shown)
      os.close(folder)
      folder = inner
    try:
      mode = os.stat(parts[-1], dir_fd=folder, follow_symlinks=False).st_mode
    except OSError as error:
      raise _refusal_for(error, shown) from None
    if stat.S_ISLNK(mode):
      raise FileRefused(p.PATH_FORBIDDEN, f"Caminho inválido ou proibido: {shown} é um link simbólico")
    if not stat.S_ISREG(mode):
      raise FileRefused(p.NOT_REGULAR, f"Não é um arquivo regular: {shown}")
    try:
      p.check_inside(root, os.path.join(root, *parts))
    except p.ForbiddenPath as error:
      raise FileRefused(p.PATH_FORBIDDEN, f"Caminho inválido ou proibido: {error}") from None
    return _open_regular(parts[-1], folder, shown)
  finally:
    os.close(folder)


# Uma recusa repete o caminho pedido. Um caminho longo aparece encurtado no
# meio, para que o ERROR caiba no limite do frame qualquer que seja o pedido.
SHOWN_PATH_LIMIT = 200


def _shown(path):
  if len(path) <= SHOWN_PATH_LIMIT:
    return path
  half = SHOWN_PATH_LIMIT // 2
  return f"{path[:half]}…{path[-half:]}"


def _open_folder(name, parent, shown, path):
  """Descritor da pasta `name`, relativa ao descritor `parent` (ou caminho
  completo, sem `parent`), aberta sem seguir links. Uma falha é
  classificada pelo que está no lugar: link é PATH_FORBIDDEN, algo que não
  é pasta é NOT_FOUND e as demais seguem `_refusal_for`. Na recusa, `shown`
  nomeia a pasta e `path`, o caminho pedido, ambos já encurtados."""
  try:
    return os.open(name, p.FOLDER_FLAGS, dir_fd=parent)
  except OSError as error:
    failure = error
  try:
    mode = os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode
  except OSError:
    raise _refusal_for(failure, path) from None
  if stat.S_ISLNK(mode):
    raise FileRefused(p.PATH_FORBIDDEN, f"Caminho inválido ou proibido: {shown} é um link simbólico")
  if not stat.S_ISDIR(mode):
    raise FileRefused(p.NOT_FOUND, f"Arquivo inexistente: {path}")
  raise _refusal_for(failure, path)


# Ao abrir, não seguir um link nem esperar por um FIFO que tenha tomado o
# lugar do arquivo depois do exame; em arquivo regular, O_NONBLOCK não altera
# a leitura.
_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def _open_regular(name, folder, path):
  try:
    descriptor = os.open(name, _OPEN_FLAGS, dir_fd=folder)
  except OSError as error:
    raise _refusal_for(error, path) from None
  if not stat.S_ISREG(os.fstat(descriptor).st_mode):
    os.close(descriptor)
    raise FileRefused(p.NOT_REGULAR, f"Não é um arquivo regular: {path}")
  return os.fdopen(descriptor, "rb")


def _refusal_for(error, path):
  """Recusa correspondente a uma falha ao examinar ou abrir `path`: só a
  ausência é NOT_FOUND; falta de permissão e as demais falhas de acesso são
  ACCESS_ERROR, e um link encontrado ao abrir é PATH_FORBIDDEN."""
  if isinstance(error, (FileNotFoundError, NotADirectoryError)):
    return FileRefused(p.NOT_FOUND, f"Arquivo inexistente: {path}")
  if error.errno == errno.ELOOP:
    return FileRefused(p.PATH_FORBIDDEN, f"Caminho inválido ou proibido: {path} passa por um link simbólico")
  return FileRefused(p.ACCESS_ERROR, f"Falha ao acessar o arquivo solicitado: {path} ({error.strerror})")


class FileTransfer:
  """Envio incremental de um arquivo aberto em modo binário: FILE_META com o
  tamanho, blocos FILE_DATA de até CHUNK_SIZE bytes (só o último pode ser
  menor) e FILE_END com o SHA-256 calculado sobre os blocos lidos. O arquivo
  nunca é lido por inteiro; cada bloco é lido quando vai ser enviado."""

  def __init__(self, file):
    self._file = file
    self._messages = self._read()
    self.finished = False

  def next_message(self):
    """Próxima mensagem do envio. Ao entregar o FILE_END, fecha o arquivo e
    marca `finished`."""
    message = next(self._messages)
    if message[0] == p.FILE_END:
      self.finished = True
      self.close()
    return message

  def close(self):
    self._file.close()

  def _read(self):
    yield p.FILE_META, {"size": os.fstat(self._file.fileno()).st_size}
    digest = hashlib.sha256()
    while block := self._file.read(p.CHUNK_SIZE):
      digest.update(block)
      yield p.FILE_DATA, block
    yield p.FILE_END, {"sha256": digest.hexdigest()}


class Session:
  """Atendimento de uma conexão aceita, cujo participante tem o
  identificador do cliente `client_id`, atribuído pelo servidor.

  A thread de atendimento é a única leitora do socket e a auxiliar escritora
  é a única que escreve nele; as demais threads entregam mensagens pela fila
  da sessão com `send` e arquivos com `send_file`, que nunca esperam espaço:
  a fila guarda até MAX_PENDING mensagens pendentes e, cheia, encerra só esta
  sessão. `close` é idempotente e pode ser chamado por qualquer thread: a
  primeira chamada descarta a fila, fecha os arquivos pendentes, interrompe
  as duas threads e notifica `on_close` uma única vez. O socket é fechado
  pela thread de atendimento depois que a escritora termina.
  """

  _STOP = object()

  def __init__(self, sock, client_id, *, send_timeout=p.SEND_TIMEOUT, receive_timeout=p.RECEIVE_TIMEOUT,
               on_message=None, on_close=None):
    self.sock = sock
    self.client_id = client_id
    self._send_timeout = send_timeout
    self._receive_timeout = receive_timeout
    self._on_message = on_message
    self._on_close = on_close
    self._lock = threading.Lock()
    self._closed = False
    self._download_active = False
    self._outgoing = queue.Queue(MAX_PENDING)
    self._reader = threading.Thread(target=self._read_loop, name=f"atendimento {client_id}")
    self._writer = threading.Thread(target=self._write_loop, name=f"escritora {client_id}")

  def start(self):
    self._writer.start()
    self._reader.start()

  def send(self, msg_type, content):
    self._enqueue((msg_type, content))

  @property
  def download_active(self):
    """Há um arquivo entregue a `send_file` cujo FILE_END ainda não seguiu
    para envio. A escritora libera o download antes de enviar o FILE_END, de
    modo que quem o recebe já pode pedir outro arquivo."""
    return self._download_active

  def send_file(self, file):
    """Enfileira o envio incremental de `file`, aberto em modo binário; a
    escritora o lê bloco a bloco, alternando com as mensagens da fila. A
    sessão passa a ser dona do arquivo e o fecha ao terminar o envio ou ao
    ser encerrada. O download fica ativo até o FILE_END."""
    transfer = FileTransfer(file)
    self._download_active = True
    if not self._enqueue(transfer):
      transfer.close()

  def _enqueue(self, item):
    """Enfileira sem esperar espaço e informa se o item entrou na fila."""
    with self._lock:
      if self._closed:
        return False
      try:
        self._outgoing.put_nowait(item)
        return True
      except queue.Full:
        pass
    self.close(f"fila de saída cheia: {MAX_PENDING} mensagens pendentes")
    return False

  def close(self, reason):
    with self._lock:
      if self._closed:
        return
      self._closed = True
      self._discard_outgoing()
      self._outgoing.put_nowait(self._STOP)
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
        item = self._outgoing.get_nowait()
      except queue.Empty:
        return
      if isinstance(item, FileTransfer):
        item.close()

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
      if message.type == p.QUIT and message.content == {}:
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
    """Sem arquivo ativo, espera a fila. Com arquivo ativo, alterna uma
    mensagem da fila, se houver, e a próxima mensagem do arquivo; cada frame
    é enviado inteiro antes do seguinte. Um arquivo enfileirado durante outro
    envio começa depois do FILE_END do atual."""
    active = None
    files = collections.deque()
    try:
      while True:
        item = self._next_item(wait=active is None and not files)
        if item is self._STOP:
          return "sessão encerrada"
        if isinstance(item, FileTransfer):
          files.append(item)
        elif item is not None:
          p.send_message(self.sock, *item, timeout=self._send_timeout)
        if active is None and files:
          active = files.popleft()
        if active is not None:
          message = active.next_message()
          if active.finished:
            active = None
            self._download_active = False
          p.send_message(self.sock, *message, timeout=self._send_timeout)
    except p.FrameTimeout:
      return "prazo de envio expirado"
    except OSError as error:
      return f"falha no envio: {error}"
    finally:
      for transfer in [active, *files]:
        if transfer is not None:
          transfer.close()

  def _next_item(self, wait):
    if wait:
      return self._outgoing.get()
    try:
      return self._outgoing.get_nowait()
    except queue.Empty:
      return None


class Server:
  def __init__(self, host=HOST, port=p.DEFAULT_PORT, *, send_timeout=p.SEND_TIMEOUT, receive_timeout=p.RECEIVE_TIMEOUT,
               log=terminal_log, shared_root=SHARED_ROOT):
    self._host = host
    self._port = port
    self._shared_root = shared_root
    self._send_timeout = send_timeout
    self._receive_timeout = receive_timeout
    self._log = log
    self._lock = threading.Lock()
    self._sessions = {}
    self._started = []
    self._last_client_number = 0
    self._stopping = False

  @property
  def address(self):
    return self._listener.getsockname()[:2]

  def start(self):
    self._listener = socket.create_server((self._host, self._port))
    self._wake_reader, self._wake_writer = socket.socketpair()
    self._acceptor = threading.Thread(target=self._accept_loop, name="aceitação")
    self._acceptor.start()

  def broadcast(self, text):
    """Broadcast do servidor: entrega o texto do operador a todos os clientes
    conectados com origem Servidor. Levanta p.InvalidChatText se o texto não
    cumprir as regras do chat, sem enviar nada."""
    p.check_chat_text(text)
    self._deliver(SERVER_ORIGIN, text)

  def shutdown(self):
    """Para de aceitar conexões, encerra as sessões conectadas sem esperar
    seus envios e só retorna quando as threads de todas as sessões, inclusive
    as que já estavam terminando por outro motivo, finalizaram."""
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
      started = list(self._started)
    for session in sessions:
      session.close("servidor encerrado")
    for session in started:
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

  def _open_session(self, sock, address):
    with self._lock:
      full = len(self._sessions) >= MAX_CLIENTS
      if not full:
        self._last_client_number += 1
        client_id = f"Cliente {self._last_client_number}"
    if full:
      self._refuse_connection(sock, address)
      return
    session = Session(
      sock,
      client_id,
      send_timeout=self._send_timeout,
      receive_timeout=self._receive_timeout,
      on_message=self._handle_message,
      on_close=self._end_session,
    )
    with self._lock:
      self._sessions[session] = address
      # Uma sessão deixa os destinatários ao ser encerrada, mas o
      # encerramento do servidor ainda espera suas threads.
      self._started = [started for started in self._started if started.is_alive()] + [session]
    self._log(f"Conectado: {address} como {client_id}")
    session.start()

  def _refuse_connection(self, sock, address):
    """Conexão excedente: fechada imediatamente, sem identificador nem
    mensagem ao cliente; a recusa fica registrada no servidor."""
    sock.close()
    self._log(f"Recusado: {address} (limite de {MAX_CLIENTS} clientes simultâneos atingido)")

  def _end_session(self, session, reason):
    with self._lock:
      address = self._sessions.pop(session)
    self._log(f"Desconectado: {address} como {session.client_id} ({reason})")

  def _handle_message(self, session, message):
    if message.type == p.CHAT:
      self._receive_chat(session, message.content)
      return
    if message.type == p.FILE_REQUEST:
      self._receive_file_request(session, message.content)
      return
    if message.type == p.QUIT:
      self._refuse(session, p.QUIT, p.INVALID_REQUEST, "QUIT exige um objeto vazio")
      return
    self._log(f"{p.TYPE_NAMES[message.type]} de {session.client_id} ignorado: só o servidor envia essa mensagem")

  def _receive_chat(self, session, content):
    text = content.get("text") if isinstance(content, dict) else None
    if not isinstance(text, str):
      self._refuse(session, p.CHAT, p.INVALID_REQUEST, "CHAT exige um objeto com o campo text do tipo string")
      return
    try:
      p.check_chat_text(text)
    except p.InvalidChatText as error:
      self._refuse(session, p.CHAT, p.INVALID_CHAT, str(error))
      return
    self._deliver(session.client_id, text, skip=session)

  def _receive_file_request(self, session, content):
    """Recusas normais seguem como ERROR; um arquivo aberto é entregue à
    escritora da sessão, que o envia bloco a bloco. Enquanto a sessão tem um
    download ativo, todo pedido recebe BUSY e o envio atual prossegue."""
    if session.download_active:
      self._refuse(session, p.FILE_REQUEST, p.BUSY, "Já existe um download ativo nesta sessão")
      return
    path = content.get("path") if isinstance(content, dict) else None
    if not isinstance(path, str):
      self._refuse(
        session, p.FILE_REQUEST, p.INVALID_REQUEST, "FILE_REQUEST exige um objeto com o campo path do tipo string"
      )
      return
    try:
      file = open_shared_file(self._shared_root, path)
    except FileRefused as refusal:
      self._refuse(session, p.FILE_REQUEST, refusal.code, str(refusal))
      return
    session.send_file(file)

  def _refuse(self, session, operation, code, message):
    """Recusa normal da mensagem do tipo `operation`: o ERROR segue pela
    fila da sessão, que continua aberta."""
    session.send(p.ERROR, {"operation": p.TYPE_NAMES[operation], "code": code, "message": message})

  def _deliver(self, origin, text, skip=None):
    """Exibe no terminal e enfileira para os clientes conectados, exceto
    `skip`; a origem é sempre a atribuída pelo servidor."""
    self._log(f"[{origin}] {text}")
    with self._lock:
      recipients = [session for session in self._sessions if session is not skip]
    for session in recipients:
      session.send(p.CHAT, {"origin": origin, "text": text})


def read_operator_input(server):
  """Envia cada linha digitada pelo operador como broadcast do servidor até
  o fim da entrada; texto fora das regras é recusado localmente."""
  while True:
    try:
      line = input()
    except EOFError:
      terminal_log("EOF na entrada do operador: encerrando o servidor")
      return
    try:
      server.broadcast(line)
    except p.InvalidChatText as error:
      terminal_log(f"Mensagem recusada: {error}")


def main():
  parser = argparse.ArgumentParser(description="Servidor TCP de chat coletivo e transferência de arquivos")
  parser.add_argument("port", nargs="?", type=p.parse_port, default=p.DEFAULT_PORT,
                      help=f"porta TCP (padrão {p.DEFAULT_PORT})")
  args = parser.parse_args()
  # Bytes digitados que não formam UTF-8 viram substitutos isolados, que
  # p.check_chat_text recusa, em vez de interromper input() com exceção.
  sys.stdin.reconfigure(errors="surrogateescape")

  server = Server(HOST, args.port)
  try:
    server.start()
  except OSError as error:
    terminal_log(f"Não foi possível escutar na porta TCP {args.port}: {error}")
    sys.exit(1)
  # Com o servidor já aceitando conexões, um Ctrl+C a qualquer momento,
  # inclusive durante as mensagens iniciais, precisa encerrá-lo.
  try:
    terminal_log(f"Servidor escutando na porta TCP {server.address[1]}.")
    terminal_log("Digite uma mensagem e Enter para enviá-la a todos os clientes. Ctrl+C ou EOF encerra.")
    read_operator_input(server)
  except KeyboardInterrupt:
    terminal_log("Ctrl+C recebido: encerrando o servidor")
  finally:
    server.shutdown()
  terminal_log("Servidor encerrado")


if __name__ == "__main__":
  main()

"""Servidor TCP multithread que serve os arquivos da pasta raiz.

Cada conexão aceita é atendida por uma thread dedicada. O socket de escuta é
criado explicitamente com AF_INET e SOCK_STREAM: SOCK_STREAM é o que faz dele
um socket TCP.
"""
import argparse
import errno
import os
import select
import socket
import stat
import sys
import threading

import protocol as p

HOST = ""
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "www")
MAX_CONNECTIONS = 10
RECEIVE_SIZE = 4096


def terminal_log(line):
  print(line, flush=True)


class ResourceRefused(Exception):
  """Recusa da abertura de um recurso: `code` diz o motivo (caminho
  proibido, inexistente, não regular ou falha de acesso) e a mensagem é a
  explicação legível."""

  def __init__(self, code, message):
    super().__init__(message)
    self.code = code


def open_resource(root, path):
  """Abre para leitura binária o recurso `path`, relativo à pasta raiz
  `root`. Levanta ResourceRefused com PATH_FORBIDDEN para
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
    raise ResourceRefused(p.PATH_FORBIDDEN, f"Caminho inválido ou proibido: {error}") from None
  parts = path.split("/")
  shown = _shown(path)
  folder = _open_folder(root, None, "a pasta raiz", shown)
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
      raise ResourceRefused(p.PATH_FORBIDDEN, f"Caminho inválido ou proibido: {shown} é um link simbólico")
    if not stat.S_ISREG(mode):
      raise ResourceRefused(p.NOT_REGULAR, f"Não é um arquivo regular: {shown}")
    try:
      p.check_inside(root, os.path.join(root, *parts))
    except p.ForbiddenPath as error:
      raise ResourceRefused(p.PATH_FORBIDDEN, f"Caminho inválido ou proibido: {error}") from None
    return _open_regular(parts[-1], folder, shown)
  finally:
    os.close(folder)


# Uma recusa repete o caminho pedido. Um caminho longo aparece encurtado no
# meio, para que a explicação continue legível qualquer que seja o pedido.
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
    raise ResourceRefused(p.PATH_FORBIDDEN, f"Caminho inválido ou proibido: {shown} é um link simbólico")
  if not stat.S_ISDIR(mode):
    raise ResourceRefused(p.NOT_FOUND, f"Arquivo inexistente: {path}")
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
    raise ResourceRefused(p.NOT_REGULAR, f"Não é um arquivo regular: {path}")
  return os.fdopen(descriptor, "rb")


def _refusal_for(error, path):
  """Recusa correspondente a uma falha ao examinar ou abrir `path`: só a
  ausência é NOT_FOUND; falta de permissão e as demais falhas de acesso são
  ACCESS_ERROR, e um link encontrado ao abrir é PATH_FORBIDDEN."""
  if isinstance(error, (FileNotFoundError, NotADirectoryError)):
    return ResourceRefused(p.NOT_FOUND, f"Arquivo inexistente: {path}")
  if error.errno == errno.ELOOP:
    return ResourceRefused(p.PATH_FORBIDDEN, f"Caminho inválido ou proibido: {path} passa por um link simbólico")
  return ResourceRefused(p.ACCESS_ERROR, f"Falha ao acessar o arquivo solicitado: {path} ({error.strerror})")


class Connection:
  """Atendimento de uma conexão aceita por uma thread dedicada, que guarda o
  estado da conexão: o socket, o endereço do cliente e o nome da thread.

  A thread é a única que lê o socket. `close` é idempotente e pode ser
  chamado por qualquer thread: a primeira chamada interrompe a conexão e
  notifica `on_close` uma única vez com o motivo. O socket é fechado pela
  própria thread de atendimento, ao terminar.
  """

  def __init__(self, sock, address, number, *, on_close=None):
    self.sock = sock
    self.address = address
    self._on_close = on_close
    self._lock = threading.Lock()
    self._closed = False
    self._thread = threading.Thread(target=self._serve, name=f"conexão {number}")

  @property
  def name(self):
    return self._thread.name

  def start(self):
    self._thread.start()

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
    self._thread.join(timeout)

  def is_alive(self):
    return self._thread.is_alive()

  def _serve(self):
    reason = "falha inesperada no atendimento"
    try:
      reason = self._receive_until_end()
    finally:
      self.close(reason)
      self.sock.close()

  def _receive_until_end(self):
    while True:
      try:
        data = self.sock.recv(RECEIVE_SIZE)
      except OSError as error:
        return f"falha na conexão: {error}"
      if not data:
        return "conexão encerrada pelo cliente"


class Server:
  def __init__(self, host=HOST, port=p.DEFAULT_PORT, *, log=terminal_log, root=ROOT):
    self._host = host
    self._port = port
    self._root = root
    self._log = log
    self._lock = threading.Lock()
    self._connections = set()
    self._started = []
    self._last_number = 0
    self._stopping = False

  @property
  def address(self):
    return self._listener.getsockname()[:2]

  def start(self):
    """Cria o socket TCP de escuta, associa-o à porta em todas as interfaces
    IPv4 (ou no `host` dado) e começa a aceitar conexões numa thread."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
      listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
      listener.bind((self._host, self._port))
      listener.listen()
    except OSError:
      listener.close()
      raise
    self._listener = listener
    self._wake_reader, self._wake_writer = socket.socketpair()
    self._acceptor = threading.Thread(target=self._accept_loop, name="aceitação")
    self._acceptor.start()

  def wait(self):
    """Espera até a aceitação de conexões terminar."""
    self._acceptor.join()

  def shutdown(self):
    """Para de aceitar conexões, encerra as conexões abertas e só retorna
    quando as threads de todas elas, inclusive as que já estavam terminando
    por outro motivo, finalizaram."""
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
      connections = list(self._connections)
      started = list(self._started)
    for connection in connections:
      connection.close("servidor encerrado")
    for connection in started:
      connection.join()

  def _accept_loop(self):
    while True:
      readable, _, _ = select.select([self._listener, self._wake_reader], [], [])
      if self._wake_reader in readable:
        return
      try:
        sock, address = self._listener.accept()
      except OSError:
        continue
      self._open_connection(sock, f"{address[0]}:{address[1]}")

  def _open_connection(self, sock, address):
    """Entrega o socket aceito a uma nova thread dedicada, salvo se o limite
    de conexões simultâneas já foi atingido."""
    with self._lock:
      full = len(self._connections) >= MAX_CONNECTIONS
      if not full:
        self._last_number += 1
        number = self._last_number
    if full:
      self._refuse_connection(sock, address)
      return
    connection = Connection(sock, address, number, on_close=self._end_connection)
    with self._lock:
      self._connections.add(connection)
      # Uma conexão sai do conjunto ao ser encerrada, mas o encerramento do
      # servidor ainda espera sua thread.
      self._started = [started for started in self._started if started.is_alive()] + [connection]
    self._log(f"Conectado: {address} na thread {connection.name}")
    connection.start()

  def _refuse_connection(self, sock, address):
    """Conexão excedente: fechada imediatamente; a recusa fica registrada."""
    sock.close()
    self._log(f"Recusado: {address} (limite de {MAX_CONNECTIONS} conexões simultâneas atingido)")

  def _end_connection(self, connection, reason):
    with self._lock:
      self._connections.discard(connection)
    self._log(f"Desconectado: {connection.address} na thread {connection.name} ({reason})")


def main():
  parser = argparse.ArgumentParser(description="Servidor HTTP simplificado sobre TCP")
  parser.add_argument("port", nargs="?", type=p.parse_port, default=p.DEFAULT_PORT,
                      help=f"porta TCP (padrão {p.DEFAULT_PORT})")
  args = parser.parse_args()

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
    terminal_log("Ctrl+C encerra.")
    server.wait()
  except KeyboardInterrupt:
    terminal_log("Ctrl+C recebido: encerrando o servidor")
  finally:
    server.shutdown()
  terminal_log("Servidor encerrado")


if __name__ == "__main__":
  main()

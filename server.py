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
import time

import protocol as p

HOST = ""
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "www")
MAX_CONNECTIONS = 64
RECEIVE_SIZE = 4096
BLOCK_SIZE = 64 * 1024
RECEIVE_TIMEOUT = 10.0
SEND_TIMEOUT = 30.0
# Fechamento gracioso: quanto esperar e quanto descartar do que o cliente
# ainda envia depois da resposta.
LINGER_TIMEOUT = 1.0
LINGER_LIMIT = 64 * 1024


def terminal_log(line):
  print(line, flush=True)


_LABELS = {
  p.BLOCKED_PATH: "Caminho bloqueado",
  p.NOT_FOUND: "Recurso inexistente",
  p.NOT_REGULAR: "Recurso inexistente",
  p.ACCESS_ERROR: "Falha ao acessar o recurso",
}


class ResourceRefused(Exception):
  """Recusa da abertura de um recurso: `code` diz o motivo (caminho
  bloqueado, recurso inexistente, objeto que não é arquivo regular ou falha
  de acesso), `label` o nomeia no vocabulário do glossário e `detail`
  explica o caso."""

  def __init__(self, code, detail):
    self.code = code
    self.label = _LABELS[code]
    self.detail = detail
    super().__init__(f"{self.label}: {detail}")


def open_resource(root, path):
  """Abre para leitura binária o recurso `path`, relativo à pasta raiz
  `root`. Levanta ResourceRefused com BLOCKED_PATH para sintaxe proibida ou
  link simbólico na raiz, em qualquer pasta do caminho ou no arquivo,
  NOT_FOUND quando nada existe no caminho, NOT_REGULAR para pasta, FIFO,
  dispositivo ou outro objeto que não seja arquivo regular e ACCESS_ERROR
  para falta de permissão ou outra falha de acesso, que não se confunde com
  ausência.

  Cada componente é examinado em ordem, sem seguir links: cada pasta é
  aberta a partir do descritor da anterior, e o arquivo a partir do
  descritor da última. Assim um link é recusado antes de se considerar a
  ausência do que viria depois dele, um objeto não regular nunca é aberto e
  uma pasta trocada por um link depois de examinada não é seguida, porque o
  caminho não é resolvido de novo pelo nome. Por fim, o caminho resolvido
  precisa estar contido na raiz pela relação entre diretórios."""
  try:
    p.check_relative_path(path)
  except p.BlockedPath as error:
    raise ResourceRefused(p.BLOCKED_PATH, str(error)) from None
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
      raise _link(shown)
    if not stat.S_ISREG(mode):
      raise _not_regular(shown)
    try:
      p.check_inside(root, os.path.join(root, *parts))
    except p.BlockedPath as error:
      raise ResourceRefused(p.BLOCKED_PATH, str(error)) from None
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


def _link(name):
  return ResourceRefused(p.BLOCKED_PATH, f"{name} é um link simbólico")


def _not_regular(name):
  return ResourceRefused(p.NOT_REGULAR, f"{name} não é um arquivo regular")


# Uma pasta é aberta sem seguir links, e o descritor obtido é o ponto de
# partida da operação seguinte; o caminho não é resolvido de novo pelo nome.
# O_PATH, onde existe, dispensa a permissão de leitura, como o exame por nome.
_FOLDER_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_PATH", 0)

# Ao abrir o arquivo, não seguir um link nem esperar por um FIFO que tenha
# tomado o lugar do arquivo depois do exame; em arquivo regular, O_NONBLOCK
# não altera a leitura.
_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def _open_folder(name, parent, shown, path):
  """Descritor da pasta `name`, relativa ao descritor `parent` (ou caminho
  completo, sem `parent`), aberta sem seguir links. Uma falha é
  classificada pelo que está no lugar: link é BLOCKED_PATH, algo que não é
  pasta é NOT_FOUND e as demais seguem `_refusal_for`. Na recusa, `shown`
  nomeia a pasta e `path`, o caminho pedido, ambos já encurtados."""
  try:
    return os.open(name, _FOLDER_FLAGS, dir_fd=parent)
  except OSError as error:
    failure = error
  try:
    mode = os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode
  except OSError:
    raise _refusal_for(failure, path) from None
  if stat.S_ISLNK(mode):
    raise _link(shown)
  if not stat.S_ISDIR(mode):
    raise ResourceRefused(p.NOT_FOUND, path)
  raise _refusal_for(failure, path)


def _open_regular(name, folder, path):
  try:
    descriptor = os.open(name, _OPEN_FLAGS, dir_fd=folder)
  except OSError as error:
    raise _refusal_for(error, path) from None
  if not stat.S_ISREG(os.fstat(descriptor).st_mode):
    os.close(descriptor)
    raise _not_regular(path)
  return os.fdopen(descriptor, "rb")


def _refusal_for(error, path):
  """Recusa correspondente a uma falha ao examinar ou abrir `path`: só a
  ausência é NOT_FOUND; falta de permissão e as demais falhas de acesso são
  ACCESS_ERROR, e um link encontrado ao abrir é BLOCKED_PATH."""
  if isinstance(error, (FileNotFoundError, NotADirectoryError)):
    return ResourceRefused(p.NOT_FOUND, path)
  if error.errno == errno.ELOOP:
    return ResourceRefused(p.BLOCKED_PATH, f"{path} passa por um link simbólico")
  return ResourceRefused(p.ACCESS_ERROR, f"{path} ({error.strerror})")


class Connection:
  """Atendimento de uma conexão aceita por uma thread dedicada, que guarda o
  estado da conexão: o socket, o endereço do cliente e o nome da thread.

  A thread lê uma requisição HTTP até a linha em branco, responde com o
  recurso pedido da pasta raiz e encerra a conexão: uma requisição por
  conexão. Uma conexão acima do limite (`refused`) só recebe a página 503.
  `close` é idempotente e pode ser chamado por qualquer thread: a primeira
  chamada interrompe a conexão e notifica `on_close` uma única vez com o
  motivo. O socket é fechado pela própria thread de atendimento, ao
  terminar.
  """

  def __init__(self, sock, address, name, *, root=ROOT, log=terminal_log, receive_timeout=RECEIVE_TIMEOUT,
               send_timeout=SEND_TIMEOUT, refused=False, on_close=None):
    self.sock = sock
    self.address = address
    self._root = root
    self._log = log
    self._receive_timeout = receive_timeout
    self._send_timeout = send_timeout
    self._refused = refused
    self._on_close = on_close
    self._lock = threading.Lock()
    self._closed = False
    self._thread = threading.Thread(target=self._serve, name=name)

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
      reason = self._refuse_over_the_limit() if self._refused else self._answer()
    except TimeoutError:
      reason = "prazo de envio expirado"
    except OSError as error:
      reason = f"falha na conexão: {error}"
    finally:
      self._finish()
      self.close(reason)
      self.sock.close()

  def _refuse_over_the_limit(self):
    self.sock.settimeout(self._send_timeout)
    self._send_error(None, 503, f"O servidor já atende {MAX_CONNECTIONS} conexões simultâneas. Tente de novo em instantes.")
    return "recusada pelo limite de conexões"

  def _finish(self):
    """Fechamento gracioso, como recomenda a RFC 9112 (seção 9.6): encerra
    só o sentido de envio, que avisa o fim da resposta, e descarta por um
    instante o que o cliente ainda envia. Fechar o socket com bytes
    recebidos e não lidos faz o sistema mandar um reset, que pode apagar a
    resposta antes de o cliente lê-la."""
    try:
      self.sock.shutdown(socket.SHUT_WR)
      deadline = time.monotonic() + LINGER_TIMEOUT
      discarded = 0
      while discarded < LINGER_LIMIT and (remaining := deadline - time.monotonic()) > 0:
        self.sock.settimeout(remaining)
        chunk = self.sock.recv(RECEIVE_SIZE)
        if not chunk:
          return
        discarded += len(chunk)
    except OSError:
      pass

  def _answer(self):
    started = time.time()
    try:
      head = self._receive_head()
    except _NothingReceived:
      return "prazo de recepção expirado sem nenhum byte recebido"
    except p.RequestError as error:
      status, size = self._send_error(error.method, error.status, str(error))
      self._log_request("(requisição incompleta)", status, size, started)
      return "resposta de erro enviada"
    if head is None:
      return "conexão encerrada pelo cliente"
    try:
      request = p.parse_request(head)
    except p.RequestError as error:
      status, size = self._send_error(error.method, error.status, str(error))
      self._log_request(head.split(b"\r\n", 1)[0].decode("iso-8859-1"), status, size, started)
      return "resposta de erro enviada"
    status, size = self._respond(request)
    self._log_request(f"{request.method} {request.target} {request.version}", status, size, started)
    return "resposta enviada"

  def _respond(self, request):
    """Envia a resposta à requisição e devolve o status e o tamanho do corpo
    anunciado. Para HEAD, o cabeçalho é o mesmo do GET, sem o corpo."""
    try:
      requested = p.request_path(request.target)
    except p.RequestError as error:
      return self._send_error(request.method, error.status, str(error))
    path = p.resource_path(requested)
    try:
      file = open_resource(self._root, path)
    except ResourceRefused as refusal:
      return self._refuse(request, path, refusal)
    with file:
      if p.names_a_folder(requested):
        return self._send_error(request.method, 404, p.explanation(404, request.target))
      size = os.fstat(file.fileno()).st_size
      self.sock.sendall(p.response_head(200, p.content_type(path), size))
      if request.method != "HEAD":
        self._send_body(file)
    return 200, size

  def _refuse(self, request, path, refusal):
    """Resposta de erro para um recurso que não pôde ser aberto, pelo motivo
    da recusa: caminho bloqueado é 403, recurso inexistente é 404 e falha de
    acesso a um recurso existente é 500, que não se confunde com ausência.
    Bloqueios e falhas de acesso ficam registrados no terminal."""
    status = p.REFUSAL_STATUS[refusal.code]
    if status != 404:
      self._log(f"[{self.name}] {refusal.label}: {request.target} (decodificado: {path!r}). {refusal.detail}")
    return self._send_error(request.method, status, p.explanation(status, request.target))

  def _send_error(self, method, status, explanation):
    """Envia a página de erro com seu tamanho exato em bytes; para HEAD, só
    o cabeçalho."""
    page = p.error_page(status, explanation)
    self.sock.sendall(p.response_head(status, p.CONTENT_TYPES["html"], len(page), p.EXTRA_HEADERS.get(status, ())))
    if method != "HEAD":
      self.sock.sendall(page)
    return status, len(page)

  def _send_body(self, file):
    """Envia o arquivo, aberto em modo binário, em blocos de BLOCK_SIZE
    bytes, sem decodificar nada: o corpo sai exatamente como está no disco,
    seja HTML ou imagem, e nunca é carregado inteiro na memória. Cada envio
    tem o prazo de envio: um cliente que para de ler perde só a própria
    conexão."""
    while block := file.read(BLOCK_SIZE):
      self.sock.sendall(block)

  def _receive_head(self):
    """Bytes da requisição até a primeira linha em branco (CRLF CRLF),
    exclusive, ou None se o cliente fechar a conexão antes dela. O que vier
    depois da linha em branco é ignorado.

    A requisição inteira precisa chegar dentro do prazo de recepção, contado
    desde o início, e caber em MAX_HEAD bytes. Levanta RequestError com 408
    quando o prazo expira no meio da requisição e com 431 quando ela passa
    do limite; se o prazo expira sem nenhum byte, levanta _NothingReceived,
    porque não há requisição a responder."""
    data = bytearray()
    deadline = time.monotonic() + self._receive_timeout
    too_large = f"A requisição passa do limite de {p.MAX_HEAD} bytes até a linha em branco."
    while (end := data.find(p.HEAD_END)) < 0:
      if len(data) >= p.MAX_HEAD + len(p.HEAD_END):
        raise p.RequestError(431, too_large, p.method_of(data))
      remaining = deadline - time.monotonic()
      if remaining <= 0:
        if not data:
          raise _NothingReceived()
        raise p.RequestError(408, f"A requisição não chegou completa em {self._receive_timeout:g} segundos.",
                             p.method_of(data))
      self.sock.settimeout(remaining)
      try:
        chunk = self.sock.recv(RECEIVE_SIZE)
      except TimeoutError:
        continue
      if not chunk:
        return None
      data += chunk
    self.sock.settimeout(self._send_timeout)
    if end > p.MAX_HEAD:
      raise p.RequestError(431, too_large, p.method_of(data))
    return bytes(data[:end])

  def _log_request(self, line, status, size, started):
    shown = "".join(char if char.isprintable() else repr(char)[1:-1] for char in line[:200])
    self._log(f"[{self.name}] {self.address} \"{shown}\" "
              f"{status} {size} bytes (início {_clock(started)}, fim {_clock(time.time())})")


class _NothingReceived(Exception):
  """O prazo de recepção expirou sem que nenhum byte da requisição chegasse."""


def _clock(moment):
  return time.strftime("%H:%M:%S", time.localtime(moment)) + f".{int(moment * 1000) % 1000:03d}"


class Server:
  def __init__(self, host=HOST, port=p.DEFAULT_PORT, *, log=terminal_log, root=ROOT, receive_timeout=RECEIVE_TIMEOUT,
               send_timeout=SEND_TIMEOUT):
    self._host = host
    self._port = port
    self.root = root
    self._log = log
    self._receive_timeout = receive_timeout
    self._send_timeout = send_timeout
    self._lock = threading.Lock()
    self._connections = set()
    self._started = []
    self._last_number = 0
    self._last_refusal = 0
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
    connection = Connection(sock, address, f"conexão {number}", root=self.root, log=self._log,
                            receive_timeout=self._receive_timeout, send_timeout=self._send_timeout,
                            on_close=self._end_connection)
    with self._lock:
      self._connections.add(connection)
    self._log(f"Conectado: {address} na thread {connection.name}")
    self._start(connection)

  def _start(self, connection):
    with self._lock:
      # Uma conexão sai do conjunto ao ser encerrada, mas o encerramento do
      # servidor ainda espera sua thread.
      self._started = [started for started in self._started if started.is_alive()] + [connection]
    connection.start()

  def _refuse_connection(self, sock, address):
    """Conexão excedente: uma thread curta, fora da contagem do limite, só
    envia a página de erro 503 e fecha; a recusa fica registrada."""
    with self._lock:
      self._last_refusal += 1
      number = self._last_refusal
    refusal = Connection(sock, address, f"recusa {number}", log=self._log, send_timeout=self._send_timeout,
                         refused=True)
    self._log(f"Recusado: {address} (limite de {MAX_CONNECTIONS} conexões simultâneas atingido)")
    self._start(refusal)

  def _end_connection(self, connection, reason):
    with self._lock:
      self._connections.discard(connection)
    self._log(f"Desconectado: {connection.address} na thread {connection.name} ({reason})")


def network_address():
  """Endereço IPv4 desta máquina na rede local, para abrir o site de outro
  dispositivo, ou None sem rede. Conectar um socket UDP não envia nenhum
  pacote: só faz o sistema escolher a interface de saída, cujo endereço é
  então lido."""
  probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  try:
    probe.connect(("10.255.255.255", 1))
    address = probe.getsockname()[0]
  except OSError:
    return None
  finally:
    probe.close()
  return None if address.startswith("127.") or address == "0.0.0.0" else address


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
    port = server.address[1]
    terminal_log(f"Servidor escutando na porta TCP {port}.")
    terminal_log(f"Pasta raiz: {server.root}")
    terminal_log(f"Neste computador: http://localhost:{port}/")
    address = network_address()
    terminal_log(f"Na rede: http://{address}:{port}/" if address else "Na rede: sem endereço de rede encontrado")
    terminal_log("Ctrl+C encerra.")
    server.wait()
  except KeyboardInterrupt:
    terminal_log("Ctrl+C recebido: encerrando o servidor")
  finally:
    server.shutdown()
  terminal_log("Servidor encerrado")


if __name__ == "__main__":
  main()

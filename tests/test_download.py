import hashlib
import os
import socket
import subprocess
import sys
import tempfile
import unittest

import protocol as p
from server import Server
from tests.support import LogRecorder, OutputReader, install_programs, receive_within, wait_until

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
SIZE_12_MIB = 12_582_912
PROMPT = "Escolha uma opção"
FILE_PROMPT = "Caminho do arquivo"
CONCLUDED = "Download concluído"


def sha256_of(path):
  digest = hashlib.sha256()
  with open(path, "rb") as file:
    while block := file.read(1024 * 1024):
      digest.update(block)
  return digest.hexdigest()


def files_under(root):
  """Caminhos relativos, com `/`, de todos os arquivos sob `root`."""
  return sorted(
    os.path.relpath(os.path.join(folder, name), root).replace(os.sep, "/")
    for folder, _, names in os.walk(root) for name in names
  )


def share(root, path, content):
  """Coloca um arquivo sob `root` e devolve seu SHA-256."""
  full = os.path.join(root, *path.split("/"))
  os.makedirs(os.path.dirname(full), exist_ok=True)
  with open(full, "wb") as file:
    file.write(content)
  return hashlib.sha256(content).hexdigest()


class ServerDownloadTest(unittest.TestCase):
  """Pedidos de arquivo a um servidor real, cuja raiz compartilhada é um
  diretório temporário."""

  def setUp(self):
    base = tempfile.TemporaryDirectory()
    self.addCleanup(base.cleanup)
    self.outside_root = base.name
    self.shared_root = os.path.join(base.name, "files")
    os.mkdir(self.shared_root)
    self.log = LogRecorder()
    self.server = Server("127.0.0.1", 0, log=self.log, shared_root=self.shared_root)
    self.server.start()
    self.addCleanup(self.server.shutdown)

  def connect(self, small_receive_buffer=False):
    """Com buffer de recepção pequeno, o servidor só avança alguns blocos
    além do que o cliente já leu."""
    sock = socket.socket()
    self.addCleanup(sock.close)
    if small_receive_buffer:
      sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    sock.settimeout(5)
    sock.connect(self.server.address)
    host, port = sock.getsockname()[:2]
    self.log.wait_for(f"Conectado: {host}:{port}")
    return sock

  def request(self, sock, path):
    sock.sendall(p.encode(p.FILE_REQUEST, {"path": path}))

  def share(self, path, content):
    return share(self.shared_root, path, content)

  def receive_download(self, sock, blocks=None):
    """Recebe FILE_META, os blocos e FILE_END de um pedido; devolve o tamanho
    anunciado, os blocos e o hash anunciado. Com `blocks`, o FILE_META e
    esses primeiros blocos já foram recebidos."""
    if blocks is None:
      meta = receive_within(sock)
      self.assertEqual(meta.type, p.FILE_META, meta)
      size = meta.content["size"]
      blocks = []
    else:
      size = None
    while (message := receive_within(sock)).type == p.FILE_DATA:
      blocks.append(message.content)
    self.assertEqual(message.type, p.FILE_END, message)
    return size, blocks, message.content["sha256"]

  def assertRefusedKeepingTheSession(self, contents, code):
    """Cada pedido recebe ERROR com `code` e a sessão continua aceitando
    operações: um arquivo existente é baixado em seguida."""
    sha256 = self.share("presente.txt", b"presente")
    client = self.connect()

    for content in contents:
      client.sendall(p.encode(p.FILE_REQUEST, content))
      error = receive_within(client)
      self.assertEqual(error.type, p.ERROR, error)
      self.assertEqual((error.content["operation"], error.content["code"]), ("FILE_REQUEST", code))
      self.assertTrue(error.content["message"])
    self.request(client, "presente.txt")

    self.assertEqual(self.receive_download(client), (8, [b"presente"], sha256))
    self.assertEqual(self.log.matching("Desconectado:"), [])

  def test_missing_file_gets_not_found_and_the_session_keeps_accepting_operations(self):
    self.assertRefusedKeepingTheSession([{"path": "ausente.txt"}, {"path": "pasta/ausente.txt"}], "NOT_FOUND")

  def test_valid_json_with_missing_or_invalid_fields_gets_invalid_request(self):
    contents = [{}, {"caminho": "presente.txt"}, {"path": None}, {"path": 7}, {"path": ["presente.txt"]},
                ["presente.txt"], "presente.txt", None]
    self.assertRefusedKeepingTheSession(contents, "INVALID_REQUEST")

  def test_forbidden_path_syntax_gets_path_forbidden_even_when_the_target_exists(self):
    with open(os.path.join(self.outside_root, "fora.txt"), "wb") as file:
      file.write(b"fora da raiz")
    self.share("aulas/resumo.pdf", b"resumo")
    forbidden = [
      "", "../fora.txt", "../../ausente.txt", "aulas/../aulas/resumo.pdf", "./aulas/resumo.pdf",
      "aulas/./resumo.pdf", "aulas//resumo.pdf", "aulas/", "/etc/passwd",
      os.path.join(self.outside_root, "fora.txt"), "C:/dados/a.txt", "C:\\dados\\a.txt", "aulas\\resumo.pdf",
      "aulas/resumo.pdf\x00", "nome\nquebrado", "tab\tulado", "escape\x1b",
    ]
    self.assertRefusedKeepingTheSession([{"path": path} for path in forbidden], "PATH_FORBIDDEN")

  def test_file_is_sent_as_meta_8_kib_blocks_and_end_with_its_sha256(self):
    content = os.urandom(2 * 8192 + 3616)
    sha256 = self.share("dados.bin", content)
    client = self.connect()

    self.request(client, "dados.bin")

    size, blocks, announced = self.receive_download(client)
    self.assertEqual(size, 20000)
    self.assertEqual([len(block) for block in blocks], [8192, 8192, 3616])
    self.assertEqual(b"".join(blocks), content)
    self.assertEqual(announced, sha256)

  def test_empty_file_is_sent_as_meta_with_size_zero_and_end_with_the_empty_hash(self):
    self.share("vazio.txt", b"")
    client = self.connect()

    self.request(client, "vazio.txt")

    self.assertEqual(self.receive_download(client), (0, [], EMPTY_SHA256))

  def test_each_block_is_read_and_hashed_when_sent_without_reading_the_file_beforehand(self):
    """O fim de um arquivo de 12 MiB muda depois que o cliente recebe o
    primeiro bloco. Os blocos e o hash recebidos refletem a mudança: o
    servidor não leu nem calculou o hash do arquivo inteiro antes de enviar."""
    original = os.urandom(SIZE_12_MIB)
    self.share("grande.bin", original)
    client = self.connect(small_receive_buffer=True)

    self.request(client, "grande.bin")
    self.assertEqual(receive_within(client), (p.FILE_META, {"size": SIZE_12_MIB}))
    first = receive_within(client)
    changed_tail = bytes(b ^ 0xFF for b in original[-p.CHUNK_SIZE:])
    with open(os.path.join(self.shared_root, "grande.bin"), "r+b") as file:
      file.seek(-p.CHUNK_SIZE, os.SEEK_END)
      file.write(changed_tail)

    _, blocks, announced = self.receive_download(client, [first.content])
    sent = original[:-p.CHUNK_SIZE] + changed_tail
    self.assertEqual(b"".join(blocks), sent)
    self.assertEqual(announced, hashlib.sha256(sent).hexdigest())



class ClientDownloadTest(unittest.TestCase):
  """O cliente roda como no terminal, a partir de uma cópia do programa em
  diretório temporário e com outro diretório corrente; o servidor é real e
  compartilha outro diretório temporário."""

  def setUp(self):
    base = tempfile.TemporaryDirectory()
    self.addCleanup(base.cleanup)
    self.shared_root = os.path.join(base.name, "servidor", "files")
    os.makedirs(self.shared_root)
    program = install_programs(os.path.join(base.name, "cliente"))
    self.downloads_root = os.path.join(program, "downloads")
    os.mkdir(self.downloads_root)
    self.working_dir = os.path.join(base.name, "outro")
    os.mkdir(self.working_dir)

    self.log = LogRecorder()
    self.server = Server("127.0.0.1", 0, log=self.log, shared_root=self.shared_root)
    self.server.start()
    self.addCleanup(self.server.shutdown)
    self.client = subprocess.Popen(
      [sys.executable, "-u", os.path.join(program, "client.py"), "127.0.0.1", str(self.server.address[1])],
      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=self.working_dir,
      encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    self.output = OutputReader(self.client.stdout)
    self.addCleanup(self.output.close)
    self.addCleanup(self.client.wait, 5)
    self.addCleanup(self.client.kill)
    self.addCleanup(self.client.stdin.close)

  def type_line(self, line):
    self.client.stdin.write(line + "\n")
    self.client.stdin.flush()

  def downloaded(self, path):
    return os.path.join(self.downloads_root, *path.split("/"))

  def download(self, path, timeout=10):
    """Escolhe Arquivo, informa o caminho e espera a conclusão anunciada."""
    concluded = self.output.text.count(CONCLUDED)
    self.type_line("2")
    self.type_line(path)
    if not wait_until(lambda: self.output.text.count(CONCLUDED) > concluded, timeout):
      raise AssertionError(f"download de {path!r} não concluído: {self.output.text!r}")

  def test_binary_file_of_12_mib_arrives_with_the_size_and_sha256_of_the_origin(self):
    sha256 = share(self.shared_root, "grande.bin", os.urandom(SIZE_12_MIB))

    self.download("grande.bin")

    self.assertEqual(os.path.getsize(self.downloaded("grande.bin")), SIZE_12_MIB)
    self.assertEqual(sha256_of(self.downloaded("grande.bin")), sha256)

  def test_missing_file_shows_the_not_found_error_and_the_session_keeps_accepting_operations(self):
    share(self.shared_root, "presente.txt", b"presente")

    self.type_line("2")
    self.type_line("ausente.txt")

    self.output.wait_for("Arquivo inexistente: ausente.txt (NOT_FOUND)")
    self.download("presente.txt")
    self.assertEqual(os.listdir(self.downloads_root), ["presente.txt"])
    self.assertEqual(self.log.matching("Desconectado:"), [])

  def test_empty_file_is_published_empty(self):
    share(self.shared_root, "vazio.txt", b"")

    self.download("vazio.txt")

    self.assertEqual(files_under(self.downloads_root), ["vazio.txt"])
    self.assertEqual(os.path.getsize(self.downloaded("vazio.txt")), 0)

  def test_files_with_any_name_are_published_preserving_their_subfolders(self):
    names = [
      "aulas/Revisão 2.pdf", "aulas/2024/notas finais da ação.txt", ".exemplo", "arquivo", "teste.bin",
      "dados.tar.gz", "pasta com espaço/.oculto sem extensão",
    ]
    shas = {name: share(self.shared_root, name, os.urandom(1000 + number)) for number, name in enumerate(names)}

    for name in names:
      self.download(name)

    self.assertEqual(files_under(self.downloads_root), sorted(names))
    for name in names:
      self.assertEqual(sha256_of(self.downloaded(name)), shas[name], name)


  def test_existing_object_at_the_destination_is_preserved_and_the_download_refused(self):
    for name in ("aulas/resumo.pdf", "pasta", "outro.txt"):
      share(self.shared_root, name, b"do servidor")
    share(self.downloads_root, "aulas/resumo.pdf", b"meu resumo")
    os.makedirs(self.downloaded("pasta/dentro"))

    for name in ("aulas/resumo.pdf", "pasta"):
      self.type_line("2")
      self.type_line(name)
    self.download("outro.txt")

    self.assertEqual(self.output.text.count("já existe"), 2)
    self.assertEqual(self.output.text.count(CONCLUDED), 1)
    self.assertEqual(files_under(self.downloads_root), ["aulas/resumo.pdf", "outro.txt"])
    with open(self.downloaded("aulas/resumo.pdf"), "rb") as file:
      self.assertEqual(file.read(), b"meu resumo")
    self.assertEqual(os.listdir(self.downloaded("pasta")), ["dentro"])



class ScriptedServerDownloadTest(unittest.TestCase):
  """O cliente roda como no terminal, a partir de uma cópia do programa, e o
  teste faz o papel do servidor na conexão TCP local, controlando o que é
  enviado e quando."""

  def setUp(self):
    base = tempfile.TemporaryDirectory()
    self.addCleanup(base.cleanup)
    program = install_programs(os.path.join(base.name, "cliente"))
    self.downloads_root = os.path.join(program, "downloads")
    os.mkdir(self.downloads_root)

    listener = socket.create_server(("127.0.0.1", 0))
    self.addCleanup(listener.close)
    listener.settimeout(5)
    self.client = subprocess.Popen(
      [sys.executable, "-u", os.path.join(program, "client.py"), "127.0.0.1", str(listener.getsockname()[1])],
      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=base.name,
      encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    self.output = OutputReader(self.client.stdout)
    self.addCleanup(self.output.close)
    self.addCleanup(self.client.wait, 5)
    self.addCleanup(self.client.kill)
    self.addCleanup(self.client.stdin.close)
    self.server_side, _ = listener.accept()
    self.addCleanup(self.server_side.close)

  def type_line(self, line):
    self.client.stdin.write(line + "\n")
    self.client.stdin.flush()

  def downloaded(self, path):
    return os.path.join(self.downloads_root, *path.split("/"))

  def request(self, path):
    """Escolhe Arquivo e informa o caminho; o pedido chega ao servidor."""
    self.type_line("2")
    self.type_line(path)
    self.assertEqual(receive_within(self.server_side), (p.FILE_REQUEST, {"path": path}))

  def send(self, msg_type, content):
    self.server_side.sendall(p.encode(msg_type, content))

  def serve(self, size, blocks, sha256):
    self.send(p.FILE_META, {"size": size})
    for block in blocks:
      self.send(p.FILE_DATA, block)
    self.send(p.FILE_END, {"sha256": sha256})

  def test_size_or_hash_mismatch_discards_the_temporary_file_and_publishes_nothing(self):
    abc = hashlib.sha256(b"abc").hexdigest()
    mismatches = [
      ("faltando.bin", 4, [b"abc"], abc),
      ("sobrando.bin", 2, [b"abc"], abc),
      ("hash.bin", 3, [b"abc"], hashlib.sha256(b"abd").hexdigest()),
      ("aulas/hash invalido.bin", 3, [b"abc"], "nao e um hash"),
    ]

    for path, size, blocks, sha256 in mismatches:
      self.request(path)
      self.serve(size, blocks, sha256)
      self.output.wait_for(f"Download de {path} falhou")

    self.assertEqual(files_under(self.downloads_root), [])
    self.assertNotIn(CONCLUDED, self.output.text)
    self.request("certo.bin")
    self.serve(3, [b"abc"], abc)
    self.output.wait_for(CONCLUDED)
    self.assertEqual(files_under(self.downloads_root), ["certo.bin"])


if __name__ == "__main__":
  unittest.main()

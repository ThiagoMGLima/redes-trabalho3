import hashlib
import os
import socket
import tempfile
import unittest

import protocol as p
from server import Server
from tests.support import LogRecorder, receive_within

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
SIZE_12_MIB = 12_582_912


class ServerDownloadTest(unittest.TestCase):
  """Pedidos de arquivo a um servidor real, cuja raiz compartilhada é um
  diretório temporário."""

  def setUp(self):
    shared = tempfile.TemporaryDirectory()
    self.addCleanup(shared.cleanup)
    self.shared_root = shared.name
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
    """Coloca um arquivo na raiz compartilhada e devolve seu SHA-256."""
    full = os.path.join(self.shared_root, *path.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as file:
      file.write(content)
    return hashlib.sha256(content).hexdigest()

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


if __name__ == "__main__":
  unittest.main()

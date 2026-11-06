"""Aceitação do conjunto com os programas reais, pela fronteira TCP.

O servidor é real, com raiz compartilhada temporária, e cada cliente roda como
no terminal, a partir de uma cópia própria do programa. Cada cliente chega ao
servidor por um repasse que decide quantos bytes do servidor lhe são
entregues: o teste segura cada download no meio pelo tempo que quiser, sem
depender de um arquivo terminar rápido ou devagar.
"""
import os
import subprocess
import sys
import tempfile
import unittest

import protocol as p
from server import Server
from tests.support import LogRecorder, OutputReader, install_programs, wait_until
from tests.test_download import CONCLUDED, EMPTY_SHA256, SIZE_12_MIB, files_under, sha256_of, share
from tests.test_interruptions import Relay, whole_frames


class TerminalClient:
  """Cliente rodando como no terminal a partir de uma cópia do programa em
  `folder`, conectado ao servidor por um `Relay`."""

  def __init__(self, test, folder, server_address):
    program = install_programs(folder)
    self.downloads_root = os.path.join(program, "downloads")
    os.mkdir(self.downloads_root)
    self.relay = Relay(test, server_address)
    self.process = subprocess.Popen(
      [sys.executable, "-u", os.path.join(program, "client.py"), *map(str, self.relay.address)],
      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=folder,
      encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    self.output = OutputReader(self.process.stdout)
    test.addCleanup(self.output.close)
    test.addCleanup(self.process.wait, 5)
    test.addCleanup(self.process.kill)
    test.addCleanup(self.process.stdin.close)
    self.name = self.relay.wait_connected()

  def type_line(self, line):
    self.process.stdin.write(line + "\n")
    self.process.stdin.flush()

  def request(self, path):
    """Escolhe Arquivo e informa o caminho."""
    self.type_line("2")
    self.type_line(path)

  def downloaded(self, path):
    return os.path.join(self.downloads_root, *path.split("/"))

  def written(self):
    """Bytes gravados até agora em cada arquivo sob a raiz de downloads."""
    return {name: os.path.getsize(self.downloaded(name)) for name in files_under(self.downloads_root)}

  def wait_for_temporary_with(self, size):
    """Espera um temporário com exatamente `size` bytes: o download começou e
    a receptora já gravou tudo o que o repasse deixou passar."""
    if not wait_until(lambda: size in self.written().values()):
      raise AssertionError(f"nenhum temporário com {size} bytes: {self.written()}")

  def wait_for_conclusion(self, path, timeout=10):
    self.output.wait_for(f"{CONCLUDED}: {path} publicado", timeout)


class TerminalAcceptanceTest(unittest.TestCase):

  def setUp(self):
    base = tempfile.TemporaryDirectory()
    self.addCleanup(base.cleanup)
    self.base = base.name
    self.shared_root = os.path.join(self.base, "servidor", "files")
    os.makedirs(self.shared_root)
    self.log = LogRecorder()
    # Prazo de envio longo: um download segurado pelo repasse não expira
    # enquanto o teste o mantém parado.
    self.server = Server("127.0.0.1", 0, log=self.log, shared_root=self.shared_root, send_timeout=60)
    self.server.start()
    self.addCleanup(self.server.shutdown)

  def start_client(self, name):
    client = TerminalClient(self, os.path.join(self.base, name), self.server.address)
    self.log.wait_for(f"Conectado: {client.name}")
    return client

  def assertPublishedAsTheOrigin(self, client, path, size, sha256):
    self.assertEqual(os.path.getsize(client.downloaded(path)), size, path)
    self.assertEqual(sha256_of(client.downloaded(path)), sha256, path)

  def test_two_clients_download_12_mib_at_the_same_time_and_publish_them_with_the_size_and_sha256_of_the_origin(self):
    """Os dois downloads de 12 MiB ficam ativos e parados no meio ao mesmo
    tempo. O segundo termina, e o mesmo cliente baixa o arquivo vazio,
    enquanto o primeiro continua parado; depois o primeiro também termina."""
    first_sha256 = share(self.shared_root, "primeiro/grande.bin", os.urandom(SIZE_12_MIB))
    second_sha256 = share(self.shared_root, "segundo/grande.bin", os.urandom(SIZE_12_MIB))
    share(self.shared_root, "vazio.txt", b"")
    first, second = self.start_client("cliente-a"), self.start_client("cliente-b")

    for client, path in ((first, "primeiro/grande.bin"), (second, "segundo/grande.bin")):
      client.relay.allow(whole_frames(SIZE_12_MIB, 2))
      client.request(path)
    for client in (first, second):
      client.wait_for_temporary_with(2 * p.CHUNK_SIZE)

    second.relay.allow()
    second.wait_for_conclusion("segundo/grande.bin")
    second.request("vazio.txt")
    second.wait_for_conclusion("vazio.txt")
    self.assertEqual(list(first.written().values()), [2 * p.CHUNK_SIZE])
    self.assertNotIn(CONCLUDED, first.output.text)

    first.relay.allow()
    first.wait_for_conclusion("primeiro/grande.bin")
    self.assertEqual(files_under(first.downloads_root), ["primeiro/grande.bin"])
    self.assertEqual(files_under(second.downloads_root), ["segundo/grande.bin", "vazio.txt"])
    self.assertPublishedAsTheOrigin(first, "primeiro/grande.bin", SIZE_12_MIB, first_sha256)
    self.assertPublishedAsTheOrigin(second, "segundo/grande.bin", SIZE_12_MIB, second_sha256)
    self.assertPublishedAsTheOrigin(second, "vazio.txt", 0, EMPTY_SHA256)
    self.assertEqual(self.log.matching("Desconectado:"), [])


if __name__ == "__main__":
  unittest.main()

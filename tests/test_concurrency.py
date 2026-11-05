"""Downloads concorrentes com chat e exclusividade.

O andamento das transferências é controlado pelo teste: um participante com
buffer de recepção pequeno que para de ler segura o próprio download no meio,
porque um arquivo de 12 MiB não cabe nos buffers do sistema. Nenhum cenário
depende de o arquivo terminar rápido ou devagar.
"""
import hashlib
import os
import socket
import tempfile
import unittest

import protocol as p
from server import Server
from tests.support import LogRecorder, receive_within
from tests.test_download import SIZE_12_MIB, share


class ServerConcurrencyTest(unittest.TestCase):
  """Participantes conectados diretamente por TCP a um servidor real, cuja
  raiz compartilhada é um diretório temporário."""

  def setUp(self):
    base = tempfile.TemporaryDirectory()
    self.addCleanup(base.cleanup)
    self.shared_root = base.name
    self.log = LogRecorder()
    self.server = Server("127.0.0.1", 0, log=self.log, shared_root=self.shared_root)
    self.server.start()
    self.addCleanup(self.server.shutdown)

  def connect(self, small_receive_buffer=False):
    sock = socket.socket()
    self.addCleanup(sock.close)
    if small_receive_buffer:
      sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    sock.settimeout(5)
    sock.connect(self.server.address)
    host, port = sock.getsockname()[:2]
    self.log.wait_for(f"Conectado: {host}:{port}")
    return sock

  def share_large(self, path):
    """Arquivo de 12 MiB: devolve o conteúdo e seu SHA-256."""
    content = os.urandom(SIZE_12_MIB)
    return content, share(self.shared_root, path, content)

  def receive_through(self, sock, download, last_type):
    """Lê até uma mensagem do tipo `last_type`, inclusive, juntando os blocos
    em download["blocks"] e as mensagens que chegam entre eles em
    download["others"], na ordem de chegada. Devolve a última mensagem."""
    while True:
      message = receive_within(sock)
      if message.type == p.FILE_DATA:
        download["blocks"].append(message.content)
      elif message.type != last_type:
        download["others"].append(message)
      if message.type == last_type:
        return message

  def receive_meta_and_a_block(self, sock, size=SIZE_12_MIB):
    """Depois de um pedido, o FILE_META chega antes de qualquer bloco; lê até
    o primeiro bloco e para. Devolve o download em andamento."""
    self.assertEqual(receive_within(sock), (p.FILE_META, {"size": size}))
    download = {"blocks": [], "others": []}
    self.receive_through(sock, download, p.FILE_DATA)
    return download

  def finish_download(self, sock, download):
    """Lê até o FILE_END; devolve os bytes recebidos e o hash anunciado."""
    end = self.receive_through(sock, download, p.FILE_END)
    return b"".join(download["blocks"]), end.content["sha256"]

  def test_overlapping_requests_get_busy_and_the_current_download_arrives_intact(self):
    """Um pedido colado ao primeiro, antes do FILE_META, e outro no meio da
    transferência recebem BUSY; o download atual chega íntegro e, depois do
    FILE_END, a sessão aceita um novo pedido."""
    original, sha256 = self.share_large("grande.bin")
    outro_sha256 = share(self.shared_root, "outro.txt", b"outro")
    client = self.connect(small_receive_buffer=True)

    client.sendall(p.encode(p.FILE_REQUEST, {"path": "grande.bin"}) + p.encode(p.FILE_REQUEST, {"path": "outro.txt"}))
    download = self.receive_meta_and_a_block(client)
    client.sendall(p.encode(p.FILE_REQUEST, {"path": "outro.txt"}))
    received, announced = self.finish_download(client, download)

    others = download["others"]
    self.assertEqual([(m.type, m.content["operation"], m.content["code"]) for m in others],
                     [(p.ERROR, "FILE_REQUEST", "BUSY")] * 2)
    self.assertTrue(all(m.content["message"] for m in others))
    self.assertEqual(len(received), SIZE_12_MIB)
    self.assertEqual(received, original)
    self.assertEqual(announced, sha256)

    client.sendall(p.encode(p.FILE_REQUEST, {"path": "outro.txt"}))
    second = self.receive_meta_and_a_block(client, size=5)
    self.assertEqual(self.finish_download(client, second), (b"outro", outro_sha256))
    self.assertEqual(second["others"], [])
    self.assertEqual(self.log.matching("Desconectado:"), [])


if __name__ == "__main__":
  unittest.main()

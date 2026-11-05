"""Downloads concorrentes com chat e exclusividade.

O andamento das transferências é controlado pelo teste: um participante com
buffer de recepção pequeno que para de ler segura o próprio download no meio,
porque um arquivo de 12 MiB não cabe nos buffers do sistema. Nenhum cenário
depende de o arquivo terminar rápido ou devagar.
"""
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
from tests.test_download import CONCLUDED, SIZE_12_MIB, files_under, share

ACTIVE_DOWNLOAD = "Já existe um download ativo"


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
    """Compartilha um arquivo de 12 MiB e devolve seu SHA-256."""
    return share(self.shared_root, path, os.urandom(SIZE_12_MIB))

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

  def start_held_download(self, path):
    """Participante que pede `path`, recebe FILE_META e um bloco e para de
    ler: o download fica ativo, preso no meio, até o teste voltar a ler."""
    sock = self.connect(small_receive_buffer=True)
    sock.sendall(p.encode(p.FILE_REQUEST, {"path": path}))
    return sock, self.receive_meta_and_a_block(sock)

  def assertIntact(self, finished, origin_sha256):
    """Tamanho e SHA-256 dos bytes recebidos iguais aos da origem, e o hash
    anunciado no FILE_END igual ao da origem."""
    received, announced = finished
    self.assertEqual(len(received), SIZE_12_MIB)
    self.assertEqual(hashlib.sha256(received).hexdigest(), origin_sha256)
    self.assertEqual(announced, origin_sha256)

  def test_two_clients_download_at_the_same_time_and_both_files_arrive_intact(self):
    """Os dois downloads ficam ativos e presos no meio ao mesmo tempo. O
    segundo termina enquanto o primeiro continua preso, e depois o primeiro
    também termina."""
    first_sha256 = self.share_large("primeiro.bin")
    second_sha256 = self.share_large("segundo.bin")
    first, first_download = self.start_held_download("primeiro.bin")
    second, second_download = self.start_held_download("segundo.bin")

    self.assertIntact(self.finish_download(second, second_download), second_sha256)
    self.assertIntact(self.finish_download(first, first_download), first_sha256)
    self.assertEqual(first_download["others"] + second_download["others"], [])

  def test_chat_and_broadcast_sent_during_the_transfers_arrive_before_each_file_end(self):
    """Com os dois downloads presos no meio, cada participante conversa
    durante o próprio download e o operador envia um broadcast; cada um
    recebe o chat do outro e o broadcast antes do próprio FILE_END."""
    first_sha256 = self.share_large("primeiro.bin")
    second_sha256 = self.share_large("segundo.bin")
    first, first_download = self.start_held_download("primeiro.bin")
    second, second_download = self.start_held_download("segundo.bin")

    first.sendall(p.encode(p.CHAT, {"text": "baixando o primeiro 😀"}))
    second.sendall(p.encode(p.CHAT, {"text": "baixando o segundo"}))
    self.log.wait_for("[Cliente 1] baixando o primeiro 😀")
    self.log.wait_for("[Cliente 2] baixando o segundo")
    self.server.broadcast("aviso durante os downloads")

    self.assertIntact(self.finish_download(first, first_download), first_sha256)
    self.assertIntact(self.finish_download(second, second_download), second_sha256)
    broadcast = (p.CHAT, {"origin": "Servidor", "text": "aviso durante os downloads"})
    self.assertCountEqual(first_download["others"],
                          [(p.CHAT, {"origin": "Cliente 2", "text": "baixando o segundo"}), broadcast])
    self.assertCountEqual(second_download["others"],
                          [(p.CHAT, {"origin": "Cliente 1", "text": "baixando o primeiro 😀"}), broadcast])

  def test_overlapping_requests_get_busy_and_the_current_download_arrives_intact(self):
    """Um pedido colado ao primeiro, antes do FILE_META, e outro no meio da
    transferência recebem BUSY; o download atual chega íntegro e, depois do
    FILE_END, a sessão aceita um novo pedido."""
    sha256 = self.share_large("grande.bin")
    outro_sha256 = share(self.shared_root, "outro.txt", b"outro")
    client = self.connect(small_receive_buffer=True)

    client.sendall(p.encode(p.FILE_REQUEST, {"path": "grande.bin"}) + p.encode(p.FILE_REQUEST, {"path": "outro.txt"}))
    download = self.receive_meta_and_a_block(client)
    client.sendall(p.encode(p.FILE_REQUEST, {"path": "outro.txt"}))
    finished = self.finish_download(client, download)

    others = download["others"]
    self.assertEqual([(m.type, m.content["operation"], m.content["code"]) for m in others],
                     [(p.ERROR, "FILE_REQUEST", "BUSY")] * 2)
    self.assertTrue(all(m.content["message"] for m in others))
    self.assertIntact(finished, sha256)

    client.sendall(p.encode(p.FILE_REQUEST, {"path": "outro.txt"}))
    second = self.receive_meta_and_a_block(client, size=5)
    self.assertEqual(self.finish_download(client, second), (b"outro", outro_sha256))
    self.assertEqual(second["others"], [])
    self.assertEqual(self.log.matching("Desconectado:"), [])


class ScriptedServerClientTest(unittest.TestCase):
  """O cliente roda como no terminal, a partir de uma cópia do programa, e o
  teste faz o papel do servidor na conexão TCP local, decidindo o que é
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

  def send(self, msg_type, content):
    self.server_side.sendall(p.encode(msg_type, content))

  def request(self, path):
    """Escolhe Arquivo e informa o caminho; o pedido chega ao servidor."""
    self.type_line("2")
    self.type_line(path)
    self.assertEqual(receive_within(self.server_side), (p.FILE_REQUEST, {"path": path}))

  def chat(self, text):
    """Escolhe Chat e envia `text`; a mensagem chega ao servidor."""
    self.type_line("3")
    self.type_line(text)
    self.assertEqual(receive_within(self.server_side), (p.CHAT, {"text": text}))

  def wait_for_received_bytes(self, size):
    """Espera o temporário do download ter `size` bytes gravados: a receptora
    já processou tudo o que foi enviado até ali."""
    def written():
      return sum(os.path.getsize(os.path.join(self.downloads_root, path)) for path in files_under(self.downloads_root))
    if not wait_until(lambda: written() == size):
      raise AssertionError(f"{size} bytes não foram gravados: {files_under(self.downloads_root)}")

  def assertSecondRequestBlockedLocally(self, phase):
    """Arquivo é recusado localmente, sem pedir caminho nem enviar nada: a
    próxima mensagem que chega ao servidor é o chat enviado em seguida."""
    refusals = self.output.text.count(ACTIVE_DOWNLOAD)
    self.type_line("2")
    if not wait_until(lambda: self.output.text.count(ACTIVE_DOWNLOAD) > refusals):
      raise AssertionError(f"segundo pedido não recusado com download {phase}: {self.output.text!r}")
    self.chat(f"menu livre com download {phase}")

  def test_second_request_is_blocked_locally_while_pending_transferring_or_in_the_final_check(self):
    content = os.urandom(2 * p.CHUNK_SIZE)
    self.request("primeiro.bin")
    self.assertSecondRequestBlockedLocally("pendente")

    self.send(p.FILE_META, {"size": len(content)})
    self.send(p.FILE_DATA, content[:p.CHUNK_SIZE])
    self.wait_for_received_bytes(p.CHUNK_SIZE)
    self.assertSecondRequestBlockedLocally("em transferência")

    self.send(p.FILE_DATA, content[p.CHUNK_SIZE:])
    self.wait_for_received_bytes(len(content))
    self.assertSecondRequestBlockedLocally("aguardando o FILE_END da verificação final")

    self.send(p.FILE_END, {"sha256": hashlib.sha256(content).hexdigest()})
    self.output.wait_for(CONCLUDED)
    self.request("segundo.bin")


if __name__ == "__main__":
  unittest.main()

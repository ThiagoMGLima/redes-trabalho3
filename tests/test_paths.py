"""Proteção de caminhos: a raiz compartilhada no servidor e a raiz de
downloads no cliente, pela fronteira TCP e com diretórios temporários."""
import hashlib
import os
import select
import socket
import subprocess
import sys
import tempfile
import unittest

import protocol as p
from server import Server
from tests.support import (CAN_EXCHANGE, LogRecorder, OutputReader, Swapper, install_programs, receive_within,
                           wait_until)
from tests.test_download import CONCLUDED, files_under, share

FORBIDDEN = "Caminho inválido ou proibido"


class ContainmentTest(unittest.TestCase):
  """Contenção na raiz autorizada pela relação entre diretórios, depois de
  resolver os caminhos."""

  def setUp(self):
    base = tempfile.TemporaryDirectory()
    self.addCleanup(base.cleanup)
    self.base = base.name
    self.root = os.path.join(self.base, "files")
    os.makedirs(os.path.join(self.root, "aulas"))
    os.makedirs(os.path.join(self.base, "files-outro"))

  def test_root_and_whatever_lies_under_it_are_inside(self):
    for target in ("", "aulas", "aulas/resumo.pdf", "ainda/nao/existe.txt", "files-outro"):
      p.check_inside(self.root, os.path.join(self.root, *target.split("/")))
    p.check_inside(self.root + os.sep, os.path.join(self.root, "aulas"))

  def test_sibling_folder_whose_name_starts_like_the_root_is_not_inside(self):
    outside = ["files-outro", "files-outro/segredo.txt", "filesx.txt", "segredo.txt", ""]
    for target in outside:
      with self.subTest(target=target), self.assertRaises(p.ForbiddenPath):
        p.check_inside(self.root, os.path.join(self.base, *target.split("/")))

  def test_resolved_path_that_leaves_the_root_through_a_link_is_not_inside(self):
    os.symlink(os.path.join(self.base, "files-outro"), os.path.join(self.root, "irma"))
    with self.assertRaises(p.ForbiddenPath):
      p.check_inside(self.root, os.path.join(self.root, "irma", "segredo.txt"))


class SharedRootTest(unittest.TestCase):
  """Pedidos a um servidor real cuja raiz compartilhada, `files/`, fica em um
  diretório temporário ao lado de uma pasta irmã de nome parecido,
  `files-outro/`, que não é compartilhada. A raiz sempre tem o arquivo
  regular `presente.txt`."""

  def setUp(self):
    base = tempfile.TemporaryDirectory()
    self.addCleanup(base.cleanup)
    self.base = base.name
    self.shared_root = os.path.join(self.base, "files")
    os.mkdir(self.shared_root)
    share(self.shared_root, "presente.txt", b"presente")
    self.sibling = os.path.join(self.base, "files-outro")
    share(self.sibling, "segredo.txt", b"segredo da pasta irma")
    self.log = LogRecorder()

  def start_server(self, shared_root=None):
    self.server = Server("127.0.0.1", 0, log=self.log, shared_root=shared_root or self.shared_root)
    self.server.start()
    self.addCleanup(self.server.shutdown)

  def connect(self):
    sock = socket.create_connection(self.server.address, timeout=5)
    self.addCleanup(sock.close)
    host, port = sock.getsockname()[:2]
    self.log.wait_for(f"Conectado: {host}:{port}")
    return sock

  def shared(self, path):
    return os.path.join(self.shared_root, *path.split("/"))

  def link(self, path, target):
    """Cria em `files/` um link simbólico `path` que aponta para `target`."""
    os.symlink(target, self.shared(path))

  def receive_whole_file(self, sock):
    meta = receive_within(sock)
    self.assertEqual(meta.type, p.FILE_META, meta)
    blocks = []
    while (message := receive_within(sock)).type == p.FILE_DATA:
      blocks.append(message.content)
    self.assertEqual(message.type, p.FILE_END, message)
    return b"".join(blocks)

  def assertRefusedKeepingTheSession(self, paths, code, present="presente.txt"):
    """Cada pedido recebe ERROR com `code` e nenhum conteúdo; a sessão
    continua aceitando operações: o arquivo `present` é baixado em seguida."""
    client = self.connect()

    for path in paths:
      client.sendall(p.encode(p.FILE_REQUEST, {"path": path}))
      error = receive_within(client)
      self.assertEqual(error.type, p.ERROR, (path, error))
      self.assertEqual((error.content["operation"], error.content["code"]), ("FILE_REQUEST", code), path)
      self.assertTrue(error.content["message"])
    client.sendall(p.encode(p.FILE_REQUEST, {"path": present}))

    with open(self.shared(present), "rb") as file:
      self.assertEqual(self.receive_whole_file(client), file.read())
    self.assertEqual(self.log.matching("Desconectado:"), [])

  def test_examples_of_the_path_policy_are_accepted_or_forbidden_as_agreed(self):
    """Exemplos da resolução da #3: os aceitos são baixados; os proibidos
    recebem PATH_FORBIDDEN mesmo quando o alvo existe ou não existe."""
    accepted = {"aulas/Revisão 2.pdf": b"revisao", ".exemplo": b"oculto", "arquivo": b"sem extensao",
                "teste.bin": bytes(range(256))}
    for path, content in accepted.items():
      share(self.shared_root, path, content)
    share(self.shared_root, "resumo.pdf", b"resumo")
    share(self.base, "segredo.txt", b"fora da raiz")
    forbidden = ["../../segredo.txt", "../segredo.txt", "aulas/../resumo.pdf", "/etc/passwd", "C:/dados/a.txt",
                 "C:\\dados\\a.txt", "", "./arquivo", "aulas//resumo.pdf", "aulas/", "arquivo\x00", "arq\nuivo"]
    self.start_server()

    self.assertRefusedKeepingTheSession(forbidden, p.PATH_FORBIDDEN)
    client = self.connect()
    for path, content in accepted.items():
      client.sendall(p.encode(p.FILE_REQUEST, {"path": path}))
      self.assertEqual(self.receive_whole_file(client), content, path)

  def test_symbolic_link_as_the_file_is_forbidden_wherever_it_points(self):
    share(self.shared_root, "aulas/resumo.pdf", b"resumo")
    self.link("interno.txt", "presente.txt")
    self.link("aulas/interno.pdf", os.path.join(self.shared_root, "aulas", "resumo.pdf"))
    self.link("externo.txt", os.path.join(self.sibling, "segredo.txt"))
    self.link("relativo.txt", "../files-outro/segredo.txt")
    self.link("quebrado.txt", "nao-existe.txt")
    self.start_server()

    links = ["interno.txt", "aulas/interno.pdf", "externo.txt", "relativo.txt", "quebrado.txt"]
    self.assertRefusedKeepingTheSession(links, p.PATH_FORBIDDEN)

  def test_symbolic_link_as_an_intermediate_folder_is_forbidden_before_absence_is_considered(self):
    """Um link usado como pasta é proibido mesmo que leve a um arquivo
    existente, inclusive na pasta irmã de nome parecido, ou que não leve a
    nada: a proibição vem antes de classificar o arquivo como ausente."""
    share(self.shared_root, "aulas/2024/resumo.pdf", b"resumo")
    self.link("atalho", "aulas")
    self.link("aulas/ano", os.path.join(self.shared_root, "aulas", "2024"))
    self.link("irma", "../files-outro")
    self.link("quebrado", "nao-existe")

    self.start_server()

    paths = ["atalho/2024/resumo.pdf", "aulas/ano/resumo.pdf", "irma/segredo.txt", "atalho/ausente.txt",
             "quebrado/ausente.txt"]
    self.assertRefusedKeepingTheSession(paths, p.PATH_FORBIDDEN)

  @unittest.skipUnless(hasattr(os, "mkfifo") and hasattr(socket, "AF_UNIX"), "requer FIFO e socket Unix")
  def test_folder_fifo_and_other_non_regular_objects_get_not_regular(self):
    """Nenhum deles é aberto: um FIFO sem escritor não bloqueia a sessão."""
    share(self.shared_root, "aulas/resumo.pdf", b"resumo")
    os.mkdir(self.shared("vazia"))
    os.mkfifo(self.shared("canal"))
    os.mkfifo(self.shared("aulas/canal"))
    unix = socket.socket(socket.AF_UNIX)
    self.addCleanup(unix.close)
    unix.bind(self.shared("tomada"))
    self.start_server()

    paths = ["aulas", "vazia", "canal", "aulas/canal", "tomada"]
    self.assertRefusedKeepingTheSession(paths, p.NOT_REGULAR)

  @unittest.skipIf(not hasattr(os, "geteuid") or os.geteuid() == 0, "root ignora as permissões de arquivo")
  def test_missing_permission_gets_access_error_not_not_found(self):
    """Sem permissão para ler o arquivo ou para atravessar a pasta, o pedido
    recebe ACCESS_ERROR, mesmo que o arquivo atrás da pasta não exista; uma
    proibição de sintaxe continua sendo PATH_FORBIDDEN."""
    share(self.shared_root, "fechado.txt", b"sem leitura")
    share(self.shared_root, "trancada/dentro.txt", b"atras da pasta")
    os.chmod(self.shared("fechado.txt"), 0)
    os.chmod(self.shared("trancada"), 0)
    self.addCleanup(os.chmod, self.shared("trancada"), 0o700)
    self.start_server()

    paths = ["fechado.txt", "trancada/dentro.txt", "trancada/ausente.txt"]
    self.assertRefusedKeepingTheSession(paths, p.ACCESS_ERROR)
    self.assertRefusedKeepingTheSession(["trancada/../presente.txt"], p.PATH_FORBIDDEN)

  def test_shared_root_that_is_a_symbolic_link_is_forbidden(self):
    """Mesmo apontando para uma pasta real com o arquivo pedido, a raiz que
    é um link recusa todo pedido, sem abrir nada; a sessão continua."""
    linked_root = os.path.join(self.base, "files-link")
    os.symlink(self.shared_root, linked_root)
    self.start_server(shared_root=linked_root)
    client = self.connect()

    for path in ["presente.txt", "ausente.txt", "presente.txt"]:
      client.sendall(p.encode(p.FILE_REQUEST, {"path": path}))
      error = receive_within(client)
      self.assertEqual(error.type, p.ERROR, (path, error))
      self.assertEqual(error.content["code"], p.PATH_FORBIDDEN, path)
    self.assertEqual(self.log.matching("Desconectado:"), [])


class DownloadsRootTest(unittest.TestCase):
  """O cliente roda como no terminal, a partir de uma cópia do programa cuja
  raiz de downloads, `downloads/`, fica ao lado de uma pasta irmã de nome
  parecido, `downloads-outro/`; o teste faz o papel do servidor na conexão
  TCP local, controlando o que é enviado e quando."""

  def setUp(self):
    base = tempfile.TemporaryDirectory()
    self.addCleanup(base.cleanup)
    self.base = base.name
    program = install_programs(os.path.join(self.base, "cliente"))
    self.downloads_root = os.path.join(program, "downloads")
    os.mkdir(self.downloads_root)
    self.sibling = os.path.join(program, "downloads-outro")
    os.mkdir(self.sibling)
    self.outside = os.path.join(self.base, "fora")
    os.mkdir(self.outside)

    listener = socket.create_server(("127.0.0.1", 0))
    self.addCleanup(listener.close)
    listener.settimeout(5)
    self.client = subprocess.Popen(
      [sys.executable, "-u", os.path.join(program, "client.py"), "127.0.0.1", str(listener.getsockname()[1])],
      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=self.base,
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

  def local(self, path):
    return os.path.join(self.downloads_root, *path.split("/"))

  def link(self, path, target):
    """Cria em `downloads/` um link simbólico `path` que aponta para `target`."""
    os.symlink(target, self.local(path))

  def request(self, path):
    """Escolhe Arquivo e informa o caminho; o pedido chega ao servidor."""
    self.type_line("2")
    self.type_line(path)
    self.assertEqual(receive_within(self.server_side), (p.FILE_REQUEST, {"path": path}))

  def serve(self, content):
    self.server_side.sendall(p.encode(p.FILE_META, {"size": len(content)}))
    if content:
      self.server_side.sendall(p.encode(p.FILE_DATA, content))
    self.server_side.sendall(p.encode(p.FILE_END, {"sha256": hashlib.sha256(content).hexdigest()}))

  def try_download(self, path, content):
    """Escolhe Arquivo e informa `path`. Se o pedido chegar ao servidor,
    serve `content` e espera o resultado anunciado. Devolve None para uma
    recusa local, True para "Download concluído" e False para falha."""
    refusals = lambda: self.output.text.count(FORBIDDEN) + self.output.text.count("Download recusado")
    before = refusals()
    self.type_line("2")
    self.type_line(path)
    sent = lambda: select.select([self.server_side], [], [], 0)[0]
    self.assertTrue(wait_until(lambda: refusals() > before or sent()), self.output.text)
    if not sent():
      return None
    self.assertEqual(receive_within(self.server_side), (p.FILE_REQUEST, {"path": path}))
    self.serve(content)
    concluded, failed = f"{CONCLUDED}: {path} publicado", f"Download de {path} falhou"
    self.assertTrue(wait_until(lambda: concluded in self.output.text or failed in self.output.text), self.output.text)
    return concluded in self.output.text

  def assertRefusedLocally(self, paths, refusal):
    """Cada caminho é recusado com `refusal` sem que o pedido seja enviado:
    o primeiro pedido que chega ao servidor é o de um caminho permitido, que
    em seguida é baixado normalmente."""
    for path in paths:
      self.type_line("2")
      self.type_line(path)
    self.request("permitido.txt")

    refusals = lambda: self.output.text.count(refusal)
    self.assertTrue(wait_until(lambda: refusals() == len(paths)), self.output.text)
    self.serve(b"permitido")
    self.output.wait_for(CONCLUDED)
    with open(self.local("permitido.txt"), "rb") as file:
      self.assertEqual(file.read(), b"permitido")

  def test_symbolic_link_as_a_download_folder_is_refused_locally_without_sending_the_request(self):
    os.mkdir(self.local("aulas"))
    self.link("interna", "aulas")
    self.link("aulas/externa", self.outside)
    self.link("irma", "../downloads-outro")
    self.link("quebrada", "nao-existe")

    paths = ["interna/resumo.pdf", "aulas/externa/resumo.pdf", "irma/resumo.pdf", "quebrada/resumo.pdf",
             "interna/nova/resumo.pdf"]
    self.assertRefusedLocally(paths, FORBIDDEN)
    self.assertEqual(files_under(self.outside) + files_under(self.sibling), [])

  def test_downloads_root_that_is_a_symbolic_link_is_refused_locally(self):
    """Mesmo apontando para uma pasta real, a raiz que é um link recusa todo
    pedido sem enviá-lo; o chat continua disponível."""
    os.rename(self.downloads_root, os.path.join(self.base, "real"))
    os.symlink(os.path.join(self.base, "real"), self.downloads_root)

    for path in ["resumo.pdf", "aulas/resumo.pdf"]:
      self.type_line("2")
      self.type_line(path)
    self.type_line("3")
    self.type_line("depois das recusas")

    self.assertEqual(receive_within(self.server_side), (p.CHAT, {"text": "depois das recusas"}))
    self.assertEqual(self.output.text.count(FORBIDDEN), 2)
    self.assertEqual(files_under(os.path.join(self.base, "real")), [])

  def test_link_that_appears_in_a_download_folder_after_the_request_fails_the_download_writing_nothing(self):
    """As pastas são verificadas de novo no FILE_META, ao criar as que
    faltam: um link que surge entre o pedido e o FILE_META faz o download
    falhar sem gravar nada fora da raiz. Um link que surge depois, durante
    a transferência, é coberto pelos testes de pasta e de temporário
    substituídos durante a transferência."""
    self.request("aulas/2024/resumo.pdf")
    self.link("aulas", self.outside)

    self.serve(b"conteudo")

    self.output.wait_for("Download de aulas/2024/resumo.pdf falhou")
    self.assertEqual(files_under(self.outside), [])
    self.assertNotIn(CONCLUDED, self.output.text)
    self.request("outro.txt")
    self.serve(b"outro")
    self.output.wait_for(CONCLUDED)
    self.assertEqual(files_under(self.downloads_root), ["outro.txt"])
    self.assertEqual(os.readlink(self.local("aulas")), self.outside)

  def start_transfer(self, path, content):
    """Pede `path` e envia FILE_META e o primeiro bloco de `content`; volta
    quando o temporário já existe na pasta do destino. Devolve o envio do
    restante e do FILE_END."""
    folder = os.path.dirname(self.local(path))
    present = len(os.listdir(folder)) if os.path.isdir(folder) else 0
    self.request(path)
    self.server_side.sendall(p.encode(p.FILE_META, {"size": len(content)}))
    self.server_side.sendall(p.encode(p.FILE_DATA, content[:p.CHUNK_SIZE]))
    self.assertTrue(wait_until(lambda: os.path.isdir(folder) and len(os.listdir(folder)) > present),
                    "temporário não criado")

    def finish():
      self.server_side.sendall(p.encode(p.FILE_DATA, content[p.CHUNK_SIZE:]))
      self.server_side.sendall(p.encode(p.FILE_END, {"sha256": hashlib.sha256(content).hexdigest()}))
    return finish

  def assertSessionContinues(self):
    """Outro download é pedido e concluído em seguida."""
    concluded = self.output.text.count(CONCLUDED)
    self.request("depois.txt")
    self.serve(b"depois")
    self.assertTrue(wait_until(lambda: self.output.text.count(CONCLUDED) > concluded), self.output.text)
    with open(self.local("depois.txt"), "rb") as file:
      self.assertEqual(file.read(), b"depois")

  def test_link_that_replaces_a_download_folder_during_the_transfer_fails_the_download_publishing_nothing(self):
    """Com o temporário já criado e antes do FILE_END, a pasta `aulas` é
    movida e um link para ela toma seu lugar: fora da raiz ou dentro dela.
    O download falha sem publicar nada através do link nem na pasta movida,
    o temporário é descartado e a sessão continua."""
    content = os.urandom(2 * p.CHUNK_SIZE)
    moves = [("aulas/externo.pdf", os.path.join(self.outside, "aulas")),
             ("aulas/interno.pdf", self.local("aulas-real"))]
    for path, moved in moves:
      os.mkdir(self.local("aulas"))
      finish = self.start_transfer(path, content)
      os.rename(self.local("aulas"), moved)
      self.link("aulas", moved)
      finish()
      self.output.wait_for(f"Download de {path} falhou")
      os.unlink(self.local("aulas"))

    self.assertNotIn(CONCLUDED, self.output.text)
    self.assertEqual(files_under(self.outside) + files_under(self.local("aulas-real")), [])
    self.assertSessionContinues()

  def test_temporary_replaced_by_a_link_during_the_transfer_publishes_nothing(self):
    """Com o temporário já criado e antes do FILE_END, um link para um
    arquivo de fora da raiz toma o lugar do temporário. O SHA-256 confere,
    porque é calculado sobre o que chegou pela conexão, mas o download
    falha: o destino não recebe o link nem o arquivo de fora, que fica
    intacto, e a sessão continua."""
    personal = os.path.join(self.outside, "pessoal.txt")
    with open(personal, "wb") as file:
      file.write(b"CONTEUDO PESSOAL, NAO VERIFICADO")
    os.mkdir(self.local("aulas"))
    finish = self.start_transfer("aulas/resumo.pdf", os.urandom(2 * p.CHUNK_SIZE))
    [temporary] = os.listdir(self.local("aulas"))
    os.symlink(personal, self.local("aulas/troca"))
    os.replace(self.local("aulas/troca"), self.local(f"aulas/{temporary}"))

    finish()

    self.output.wait_for("Download de aulas/resumo.pdf falhou")
    self.assertNotIn(CONCLUDED, self.output.text)
    self.assertFalse(os.path.lexists(self.local("aulas/resumo.pdf")))
    with open(personal, "rb") as file:
      self.assertEqual(file.read(), b"CONTEUDO PESSOAL, NAO VERIFICADO")
    self.assertEqual(os.stat(personal).st_nlink, 1)
    self.assertSessionContinues()

  def assertReceiverContinues(self):
    """A receptora segue exibindo o chat e o download deixou de estar ativo:
    outro é pedido e concluído em seguida."""
    self.server_side.sendall(p.encode(p.CHAT, {"origin": "Servidor", "text": "ainda recebendo"}))
    self.output.wait_for("[Servidor] ainda recebendo")
    self.assertSessionContinues()
    self.assertNotIn("Traceback", self.output.text)

  def test_temporary_removed_during_the_transfer_fails_the_download_and_ends_it(self):
    """Com o temporário já criado e antes do FILE_END, alguém o remove. O
    download falha com o motivo, sem conclusão, deixa de estar ativo e a
    receptora continua."""
    os.mkdir(self.local("aulas"))
    finish = self.start_transfer("aulas/resumo.pdf", os.urandom(2 * p.CHUNK_SIZE))
    [temporary] = os.listdir(self.local("aulas"))
    os.unlink(self.local(f"aulas/{temporary}"))

    finish()

    self.output.wait_for("Download de aulas/resumo.pdf falhou")
    self.assertNotIn(CONCLUDED, self.output.text)
    self.assertReceiverContinues()
    self.assertEqual(os.listdir(self.local("aulas")), [])

  @unittest.skipIf(not hasattr(os, "geteuid") or os.geteuid() == 0, "root ignora as permissões de arquivo")
  def test_destination_folder_closed_for_writing_during_the_transfer_fails_the_download_and_ends_it(self):
    """Com o temporário já criado e antes do FILE_END, a pasta do destino
    deixa de aceitar escrita. A publicação falha com o motivo, sem
    conclusão; o temporário que não pôde ser removido é informado; o
    download deixa de estar ativo e a receptora continua."""
    os.mkdir(self.local("aulas"))
    self.addCleanup(os.chmod, self.local("aulas"), 0o700)
    finish = self.start_transfer("aulas/resumo.pdf", os.urandom(2 * p.CHUNK_SIZE))
    [temporary] = os.listdir(self.local("aulas"))
    os.chmod(self.local("aulas"), 0o500)

    finish()

    self.output.wait_for("Download de aulas/resumo.pdf falhou")
    self.output.wait_for(f"Não foi possível remover o arquivo temporário {self.local(f'aulas/{temporary}')}")
    self.assertNotIn(CONCLUDED, self.output.text)
    self.assertFalse(os.path.lexists(self.local("aulas/resumo.pdf")))
    self.assertReceiverContinues()

  @unittest.skipIf(not hasattr(os, "geteuid") or os.geteuid() == 0, "root ignora as permissões de arquivo")
  def test_destination_folder_that_does_not_accept_the_temporary_fails_the_download_writing_nothing(self):
    """Depois do pedido, a pasta do destino deixa de aceitar escrita: o
    temporário não pode ser criado, nada é gravado, o download falha ao
    receber FILE_END e a receptora continua."""
    os.mkdir(self.local("aulas"))
    self.addCleanup(os.chmod, self.local("aulas"), 0o700)
    self.request("aulas/resumo.pdf")
    os.chmod(self.local("aulas"), 0o500)

    self.serve(b"conteudo")

    self.output.wait_for("Download de aulas/resumo.pdf falhou")
    self.assertNotIn(CONCLUDED, self.output.text)
    self.assertEqual(os.listdir(self.local("aulas")), [])
    self.assertReceiverContinues()

  @unittest.skipUnless(CAN_EXCHANGE, "requer renameat2 com RENAME_EXCHANGE")
  def test_download_folder_swapped_with_a_link_during_downloads_leaves_nothing_outside_the_root(self):
    """Uma thread troca sem parar a pasta `aulas` por um link para fora da
    raiz enquanto downloads em `aulas/2024/` são pedidos e atendidos. Cada
    um é recusado, falha ou conclui, mas nada é criado fora da raiz: nem
    pasta, nem temporário, nem arquivo publicado. O que foi anunciado como
    concluído está no destino, com o conteúdo verificado."""
    content = b"conteudo verificado"
    os.mkdir(self.local("aulas"))
    self.link("troca", self.outside)
    swapper = Swapper(self.local("aulas"), self.local("troca")).start()
    try:
      outcomes = [self.try_download(f"aulas/2024/{number}.bin", content) for number in range(60)]
    finally:
      swapper.stop()

    self.assertEqual(os.listdir(self.outside), [])
    for number, concluded in enumerate(outcomes):
      if concluded:
        with open(self.local(f"aulas/2024/{number}.bin"), "rb") as file:
          self.assertEqual(file.read(), content, number)

  def test_object_in_the_place_of_a_download_folder_is_preserved(self):
    """Antes do pedido, a recusa é local; depois dele, o download falha."""
    with open(self.local("aulas"), "wb") as file:
      file.write(b"arquivo, nao pasta")

    self.assertRefusedLocally(["aulas/resumo.pdf", "aulas/2024/resumo.pdf"], "já existe")
    self.request("notas/resumo.pdf")
    with open(self.local("notas"), "wb") as file:
      file.write(b"surgiu depois do pedido")
    self.serve(b"conteudo")

    self.output.wait_for("Download de notas/resumo.pdf falhou")
    self.assertEqual(self.output.text.count(CONCLUDED), 1)
    for name, content in [("aulas", b"arquivo, nao pasta"), ("notas", b"surgiu depois do pedido")]:
      with open(self.local(name), "rb") as file:
        self.assertEqual(file.read(), content)
    self.assertEqual(files_under(self.downloads_root), ["aulas", "notas", "permitido.txt"])

  def test_object_created_at_the_destination_during_the_transfer_is_preserved_without_announcing_completion(self):
    def create_file(destination):
      with open(destination, "wb") as file:
        file.write(b"criado durante")

    creations = [("aulas/arquivo.txt", create_file), ("aulas/pasta.txt", os.mkdir),
                 ("aulas/link.txt", lambda destination: os.symlink(self.outside, destination))]
    content = os.urandom(2 * p.CHUNK_SIZE)
    os.mkdir(self.local("aulas"))
    for path, create in creations:
      present = len(os.listdir(self.local("aulas")))
      self.request(path)
      self.server_side.sendall(p.encode(p.FILE_META, {"size": len(content)}))
      self.server_side.sendall(p.encode(p.FILE_DATA, content[:p.CHUNK_SIZE]))
      self.assertTrue(wait_until(lambda: len(os.listdir(self.local("aulas"))) > present), "temporário não criado")
      create(self.local(path))
      self.server_side.sendall(p.encode(p.FILE_DATA, content[p.CHUNK_SIZE:]))
      self.server_side.sendall(p.encode(p.FILE_END, {"sha256": hashlib.sha256(content).hexdigest()}))
      self.output.wait_for(f"Download de {path} falhou")

    self.assertNotIn(CONCLUDED, self.output.text)
    with open(self.local("aulas/arquivo.txt"), "rb") as file:
      self.assertEqual(file.read(), b"criado durante")
    self.assertEqual(os.listdir(self.local("aulas/pasta.txt")), [])
    self.assertEqual(os.readlink(self.local("aulas/link.txt")), self.outside)
    self.assertEqual(sorted(os.listdir(self.local("aulas"))), ["arquivo.txt", "link.txt", "pasta.txt"])
    self.assertEqual(files_under(self.outside), [])

  def test_destination_follows_only_the_requested_path_whatever_the_server_sends(self):
    content = b"conteudo pedido"
    elsewhere = {"path": "../../fora/outro.txt", "name": "outro.txt", "filename": "outro.txt",
                 "destination": os.path.join(self.outside, "outro.txt")}
    self.request("aulas/resumo.pdf")

    self.server_side.sendall(p.encode(p.FILE_META, {"size": len(content), **elsewhere}))
    self.server_side.sendall(p.encode(p.FILE_DATA, content))
    self.server_side.sendall(p.encode(p.FILE_END, {"sha256": hashlib.sha256(content).hexdigest(), **elsewhere}))

    self.output.wait_for(CONCLUDED)
    self.assertEqual(files_under(self.downloads_root), ["aulas/resumo.pdf"])
    with open(self.local("aulas/resumo.pdf"), "rb") as file:
      self.assertEqual(file.read(), content)
    self.assertEqual(files_under(self.outside) + files_under(self.sibling), [])

  @unittest.skipIf(not hasattr(os, "geteuid") or os.geteuid() == 0, "root ignora as permissões de arquivo")
  def test_download_folder_without_permission_refuses_the_download_without_ending_the_client(self):
    """Antes do pedido, a recusa é local; se a permissão faltar depois dele,
    o download falha ao criar a subpasta."""
    for folder in ("trancada", "somente-leitura"):
      os.mkdir(self.local(folder))
      self.addCleanup(os.chmod, self.local(folder), 0o700)
    os.chmod(self.local("trancada"), 0)

    self.request("somente-leitura/sub/resumo.pdf")
    os.chmod(self.local("somente-leitura"), 0o500)
    self.serve(b"conteudo")
    self.output.wait_for("Download de somente-leitura/sub/resumo.pdf falhou")

    self.assertRefusedLocally(["trancada/resumo.pdf", "trancada/sub/resumo.pdf"], "Download recusado")
    self.assertEqual(self.output.text.count(CONCLUDED), 1)
    self.assertNotIn("Traceback", self.output.text)

  def test_symbolic_link_at_the_destination_is_forbidden_and_preserved(self):
    with open(self.local("meu.txt"), "wb") as file:
      file.write(b"meu")
    self.link("interno.txt", "meu.txt")
    self.link("quebrado.txt", "nao-existe.txt")
    self.link("externo.txt", os.path.join(self.outside, "novo.txt"))

    self.assertRefusedLocally(["interno.txt", "quebrado.txt", "externo.txt"], FORBIDDEN)
    self.assertEqual(os.readlink(self.local("interno.txt")), "meu.txt")
    self.assertEqual(os.readlink(self.local("quebrado.txt")), "nao-existe.txt")
    self.assertEqual(files_under(self.outside), [])


if __name__ == "__main__":
  unittest.main()

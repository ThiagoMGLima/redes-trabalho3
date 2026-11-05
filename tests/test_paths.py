"""Proteção de caminhos: a raiz compartilhada no servidor e a raiz de
downloads no cliente, pela fronteira TCP e com diretórios temporários."""
import os
import socket
import tempfile
import unittest

import protocol as p
from server import Server
from tests.support import LogRecorder, receive_within
from tests.test_download import share


class SharedRootTest(unittest.TestCase):
  """Pedidos a um servidor real cuja raiz compartilhada, `files/`, fica em um
  diretório temporário ao lado de uma pasta irmã de nome parecido,
  `files-outro/`, que não é compartilhada."""

  def setUp(self):
    base = tempfile.TemporaryDirectory()
    self.addCleanup(base.cleanup)
    self.base = base.name
    self.shared_root = os.path.join(self.base, "files")
    os.mkdir(self.shared_root)
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

  def test_symbolic_link_as_the_file_is_forbidden_wherever_it_points(self):
    share(self.shared_root, "presente.txt", b"presente")
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
    share(self.shared_root, "presente.txt", b"presente")
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
    share(self.shared_root, "presente.txt", b"presente")
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
    share(self.shared_root, "presente.txt", b"presente")
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
    share(self.shared_root, "presente.txt", b"presente")
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


if __name__ == "__main__":
  unittest.main()

"""Proteção de caminhos na pasta raiz, herdada do Trabalho 2.

Enquanto o servidor ainda não fala HTTP, estes testes exercitam diretamente
a abertura protegida de recursos. O ticket do caminho bloqueado (403) os
substitui por testes pela fronteira HTTP."""
import os
import tempfile
import unittest

import protocol as p
from server import ResourceRefused, open_resource
from tests.support import CAN_EXCHANGE, Swapper


def put(root, path, content):
  """Coloca um arquivo com `content` sob `root`, criando as pastas."""
  full = os.path.join(root, *path.split("/"))
  os.makedirs(os.path.dirname(full), exist_ok=True)
  with open(full, "wb") as file:
    file.write(content)


class ContainmentTest(unittest.TestCase):
  """Contenção na pasta raiz pela relação entre diretórios, depois de
  resolver os caminhos."""

  def setUp(self):
    base = tempfile.TemporaryDirectory()
    self.addCleanup(base.cleanup)
    self.base = base.name
    self.root = os.path.join(self.base, "www")
    os.makedirs(os.path.join(self.root, "aulas"))
    os.makedirs(os.path.join(self.base, "www-outro"))

  def test_root_and_whatever_lies_under_it_are_inside(self):
    for target in ("", "aulas", "aulas/resumo.pdf", "ainda/nao/existe.txt", "www-outro"):
      p.check_inside(self.root, os.path.join(self.root, *target.split("/")))
    p.check_inside(self.root + os.sep, os.path.join(self.root, "aulas"))

  def test_sibling_folder_whose_name_starts_like_the_root_is_not_inside(self):
    outside = ["www-outro", "www-outro/segredo.txt", "wwwx.txt", "segredo.txt", ""]
    for target in outside:
      with self.subTest(target=target), self.assertRaises(p.ForbiddenPath):
        p.check_inside(self.root, os.path.join(self.base, *target.split("/")))

  def test_resolved_path_that_leaves_the_root_through_a_link_is_not_inside(self):
    os.symlink(os.path.join(self.base, "www-outro"), os.path.join(self.root, "irma"))
    with self.assertRaises(p.ForbiddenPath):
      p.check_inside(self.root, os.path.join(self.root, "irma", "segredo.txt"))


class RootTest(unittest.TestCase):
  """A pasta raiz, `www/`, fica num diretório temporário ao lado de uma pasta
  irmã de nome parecido, `www-outro/`, que não é servida. A raiz sempre tem
  o recurso `presente.txt`."""

  def setUp(self):
    base = tempfile.TemporaryDirectory()
    self.addCleanup(base.cleanup)
    self.base = base.name
    self.root = os.path.join(self.base, "www")
    os.mkdir(self.root)
    put(self.root, "presente.txt", b"presente")
    self.sibling = os.path.join(self.base, "www-outro")
    put(self.sibling, "segredo.txt", b"segredo da pasta irma")

  def inside(self, path):
    return os.path.join(self.root, *path.split("/"))

  def link(self, path, target):
    """Cria na pasta raiz um link simbólico `path` que aponta para `target`."""
    os.symlink(target, self.inside(path))

  def read(self, path, root=None):
    with open_resource(root or self.root, path) as file:
      return file.read()

  def assertRefused(self, paths, code, root=None):
    for path in paths:
      with self.subTest(path=path), self.assertRaises(ResourceRefused) as refusal:
        open_resource(root or self.root, path)
      self.assertEqual(refusal.exception.code, code, path)
      self.assertTrue(str(refusal.exception))

  def test_examples_of_the_path_policy_are_accepted_or_forbidden_as_agreed(self):
    accepted = {"aulas/Revisão 2.pdf": b"revisao", ".exemplo": b"oculto", "arquivo": b"sem extensao",
                "teste.bin": bytes(range(256))}
    for path, content in accepted.items():
      put(self.root, path, content)
    put(self.root, "resumo.pdf", b"resumo")
    put(self.base, "segredo.txt", b"fora da raiz")
    forbidden = ["../../segredo.txt", "../segredo.txt", "aulas/../resumo.pdf", "/etc/passwd", "C:/dados/a.txt",
                 "C:\\dados\\a.txt", "", "./arquivo", "aulas//resumo.pdf", "aulas/", "arquivo\x00", "arq\nuivo"]

    self.assertRefused(forbidden, p.PATH_FORBIDDEN)
    for path, content in accepted.items():
      self.assertEqual(self.read(path), content, path)

  def test_path_that_is_not_utf_8_text_or_breaks_the_line_is_forbidden_even_when_the_name_exists(self):
    for name in ("arq\u2028uivo", "arq\u2029uivo"):
      put(self.root, name, b"existe")

    paths = ["arq\u2028uivo", "arq\u2029uivo", "arq\udc80uivo", "arq\ud800uivo", "aulas/\udcff"]
    self.assertRefused(paths, p.PATH_FORBIDDEN)

  def test_symbolic_link_as_the_file_is_forbidden_wherever_it_points(self):
    put(self.root, "aulas/resumo.pdf", b"resumo")
    self.link("interno.txt", "presente.txt")
    self.link("aulas/interno.pdf", self.inside("aulas/resumo.pdf"))
    self.link("externo.txt", os.path.join(self.sibling, "segredo.txt"))
    self.link("relativo.txt", "../www-outro/segredo.txt")
    self.link("quebrado.txt", "nao-existe.txt")

    links = ["interno.txt", "aulas/interno.pdf", "externo.txt", "relativo.txt", "quebrado.txt"]
    self.assertRefused(links, p.PATH_FORBIDDEN)

  def test_symbolic_link_as_an_intermediate_folder_is_forbidden_before_absence_is_considered(self):
    put(self.root, "aulas/2024/resumo.pdf", b"resumo")
    self.link("atalho", "aulas")
    self.link("aulas/ano", self.inside("aulas/2024"))
    self.link("irma", "../www-outro")
    self.link("quebrado", "nao-existe")

    paths = ["atalho/2024/resumo.pdf", "aulas/ano/resumo.pdf", "irma/segredo.txt", "atalho/ausente.txt",
             "quebrado/ausente.txt"]
    self.assertRefused(paths, p.PATH_FORBIDDEN)

  def test_missing_resource_is_not_found(self):
    put(self.root, "aulas/resumo.pdf", b"resumo")

    self.assertRefused(["ausente.txt", "aulas/ausente.pdf", "nada/ausente.txt", "presente.txt/dentro"], p.NOT_FOUND)

  @unittest.skipUnless(hasattr(os, "mkfifo"), "requer FIFO")
  def test_folder_fifo_and_other_non_regular_objects_get_not_regular_without_being_opened(self):
    """Um FIFO sem escritor não bloqueia a abertura."""
    put(self.root, "aulas/resumo.pdf", b"resumo")
    os.mkdir(self.inside("vazia"))
    os.mkfifo(self.inside("canal"))
    os.mkfifo(self.inside("aulas/canal"))

    self.assertRefused(["aulas", "vazia", "canal", "aulas/canal"], p.NOT_REGULAR)

  @unittest.skipIf(not hasattr(os, "geteuid") or os.geteuid() == 0, "root ignora as permissões de arquivo")
  def test_missing_permission_gets_access_error_not_not_found(self):
    put(self.root, "fechado.txt", b"sem leitura")
    put(self.root, "trancada/dentro.txt", b"atras da pasta")
    os.chmod(self.inside("fechado.txt"), 0)
    os.chmod(self.inside("trancada"), 0)
    self.addCleanup(os.chmod, self.inside("trancada"), 0o700)

    self.assertRefused(["fechado.txt", "trancada/dentro.txt", "trancada/ausente.txt"], p.ACCESS_ERROR)
    self.assertRefused(["trancada/../presente.txt"], p.PATH_FORBIDDEN)

  @unittest.skipUnless(CAN_EXCHANGE, "requer renameat2 com RENAME_EXCHANGE")
  def test_folder_swapped_with_a_link_during_openings_never_serves_a_file_from_outside_the_root(self):
    """Uma thread troca sem parar a pasta `aulas` por um link para fora da
    raiz enquanto `aulas/segredo.txt` é aberto repetidamente. Cada abertura
    entrega o arquivo legítimo ou uma recusa, nunca o arquivo de fora."""
    put(self.root, "aulas/segredo.txt", b"legitimo")
    put(self.base, "fora/segredo.txt", b"FORA DA RAIZ")
    self.link("troca", os.path.join(self.base, "fora"))

    served = []
    swapper = Swapper(self.inside("aulas"), self.inside("troca")).start()
    try:
      for _ in range(200):
        try:
          served.append(self.read("aulas/segredo.txt"))
        except ResourceRefused:
          pass
    finally:
      swapper.stop()

    self.assertNotIn(b"FORA DA RAIZ", served)

  def test_root_that_is_a_symbolic_link_is_forbidden(self):
    linked_root = os.path.join(self.base, "www-link")
    os.symlink(self.root, linked_root)

    self.assertRefused(["presente.txt", "ausente.txt"], p.PATH_FORBIDDEN, root=linked_root)


if __name__ == "__main__":
  unittest.main()

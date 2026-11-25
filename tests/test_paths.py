"""Caminhos da URL até os recursos da pasta raiz, pela fronteira HTTP: o que é
entregue, o que é bloqueado (403) e o que não existe (404)."""
import os
import unittest

import protocol as p
from server import ResourceRefused, open_resource
from tests.support import CAN_EXCHANGE, Swapper
from tests.test_http import HttpTestCase, put

SECRET = b"SEGREDO FORA DA RAIZ"


class ContainmentTest(unittest.TestCase):
  """Contenção na pasta raiz pela relação entre diretórios, depois de
  resolver os caminhos. É a segunda barreira contra traversal, depois da
  política de caminhos e da recusa de links; como as anteriores já barram
  tudo o que chega pela rede, ela é verificada diretamente."""

  def setUp(self):
    import tempfile
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


class PathTestCase(HttpTestCase):
  """A pasta raiz `www/` fica ao lado de uma pasta irmã de nome parecido,
  `www-outro/`, e de `segredo.txt`, ambos fora da raiz. A raiz sempre tem o
  recurso `presente.txt`."""

  def setUp(self):
    super().setUp()
    self.base = os.path.dirname(self.root)
    put(self.root, "presente.txt", b"presente")
    put(self.base, "segredo.txt", SECRET)
    put(os.path.join(self.base, "www-outro"), "segredo.txt", SECRET)

  def inside(self, path):
    return os.path.join(self.root, *path.split("/"))

  def link(self, path, target):
    """Cria na pasta raiz um link simbólico `path` que aponta para `target`."""
    os.symlink(target, self.inside(path))

  def assertBlocked(self, targets):
    for target in targets:
      with self.subTest(target=target):
        response = self.get(target)
        self.assertEqual(response.status_line, "HTTP/1.1 403 Forbidden")
        self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(response.headers["content-length"], str(len(response.body)))
        self.assertIn("Acesso negado", response.body.decode("utf-8"))
        self.assertNotIn(SECRET, response.body)


class BlockedPathTest(PathTestCase):
  def test_dot_dot_and_dot_components_sent_as_they_are_are_blocked(self):
    put(self.root, "aulas/resumo.txt", b"resumo")
    self.start_server()

    self.assertBlocked(["/../segredo.txt", "/../../segredo.txt", "/../www-outro/segredo.txt",
                        "/aulas/../presente.txt", "/./presente.txt", "/aulas/./resumo.txt"])

  def test_traversal_hidden_by_percent_encoding_is_decoded_and_blocked(self):
    self.start_server()

    self.assertBlocked(["/..%2F..%2Fsegredo.txt", "/..%2f..%2fsegredo.txt", "/%2e%2e/%2e%2e/segredo.txt",
                        "/%2E%2E%2F%2E%2E%2Fsegredo.txt", "/aulas/..%2F..%2Fsegredo.txt",
                        "/..%2F..%2F..%2F..%2F..%2F..%2Fetc%2Fpasswd", "/.%2E/segredo.txt"])

  def test_absolute_paths_backslashes_colons_empty_components_and_control_characters_are_blocked(self):
    put(self.root, "aulas/resumo.txt", b"resumo")
    self.start_server()

    self.assertBlocked(["//etc/passwd", "/%2Fetc%2Fpasswd", "/..%5C..%5Csegredo.txt", "/C:%5Cdados%5Ca.txt",
                        "/C:/dados/a.txt", "/aulas//resumo.txt", "/presente.txt%00.html", "/arq%0Auivo",
                        "/arq%0D%0Auivo", "/arq%E2%80%A8uivo", "/arq%E2%80%A9uivo"])

  def test_symbolic_link_as_the_resource_is_blocked_wherever_it_points(self):
    put(self.root, "aulas/resumo.txt", b"resumo")
    self.link("interno.txt", "presente.txt")
    self.link("aulas/interno.txt", self.inside("aulas/resumo.txt"))
    self.link("externo.txt", os.path.join(self.base, "segredo.txt"))
    self.link("relativo.txt", "../www-outro/segredo.txt")
    self.link("quebrado.txt", "nao-existe.txt")
    self.start_server()

    self.assertBlocked(["/interno.txt", "/aulas/interno.txt", "/externo.txt", "/relativo.txt", "/quebrado.txt"])

  def test_symbolic_link_as_a_folder_is_blocked_before_absence_is_considered(self):
    put(self.root, "aulas/2024/resumo.txt", b"resumo")
    self.link("atalho", "aulas")
    self.link("aulas/ano", self.inside("aulas/2024"))
    self.link("irma", "../www-outro")
    self.link("quebrado", "nao-existe")
    self.start_server()

    self.assertBlocked(["/atalho/2024/resumo.txt", "/aulas/ano/resumo.txt", "/irma/segredo.txt",
                        "/atalho/ausente.txt", "/quebrado/ausente.txt"])

  def test_root_folder_that_is_a_symbolic_link_blocks_every_request(self):
    linked_root = os.path.join(self.base, "www-link")
    os.symlink(self.root, linked_root)
    self.start_server(root=linked_root)

    self.assertBlocked(["/presente.txt", "/ausente.txt"])

  @unittest.skipUnless(CAN_EXCHANGE, "requer renameat2 com RENAME_EXCHANGE")
  def test_folder_swapped_with_a_link_during_requests_never_serves_a_file_from_outside_the_root(self):
    """Uma thread troca sem parar a pasta `aulas` por um link para fora da
    raiz enquanto `aulas/segredo.txt` é pedido repetidamente. Cada pedido
    recebe o recurso legítimo ou uma recusa, nunca o arquivo de fora."""
    put(self.root, "aulas/segredo.txt", b"legitimo")
    put(self.base, "fora/segredo.txt", SECRET)
    self.link("troca", os.path.join(self.base, "fora"))
    self.start_server()

    bodies = []
    swapper = Swapper(self.inside("aulas"), self.inside("troca")).start()
    try:
      for _ in range(150):
        response = self.get("/aulas/segredo.txt")
        if response.status == 200:
          bodies.append(response.body)
    finally:
      swapper.stop()

    self.assertNotIn(SECRET, bodies)

  def test_blocked_path_is_logged_with_the_target_received(self):
    self.start_server()

    self.get("/..%2F..%2Fsegredo.txt")

    self.assertIn("../../segredo.txt", self.log.wait_for("Caminho bloqueado: /..%2F..%2Fsegredo.txt"))
    self.log.wait_for('"GET /..%2F..%2Fsegredo.txt HTTP/1.1" 403')

  def test_missing_resource_is_still_not_found_and_not_blocked(self):
    self.start_server()

    for target in ("/ausente.txt", "/aulas/ausente.txt", "/presente.txt/dentro"):
      with self.subTest(target=target):
        self.assertEqual(self.get(target).status, 404)


class TargetDecodingTest(PathTestCase):
  def test_query_string_and_fragment_are_ignored(self):
    self.start_server()

    for target in ("/presente.txt?v=1", "/presente.txt?x=../../segredo.txt", "/presente.txt#topo",
                   "/presente.txt?a=1#b"):
      with self.subTest(target=target):
        response = self.get(target)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b"presente")

  def test_names_with_spaces_and_accents_encoded_as_utf_8_are_served(self):
    put(self.root, "aulas/Revisão 2.pdf", b"revisao")
    put(self.root, "pergunta?.txt", b"interrogacao")
    self.start_server()

    for target in ("/aulas/Revis%C3%A3o%202.pdf", "/aulas/Revis%c3%a3o%202.pdf", "/pergunta%3F.txt"):
      with self.subTest(target=target):
        response = self.get(target)
        self.assertEqual(response.status, 200)
    self.assertEqual(self.get("/aulas/Revis%C3%A3o%202.pdf").headers["content-type"], "application/pdf")

  def test_name_sent_as_raw_utf_8_bytes_is_served(self):
    put(self.root, "ação.txt", b"acao")
    self.start_server()

    response = self.exchange("GET /ação.txt HTTP/1.1\r\nHost: localhost\r\n\r\n".encode("utf-8"))

    self.assertEqual(response.status, 200)
    self.assertEqual(response.body, b"acao")

  def test_encoded_slash_at_the_end_names_a_folder(self):
    put(self.root, "aulas/resumo.txt", b"resumo")
    self.start_server()

    self.assertEqual(self.get("/aulas%2F").status, 404)

  def test_invalid_percent_encoding_or_bytes_that_are_not_utf_8_get_400(self):
    self.start_server()

    for target in ("/%ZZ.txt", "/abc%4", "/abc%", "/%C3%28.txt", "/%80.txt", "/%ED%B2%80.txt"):
      with self.subTest(target=target):
        response = self.get(target)
        self.assertEqual(response.status_line, "HTTP/1.1 400 Bad Request")
        self.assertEqual(response.headers["content-length"], str(len(response.body)))
        self.assertIn("Requisição inválida", response.body.decode("utf-8"))


class AccessErrorTest(PathTestCase):
  """Transitório: o ticket da robustez (500) leva este caso à fronteira HTTP."""

  @unittest.skipIf(not hasattr(os, "geteuid") or os.geteuid() == 0, "root ignora as permissões de arquivo")
  def test_missing_permission_is_an_access_error_and_not_a_missing_resource(self):
    put(self.root, "fechado.txt", b"sem leitura")
    put(self.root, "trancada/dentro.txt", b"atras da pasta")
    os.chmod(self.inside("fechado.txt"), 0)
    os.chmod(self.inside("trancada"), 0)
    self.addCleanup(os.chmod, self.inside("trancada"), 0o700)

    for path in ("fechado.txt", "trancada/dentro.txt", "trancada/ausente.txt"):
      with self.subTest(path=path), self.assertRaises(ResourceRefused) as refusal:
        open_resource(self.root, path)
      self.assertEqual(refusal.exception.code, p.ACCESS_ERROR)


if __name__ == "__main__":
  unittest.main()

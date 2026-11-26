"""Materiais de entrega, rodando os scripts como no terminal: as cópias sem
comentários para a gravação do vídeo, as linhas que o roteiro do vídeo cita
nelas e o ZIP do código-fonte, igual ao mostrado no vídeo."""
import ast
import io
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import tokenize
import unittest
import zipfile

from tests.support import OutputReader, free_port

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(PROJECT_ROOT, "scripts")
PROGRAMS = ("server.py", "protocol.py")
VIDEO_SCRIPT = os.path.join(PROJECT_ROOT, "docs", "roteiro-video.md")
CITED_LINE = re.compile(r"^\| `(server\.py|protocol\.py)` \| (\d+) \| `(.+)` \|$")


def run_script(name, *args):
  return subprocess.run([sys.executable, os.path.join(SCRIPTS, name), *args], capture_output=True, text=True,
                        timeout=120)


def read(path):
  with open(path, encoding="utf-8") as file:
    return file.read()


def docstrings(tree):
  return [node for node in ast.walk(tree)
          if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
          and ast.get_docstring(node, clean=False) is not None]


def without_docstrings(tree):
  """A árvore sem as docstrings; um corpo que só tinha a docstring vira pass."""
  for node in docstrings(tree):
    node.body = node.body[1:] or ([] if isinstance(node, ast.Module) else [ast.Pass()])
  return ast.dump(tree, include_attributes=False)


def files_under(root):
  found = set()
  for folder, folders, names in os.walk(root):
    folders[:] = [name for name in folders if name != "__pycache__"]
    for name in names:
      if not name.endswith(".pyc"):
        found.add(os.path.relpath(os.path.join(folder, name), root).replace(os.sep, "/"))
  return found


@unittest.skipUnless(os.path.isdir(SCRIPTS), "os scripts de entrega não fazem parte do ZIP")
class RecordingCopiesTest(unittest.TestCase):
  def setUp(self):
    base = tempfile.TemporaryDirectory()
    self.addCleanup(base.cleanup)
    self.destination = os.path.join(base.name, "gravacao")
    result = run_script("preparar_gravacao.py", self.destination)
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    self.output = result.stdout

  def test_copies_have_no_comments_and_no_docstrings(self):
    for name in PROGRAMS:
      with self.subTest(name=name):
        copy = read(os.path.join(self.destination, name))
        tokens = tokenize.generate_tokens(io.StringIO(copy).readline)
        self.assertEqual([token.string for token in tokens if token.type == tokenize.COMMENT], [])
        self.assertEqual(docstrings(ast.parse(copy)), [])

  def test_copies_keep_exactly_the_logic_of_the_documented_sources(self):
    for name in PROGRAMS:
      with self.subTest(name=name):
        original = ast.parse(read(os.path.join(PROJECT_ROOT, name)))
        copy = ast.parse(read(os.path.join(self.destination, name)))
        self.assertEqual(ast.dump(copy, include_attributes=False), without_docstrings(original))

  def test_removed_lines_leave_no_gaps_or_trailing_spaces(self):
    for name in PROGRAMS:
      with self.subTest(name=name):
        lines = read(os.path.join(self.destination, name)).split("\n")
        self.assertEqual([line for line in lines if line != line.rstrip()], [])
        self.assertNotIn("\n\n\n\n", "\n".join(lines))
        after_header = [number for number, line in enumerate(lines[:-1], start=1)
                        if line.endswith(":") and not lines[number].strip()]
        self.assertEqual(after_header, [], "linha em branco logo depois de um cabeçalho de bloco")

  def test_copied_server_serves_the_site_like_the_original(self):
    port = free_port()
    process = subprocess.Popen([sys.executable, "-u", os.path.join(self.destination, "server.py"), str(port)],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    output = OutputReader(process.stdout)
    self.addCleanup(output.close)
    self.addCleanup(process.wait, 5)
    self.addCleanup(process.kill)
    output.wait_for(f"porta TCP {port}")

    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
      sock.sendall(b"GET /pagina.html HTTP/1.1\r\nHost: localhost\r\n\r\n")
      data = bytearray()
      while chunk := sock.recv(65536):
        data += chunk

    with open(os.path.join(PROJECT_ROOT, "www", "pagina.html"), "rb") as file:
      self.assertTrue(bytes(data).endswith(b"\r\n\r\n" + file.read()))
    self.assertIn(os.path.join(self.destination, "www"), output.text)
    process.send_signal(signal.SIGINT)
    self.assertEqual(process.wait(5), 0)

  @unittest.skipUnless(os.path.isfile(VIDEO_SCRIPT), "o roteiro do vídeo não faz parte do ZIP")
  def test_lines_cited_in_the_video_script_hold_the_code_it_names(self):
    cited = [match.groups() for line in read(VIDEO_SCRIPT).splitlines() if (match := CITED_LINE.match(line))]
    copies = {name: read(os.path.join(self.destination, name)).split("\n") for name in PROGRAMS}

    self.assertGreater(len(cited), 0, "o roteiro não tem a tabela de linhas citadas")
    for name, number, code in cited:
      with self.subTest(name=name, line=number):
        self.assertIn(code, copies[name][int(number) - 1])

  def test_destination_that_is_not_empty_is_refused_without_touching_it(self):
    result = run_script("preparar_gravacao.py", self.destination)

    self.assertNotEqual(result.returncode, 0)
    self.assertIn("vazia", result.stdout + result.stderr)


@unittest.skipUnless(os.path.isdir(SCRIPTS), "os scripts de entrega não fazem parte do ZIP")
class SourcePackageTest(unittest.TestCase):
  def setUp(self):
    base = tempfile.TemporaryDirectory()
    self.addCleanup(base.cleanup)
    self.base = base.name
    self.package = os.path.join(self.base, "trabalho3.zip")
    result = run_script("empacotar.py", self.package)
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

  def test_package_has_the_programs_and_the_site_and_nothing_else(self):
    expected = set(PROGRAMS) | {f"www/{name}" for name in files_under(os.path.join(PROJECT_ROOT, "www"))}

    with zipfile.ZipFile(self.package) as package:
      names = {name for name in package.namelist() if not name.endswith("/")}

    self.assertEqual(names, {f"Trabalho3/{name}" for name in expected})

  def test_programs_are_identical_to_the_copies_shown_in_the_video(self):
    copies = os.path.join(self.base, "gravacao")
    result = run_script("preparar_gravacao.py", copies)
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    with zipfile.ZipFile(self.package) as package:
      for name in PROGRAMS:
        with self.subTest(name=name):
          with open(os.path.join(copies, name), "rb") as file:
            self.assertEqual(package.read(f"Trabalho3/{name}"), file.read())

  def test_site_files_are_identical_to_the_project_site(self):
    with zipfile.ZipFile(self.package) as package:
      for name in files_under(os.path.join(PROJECT_ROOT, "www")):
        with self.subTest(name=name):
          with open(os.path.join(PROJECT_ROOT, "www", name), "rb") as file:
            self.assertEqual(package.read(f"Trabalho3/www/{name}"), file.read())

  def test_extracted_server_serves_the_site(self):
    with zipfile.ZipFile(self.package) as package:
      package.extractall(self.base)
    extracted = os.path.join(self.base, "Trabalho3")
    port = free_port()
    process = subprocess.Popen([sys.executable, "-u", "server.py", str(port)], cwd=extracted,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    output = OutputReader(process.stdout)
    self.addCleanup(output.close)
    self.addCleanup(process.wait, 5)
    self.addCleanup(process.kill)
    output.wait_for(f"porta TCP {port}")

    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
      sock.sendall(b"GET /foto.jpg HTTP/1.1\r\nHost: localhost\r\n\r\n")
      data = bytearray()
      while chunk := sock.recv(65536):
        data += chunk

    with open(os.path.join(PROJECT_ROOT, "www", "foto.jpg"), "rb") as file:
      self.assertTrue(bytes(data).startswith(b"HTTP/1.1 200 OK\r\n"))
      self.assertTrue(bytes(data).endswith(b"\r\n\r\n" + file.read()))
    process.send_signal(signal.SIGINT)
    self.assertEqual(process.wait(5), 0)


if __name__ == "__main__":
  unittest.main()

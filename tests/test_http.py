"""Requisições HTTP ao servidor real pela fronteira TCP: o teste envia os bytes
da requisição por um socket cru e lê a resposta até o servidor fechar."""
import datetime
import email.utils
import os
import re
import socket
import tempfile
import time
import unittest

from server import Server
from tests.support import LogRecorder, shut_down_within

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PAGE = "<html><body><h1>Olá, você</h1>\r\n<p>conteúdo</p></body></html>\n".encode("utf-8")

# Modelo de página do enunciado do Trabalho 3, sem alterações.
TEMPLATE = """<html>
  <head>
    <title>Trabalho 3 - Redes</title>
  </head>
  <body>
    <h1>Servidor HTTP Funcional</h1>
    <p>Se você está vendo esta página,
    seu servidor TCP aceitou a conexão e
    processou o GET corretamente. </p>
  </body>
</html>
""".encode("utf-8")

IMF_FIXDATE = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{2} "
                         r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4} \d{2}:\d{2}:\d{2} GMT$")


def put(root, path, content):
  full = os.path.join(root, *path.split("/"))
  os.makedirs(os.path.dirname(full), exist_ok=True)
  with open(full, "wb") as file:
    file.write(content)


class Response:
  """Resposta lida até o fechamento da conexão, separada na primeira linha
  em branco."""

  def __init__(self, data):
    head, separator, self.body = data.partition(b"\r\n\r\n")
    if not separator:
      raise AssertionError(f"resposta sem linha em branco: {data[:200]!r}")
    lines = head.decode("ascii").split("\r\n")
    self.status_line = lines[0]
    self.header_lines = lines[1:]
    self.headers = {}
    for line in self.header_lines:
      name, _, value = line.partition(": ")
      self.headers[name.lower()] = value

  @property
  def status(self):
    return int(self.status_line.split(" ")[1])


class HttpTestCase(unittest.TestCase):
  """Servidor real numa porta livre, com a pasta raiz `www/` num diretório
  temporário."""

  def setUp(self):
    base = tempfile.TemporaryDirectory()
    self.addCleanup(base.cleanup)
    self.root = os.path.join(base.name, "www")
    os.mkdir(self.root)

  def start_server(self, root=None):
    self.log = LogRecorder()
    self.server = Server("127.0.0.1", 0, log=self.log, root=root or self.root)
    self.server.start()
    self.addCleanup(shut_down_within, self.server)

  def open_connection(self):
    sock = socket.create_connection(self.server.address, timeout=5)
    self.addCleanup(sock.close)
    return sock

  def read_until_closed(self, sock):
    data = bytearray()
    while chunk := sock.recv(65536):
      data += chunk
    return bytes(data)

  def exchange(self, raw):
    """Envia `raw` de uma vez e devolve a resposta inteira."""
    sock = self.open_connection()
    sock.sendall(raw)
    return Response(self.read_until_closed(sock))

  def get(self, target):
    return self.exchange(f"GET {target} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode("ascii"))


class SuccessfulGetTest(HttpTestCase):
  def test_get_of_an_html_resource_gets_200_and_exactly_the_bytes_of_the_file(self):
    put(self.root, "pagina.html", PAGE)
    self.start_server()

    response = self.get("/pagina.html")

    self.assertEqual(response.status_line, "HTTP/1.1 200 OK")
    self.assertEqual(response.body, PAGE)

  def test_response_carries_the_six_headers_with_the_exact_length_and_the_html_type(self):
    put(self.root, "pagina.html", PAGE)
    self.start_server()

    response = self.get("/pagina.html")

    self.assertEqual(
      [line.split(":")[0] for line in response.header_lines],
      ["Date", "Server", "Content-Type", "Content-Length", "Connection", "Cache-Control"])
    self.assertEqual(response.headers["content-length"], str(len(PAGE)))
    self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
    self.assertEqual(response.headers["server"], "ServidorRedes/1.0")
    self.assertEqual(response.headers["connection"], "close")
    self.assertEqual(response.headers["cache-control"], "no-store")

  def test_date_is_the_current_time_in_the_imf_fixdate_format(self):
    put(self.root, "pagina.html", PAGE)
    self.start_server()

    response = self.get("/pagina.html")

    date = response.headers["date"]
    self.assertRegex(date, IMF_FIXDATE)
    sent = email.utils.parsedate_to_datetime(date)
    now = datetime.datetime.now(datetime.timezone.utc)
    self.assertLess(abs((now - sent).total_seconds()), 5)

  def test_root_gets_the_index(self):
    put(self.root, "index.html", b"<p>indice</p>")
    put(self.root, "pagina.html", PAGE)
    self.start_server()

    response = self.get("/")

    self.assertEqual(response.status, 200)
    self.assertEqual(response.body, b"<p>indice</p>")

  def test_request_arriving_in_pieces_is_assembled_up_to_the_blank_line(self):
    put(self.root, "pagina.html", PAGE)
    self.start_server()
    sock = self.open_connection()

    for piece in (b"GET /pag", b"ina.html HTTP/1.1\r", b"\nHost: localhost\r\n", b"\r", b"\n"):
      sock.sendall(piece)
      time.sleep(0.05)

    response = Response(self.read_until_closed(sock))
    self.assertEqual(response.status, 200)
    self.assertEqual(response.body, PAGE)

  def test_bytes_after_the_blank_line_are_ignored(self):
    put(self.root, "pagina.html", PAGE)
    self.start_server()

    response = self.exchange(b"GET /pagina.html HTTP/1.1\r\nHost: localhost\r\n\r\nGET /outra HTTP/1.1\r\n\r\n")

    self.assertEqual(response.status, 200)
    self.assertEqual(response.body, PAGE)

  def test_headers_from_the_browser_are_accepted(self):
    put(self.root, "pagina.html", PAGE)
    self.start_server()

    response = self.exchange(
      b"GET /pagina.html HTTP/1.1\r\nHost: localhost:8080\r\nUser-Agent: Mozilla/5.0\r\n"
      b"Accept: text/html,image/jpeg\r\nAccept-Language: pt-BR,en-US\r\nConnection: keep-alive\r\n\r\n")

    self.assertEqual(response.status, 200)
    self.assertEqual(response.headers["connection"], "close")
    self.assertEqual(response.body, PAGE)

  def test_each_request_is_logged_with_thread_client_request_status_bytes_and_times(self):
    put(self.root, "pagina.html", PAGE)
    self.start_server()
    sock = self.open_connection()
    host, port = sock.getsockname()[:2]

    sock.sendall(b"GET /pagina.html HTTP/1.1\r\nHost: localhost\r\n\r\n")
    self.read_until_closed(sock)

    line = self.log.wait_for('"GET /pagina.html HTTP/1.1"')
    self.assertIn("conexão 1", line)
    self.assertIn(f"{host}:{port}", line)
    self.assertIn(f"200 {len(PAGE)} bytes", line)
    self.assertRegex(line, r"início \d{2}:\d{2}:\d{2}\.\d{3}, fim \d{2}:\d{2}:\d{2}\.\d{3}")


class ContentTypeTest(HttpTestCase):
  # Tabela da spec #1, que é a fonte do valor esperado.
  EXPECTED = {
    "a.html": "text/html; charset=utf-8",
    "a.htm": "text/html; charset=utf-8",
    "a.css": "text/css; charset=utf-8",
    "a.js": "text/javascript; charset=utf-8",
    "a.txt": "text/plain; charset=utf-8",
    "a.json": "application/json",
    "a.png": "image/png",
    "a.jpg": "image/jpeg",
    "a.jpeg": "image/jpeg",
    "a.gif": "image/gif",
    "a.svg": "image/svg+xml",
    "a.ico": "image/x-icon",
    "a.webp": "image/webp",
    "a.pdf": "application/pdf",
    "FOTO.JPG": "image/jpeg",
    "Pagina.HTML": "text/html; charset=utf-8",
    "a.desconhecida": "application/octet-stream",
    "sem-extensao": "application/octet-stream",
    ".oculto": "application/octet-stream",
    "pasta.png/arquivo": "application/octet-stream",
  }

  def test_content_type_follows_the_extension_of_the_resource_ignoring_case(self):
    for path in self.EXPECTED:
      put(self.root, path, b"x")
    self.start_server()

    for path, expected in self.EXPECTED.items():
      with self.subTest(path=path):
        self.assertEqual(self.get(f"/{path}").headers["content-type"], expected)


class BinaryContentTest(HttpTestCase):
  def test_bytes_that_are_not_text_arrive_unchanged(self):
    content = bytes(range(256)) * 4 + b"\r\n\n\r\x00\xff\xfe" + "acentuação".encode("latin-1")
    put(self.root, "dados.bin", content)
    put(self.root, "imagem.png", b"\x89PNG\r\n\x1a\n" + content)
    self.start_server()

    for path in ("dados.bin", "imagem.png"):
      with self.subTest(path=path):
        response = self.get(f"/{path}")
        self.assertEqual(response.headers["content-length"], str(len(response.body)))
        self.assertEqual(response.body, b"\x89PNG\r\n\x1a\n" + content if path == "imagem.png" else content)

  def test_file_larger_than_one_block_arrives_whole_with_its_exact_length(self):
    content = os.urandom(3 * 1024 * 1024 + 17)
    put(self.root, "grande.jpg", content)
    self.start_server()

    response = self.get("/grande.jpg")

    self.assertEqual(response.headers["content-length"], str(len(content)))
    self.assertEqual(response.body, content)

  def test_empty_file_gets_length_zero_and_an_empty_body(self):
    put(self.root, "vazio.txt", b"")
    self.start_server()

    response = self.get("/vazio.txt")

    self.assertEqual(response.status, 200)
    self.assertEqual(response.headers["content-length"], "0")
    self.assertEqual(response.body, b"")


class DemonstrationSiteTest(HttpTestCase):
  """O site de demonstração em `www/` do projeto."""

  RESOURCES = {
    "/": "text/html; charset=utf-8",
    "/pagina.html": "text/html; charset=utf-8",
    "/estilo.css": "text/css; charset=utf-8",
    "/favicon.ico": "image/x-icon",
    "/foto.jpg": "image/jpeg",
    "/diagrama.png": "image/png",
    "/grande.png": "image/png",
  }

  def setUp(self):
    super().setUp()
    self.site = os.path.join(PROJECT_ROOT, "www")
    self.start_server(root=self.site)

  def test_every_resource_of_the_site_is_served_whole_with_its_type(self):
    for target, expected in self.RESOURCES.items():
      with self.subTest(target=target):
        response = self.get(target)
        name = "index.html" if target == "/" else target[1:]
        with open(os.path.join(self.site, name), "rb") as file:
          content = file.read()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["content-type"], expected)
        self.assertEqual(response.body, content)

  def test_images_are_real_jpeg_png_and_ico_files(self):
    signatures = {"/foto.jpg": b"\xff\xd8\xff", "/diagrama.png": b"\x89PNG\r\n\x1a\n",
                  "/grande.png": b"\x89PNG\r\n\x1a\n", "/favicon.ico": b"\x00\x00\x01\x00"}
    for target, signature in signatures.items():
      with self.subTest(target=target):
        self.assertTrue(self.get(target).body.startswith(signature))

  def test_large_image_for_the_concurrency_demonstration_has_about_20_mb(self):
    size = int(self.get("/grande.png").headers["content-length"])
    self.assertGreater(size, 15 * 1000 * 1000)
    self.assertLess(size, 30 * 1000 * 1000)

  def test_index_shows_the_jpeg_and_the_png_and_links_the_large_image(self):
    index = self.get("/").body.decode("utf-8")
    for reference in ('src="/foto.jpg"', 'src="/diagrama.png"', 'href="/grande.png"', 'href="/pagina.html"',
                      'href="/estilo.css"'):
      self.assertIn(reference, index)


class StatementTemplateTest(HttpTestCase):
  def test_the_page_of_the_statement_is_served_unchanged_from_the_project_root_folder(self):
    self.start_server(root=os.path.join(PROJECT_ROOT, "www"))

    response = self.get("/pagina.html")

    self.assertEqual(response.status, 200)
    self.assertEqual(response.body, TEMPLATE)


if __name__ == "__main__":
  unittest.main()

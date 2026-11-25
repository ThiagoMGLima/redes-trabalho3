"""Requisições inválidas, incompletas ou excessivas pela fronteira HTTP: cada
uma recebe o código adequado, com página de erro, sem derrubar o servidor nem
prender a thread."""
import os
import select
import socket
import time
import unittest

from tests.support import ThreadErrors
from tests.test_http import PAGE, HttpTestCase, Response, put

SHORT_DEADLINE = 0.5


class RobustnessTestCase(HttpTestCase):
  def setUp(self):
    super().setUp()
    put(self.root, "pagina.html", PAGE)
    self.thread_errors = ThreadErrors(self)

  def start_server(self, **options):
    options.setdefault("receive_timeout", SHORT_DEADLINE)
    super().start_server(**options)

  def assertError(self, response, status_line, title):
    self.assertEqual(response.status_line, status_line)
    self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
    self.assertEqual(response.headers["content-length"], str(len(response.body)))
    self.assertEqual(response.headers["connection"], "close")
    self.assertIn(title, response.body.decode("utf-8"))

  def assertStillServing(self):
    self.assertEqual(self.get("/pagina.html").body, PAGE)
    self.assertEqual(self.thread_errors.errors, [])


class MalformedRequestTest(RobustnessTestCase):
  def test_request_line_without_exactly_method_target_and_version_gets_400(self):
    self.start_server()

    for line in (b"GET /pagina.html", b"GET  /pagina.html HTTP/1.1", b"GET /a b HTTP/1.1", b"",
                 b"GET /pagina.html HTTP/1.1 extra", b" GET /pagina.html HTTP/1.1"):
      with self.subTest(line=line):
        response = self.exchange(line + b"\r\nHost: localhost\r\n\r\n")
        self.assertError(response, "HTTP/1.1 400 Bad Request", "Requisição inválida")
    self.assertStillServing()

  def test_target_that_does_not_start_with_a_slash_gets_400(self):
    self.start_server()

    for target in (b"pagina.html", b"*", b"http://localhost/pagina.html"):
      with self.subTest(target=target):
        response = self.exchange(b"GET " + target + b" HTTP/1.1\r\nHost: localhost\r\n\r\n")
        self.assertEqual(response.status, 400)

  def test_version_that_is_not_http_syntax_gets_400(self):
    self.start_server()

    for version in (b"HTTP/1", b"http/1.1", b"FOO", b"HTTP/1.1.1"):
      with self.subTest(version=version):
        response = self.exchange(b"GET /pagina.html " + version + b"\r\nHost: localhost\r\n\r\n")
        self.assertEqual(response.status, 400)

  def test_header_without_colon_or_with_space_in_the_name_gets_400(self):
    self.start_server()

    for header in (b"Host localhost", b"Host : localhost", b"  continuacao", b": sem nome"):
      with self.subTest(header=header):
        response = self.exchange(b"GET /pagina.html HTTP/1.1\r\nHost: localhost\r\n" + header + b"\r\n\r\n")
        self.assertError(response, "HTTP/1.1 400 Bad Request", "Requisição inválida")
    self.assertStillServing()

  def test_http_1_1_request_without_host_gets_400_but_http_1_0_does_not_need_it(self):
    self.start_server()

    without_host = self.exchange(b"GET /pagina.html HTTP/1.1\r\nAccept: */*\r\n\r\n")
    old = self.exchange(b"GET /pagina.html HTTP/1.0\r\n\r\n")

    self.assertError(without_host, "HTTP/1.1 400 Bad Request", "Requisição inválida")
    self.assertIn("Host", without_host.body.decode("utf-8"))
    self.assertEqual(old.status, 200)
    self.assertEqual(old.body, PAGE)

  def test_header_names_are_not_case_sensitive(self):
    self.start_server()

    response = self.exchange(b"GET /pagina.html HTTP/1.1\r\nhOsT: localhost\r\n\r\n")

    self.assertEqual(response.status, 200)


class UnsupportedRequestTest(RobustnessTestCase):
  def test_version_other_than_http_1_0_and_1_1_gets_505(self):
    self.start_server()

    for version in (b"HTTP/2.0", b"HTTP/0.9", b"HTTP/1.2", b"HTTP/3.0"):
      with self.subTest(version=version):
        response = self.exchange(b"GET /pagina.html " + version + b"\r\nHost: localhost\r\n\r\n")
        self.assertError(response, "HTTP/1.1 505 HTTP Version Not Supported", "Versão do HTTP não suportada")
    self.assertStillServing()

  def test_method_other_than_get_and_head_gets_405_with_allow(self):
    self.start_server()

    for method in (b"POST", b"PUT", b"DELETE", b"OPTIONS", b"get", b"Head"):
      with self.subTest(method=method):
        response = self.exchange(method + b" /pagina.html HTTP/1.1\r\nHost: localhost\r\n\r\n")
        self.assertError(response, "HTTP/1.1 405 Method Not Allowed", "Método não permitido")
        self.assertEqual(response.headers["allow"], "GET, HEAD")
    self.assertStillServing()

  def test_line_is_checked_before_the_version_the_version_before_the_method_and_the_method_before_the_headers(self):
    self.start_server()

    cases = {
      b"POST /pagina.html\r\nsem dois pontos\r\n\r\n": 400,
      b"POST /pagina.html HTTP/3.0\r\nsem dois pontos\r\n\r\n": 505,
      b"POST /pagina.html HTTP/1.1\r\nsem dois pontos\r\n\r\n": 405,
      b"GET /..%2Fsegredo HTTP/1.1\r\nsem dois pontos\r\n\r\n": 400,
    }
    for raw, status in cases.items():
      with self.subTest(raw=raw):
        self.assertEqual(self.exchange(raw).status, status)

  def test_head_with_an_error_gets_the_headers_without_body(self):
    self.start_server()

    response = self.exchange(b"HEAD /pagina.html HTTP/2.0\r\nHost: localhost\r\n\r\n")

    self.assertEqual(response.status, 505)
    self.assertGreater(int(response.headers["content-length"]), 0)
    self.assertEqual(response.body, b"")


class OversizedRequestTest(RobustnessTestCase):
  def test_head_above_8_kib_gets_431(self):
    self.start_server()

    filler = b"X-Enchimento: " + b"a" * 8192 + b"\r\n"
    response = self.exchange(b"GET /pagina.html HTTP/1.1\r\nHost: localhost\r\n" + filler + b"\r\n")

    self.assertError(response, "HTTP/1.1 431 Request Header Fields Too Large", "Cabeçalho grande demais")
    self.assertStillServing()

  def test_head_of_exactly_8_kib_is_accepted(self):
    self.start_server()
    start = b"GET /pagina.html HTTP/1.1\r\nHost: localhost\r\nX-Enchimento: "
    head = start + b"a" * (8192 - len(start))
    self.assertEqual(len(head), 8192)

    response = self.exchange(head + b"\r\n\r\n")

    self.assertEqual(response.status, 200)

  def test_stream_without_blank_line_is_cut_at_the_limit_without_waiting_for_the_rest(self):
    self.start_server()
    sock = self.open_connection()

    sock.sendall(b"GET /pagina.html HTTP/1.1\r\nHost: localhost\r\nX-Sem-Fim: " + b"a" * 9000)

    self.assertEqual(Response(self.read_until_closed(sock)).status, 431)


class DeadlineTest(RobustnessTestCase):
  def test_incomplete_request_gets_408_when_the_deadline_expires(self):
    self.start_server()
    sock = self.open_connection()

    sock.sendall(b"GET /pagina.html HTTP/1.1\r\nHost: localhost\r\n")
    started = time.monotonic()
    response = Response(self.read_until_closed(sock))

    self.assertError(response, "HTTP/1.1 408 Request Timeout", "Tempo esgotado")
    self.assertLess(time.monotonic() - started, SHORT_DEADLINE + 2)
    self.assertStillServing()

  def test_deadline_counts_the_whole_request_and_not_each_piece(self):
    """O cliente manda um pedaço a cada quinto do prazo, sem nunca terminar
    a requisição; o 408 chega enquanto ele ainda está mandando."""
    self.start_server()
    sock = self.open_connection()

    started = time.monotonic()
    pieces = iter([b"GET /pagina.html HTTP/1.1\r\nHost: localhost\r\n"] + [b"X-Lento: a\r\n"] * 100)
    while time.monotonic() - started < 4 * SHORT_DEADLINE:
      readable, _, _ = select.select([sock], [], [], SHORT_DEADLINE / 5)
      if readable:
        break
      sock.sendall(next(pieces))
    answered = time.monotonic() - started

    self.assertEqual(Response(self.read_until_closed(sock)).status, 408)
    self.assertLess(answered, 2 * SHORT_DEADLINE)

  def test_error_page_reaches_a_client_that_is_still_sending_when_the_server_answers(self):
    """O servidor responde 431 antes de ler tudo o que o cliente mandou, e o
    cliente só lê depois que o servidor já terminou; a página de erro chega
    inteira em vez de se perder num reset."""
    self.start_server()
    sock = self.open_connection()

    sock.sendall(b"GET /pagina.html HTTP/1.1\r\nHost: localhost\r\nX-Enorme: " + b"a" * 20_000)
    time.sleep(0.3)

    self.assertEqual(Response(self.read_until_closed(sock)).status, 431)

  def test_line_feeds_without_carriage_return_do_not_end_the_request(self):
    self.start_server()

    response = self.exchange(b"GET /pagina.html HTTP/1.1\nHost: localhost\n\n")

    self.assertEqual(response.status, 408)

  def test_connection_without_any_byte_is_closed_silently_at_the_deadline(self):
    self.start_server()
    sock = self.open_connection()

    self.assertEqual(self.read_until_closed(sock), b"")
    self.assertStillServing()

  def test_default_deadline_is_10_seconds(self):
    from server import RECEIVE_TIMEOUT
    self.assertEqual(RECEIVE_TIMEOUT, 10.0)


class ClientLeavingTest(RobustnessTestCase):
  def test_client_that_closes_without_sending_or_in_the_middle_of_the_request_causes_no_error(self):
    self.start_server()

    for partial in (b"", b"GET /pagi", b"GET /pagina.html HTTP/1.1\r\nHost: loc"):
      with self.subTest(partial=partial):
        sock = socket.create_connection(self.server.address, timeout=5)
        if partial:
          sock.sendall(partial)
        sock.close()
    self.log.wait_for("encerrada pelo cliente")
    self.assertStillServing()


class AccessErrorTest(RobustnessTestCase):
  @unittest.skipIf(not hasattr(os, "geteuid") or os.geteuid() == 0, "root ignora as permissões de arquivo")
  def test_resource_without_read_permission_gets_500_and_not_404(self):
    put(self.root, "fechado.html", b"sem leitura")
    put(self.root, "trancada/dentro.html", b"atras da pasta")
    os.chmod(os.path.join(self.root, "fechado.html"), 0)
    os.chmod(os.path.join(self.root, "trancada"), 0)
    self.addCleanup(os.chmod, os.path.join(self.root, "trancada"), 0o700)
    self.start_server()

    for target in ("/fechado.html", "/trancada/dentro.html", "/trancada/ausente.html"):
      with self.subTest(target=target):
        self.assertError(self.get(target), "HTTP/1.1 500 Internal Server Error", "Erro interno do servidor")
    self.assertStillServing()


if __name__ == "__main__":
  unittest.main()

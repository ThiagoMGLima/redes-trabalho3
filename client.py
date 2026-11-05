import argparse
import socket
import sys
import threading

import protocol as p

HOST = "127.0.0.1"
PORT = 50007

MENU = """
Menu:
  1 - Sair
  2 - Arquivo
  3 - Chat"""
PROMPT = "Escolha uma opção: "
OPTIONS = {
  "1": "sair", "sair": "sair",
  "2": "arquivo", "arquivo": "arquivo",
  "3": "chat", "chat": "chat",
}


def show(text):
  print(text, flush=True)


def parse_port(text):
  try:
    port = int(text)
  except ValueError:
    raise argparse.ArgumentTypeError("a porta deve ser um número")
  if not 1024 < port <= 65535:
    raise argparse.ArgumentTypeError("a porta deve estar entre 1025 e 65535")
  return port


class Client:
  """Sessão do participante. A thread principal cuida do teclado e dos
  envios; a receptora é a única leitora do socket."""

  def __init__(self, sock):
    self._sock = sock
    self._leaving = threading.Event()
    self._disconnected = threading.Event()
    self._receiver = threading.Thread(target=self._receive_loop, name="receptora")

  def run(self):
    self._receiver.start()
    try:
      self._menu_loop()
    finally:
      self._leaving.set()
      try:
        self._sock.shutdown(socket.SHUT_RDWR)
      except OSError:
        pass
      self._receiver.join()
      self._sock.close()

  def _menu_loop(self):
    while not self._disconnected.is_set():
      show(MENU)
      try:
        choice = input(PROMPT).strip().lower()
      except EOFError:
        choice = "sair"
      if self._disconnected.is_set():
        return
      option = OPTIONS.get(choice)
      if option == "sair":
        self._quit()
        return
      if option in ("arquivo", "chat"):
        show("Opção ainda não disponível.")
      else:
        show("Opção inválida.")

  def _quit(self):
    self._leaving.set()
    try:
      p.send_message(self._sock, p.QUIT, {})
    except (p.ProtocolError, OSError):
      pass
    show("Sessão encerrada.")

  def _receive_loop(self):
    while True:
      try:
        message = p.receive_message(self._sock)
      except (p.ProtocolError, OSError):
        message = None
      if message is None:
        break
    self._disconnected.set()
    if not self._leaving.is_set():
      show("\nDesconectado do servidor. Pressione Enter para sair.")


def main():
  parser = argparse.ArgumentParser(description="Cliente TCP de chat coletivo e transferência de arquivos")
  parser.add_argument("host", nargs="?", default=HOST, help=f"endereço do servidor (padrão {HOST})")
  parser.add_argument("port", nargs="?", type=parse_port, default=PORT, help=f"porta TCP (padrão {PORT})")
  args = parser.parse_args()

  try:
    sock = socket.create_connection((args.host, args.port))
  except OSError as error:
    show(f"Não foi possível conectar a {args.host}:{args.port}: {error}")
    sys.exit(1)
  show(f"Conectado ao servidor {args.host}:{args.port}")
  try:
    Client(sock).run()
  except KeyboardInterrupt:
    show("\nCliente encerrado.")


if __name__ == "__main__":
  main()

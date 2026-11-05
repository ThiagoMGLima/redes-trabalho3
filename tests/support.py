import socket
import threading
import time


def wait_until(predicate, timeout=5.0, interval=0.01):
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    if predicate():
      return True
    time.sleep(interval)
  return predicate()


class LogRecorder:
  """Substitui o terminal do servidor: guarda cada linha registrada."""

  def __init__(self):
    self._lock = threading.Lock()
    self._lines = []

  def __call__(self, line):
    with self._lock:
      self._lines.append(str(line))

  @property
  def lines(self):
    with self._lock:
      return list(self._lines)

  def matching(self, text):
    return [line for line in self.lines if text in line]

  def wait_for(self, text, timeout=5.0):
    if not wait_until(lambda: self.matching(text), timeout):
      raise AssertionError(f"{text!r} não apareceu no registro: {self.lines}")
    return self.matching(text)[0]


class ThreadErrors:
  """Registra exceções não tratadas em threads durante um teste."""

  def __init__(self, test_case):
    self.errors = []
    previous = threading.excepthook
    threading.excepthook = lambda args: self.errors.append(args.exc_value)
    test_case.addCleanup(setattr, threading, "excepthook", previous)


def tcp_pair(small_buffers=False):
  """Conexão TCP local; com buffers pequenos, um frame grande só avança
  quando o outro lado lê."""
  with socket.create_server(("127.0.0.1", 0)) as listener:
    if small_buffers:
      listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    near = socket.socket()
    if small_buffers:
      near.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
    near.connect(listener.getsockname())
    far, _ = listener.accept()
  return near, far

"""Memória do servidor diante de arquivos de tamanhos diferentes.

Cada medição roda server.py como no terminal, a partir de uma cópia do
programa com a própria pasta `www/`, sempre com o mesmo roteiro: um servidor,
um GET pelo socket e a leitura da resposta inteira. Só o tamanho do arquivo
muda. Depois da resposta, com o processo ainda vivo, lê-se o pico de memória
residente (VmHWM em /proc/<pid>/status, em KiB), que o núcleo acompanha
desde o início do programa. O teste compara picos entre tamanhos: o que
cresce de um arquivo pequeno para um grande é a memória que depende do
tamanho do arquivo.

O ru_maxrss devolvido por os.wait4 não serve: no Linux, o processo criado
herda o pico do processo que o criou, e o pico da própria suíte de testes
esconderia o do servidor.
"""
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import unittest

from tests.support import OutputReader, free_port

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIB = 1024 * 1024


def _peak_kib(process):
  with open(f"/proc/{process.pid}/status") as status:
    for line in status:
      if line.startswith("VmHWM:"):
        return int(line.split()[1])
  raise AssertionError(f"VmHWM ausente em /proc/{process.pid}/status")


def _body_size(port):
  """Pede o arquivo e devolve quantos bytes de corpo chegaram, sem guardá-los."""
  with socket.create_connection(("127.0.0.1", port), timeout=30) as sock:
    sock.sendall(b"GET /arquivo.bin HTTP/1.1\r\nHost: localhost\r\n\r\n")
    head = bytearray()
    while b"\r\n\r\n" not in head:
      head += sock.recv(4096)
    received = len(head) - head.index(b"\r\n\r\n") - 4
    while chunk := sock.recv(1024 * 1024):
      received += len(chunk)
  return received


def peak_memory(size):
  """Serve um arquivo de `size` bytes com o server.py real e devolve o pico
  do servidor, em KiB."""
  with tempfile.TemporaryDirectory() as base:
    for name in ("server.py", "protocol.py"):
      shutil.copy(os.path.join(PROJECT_ROOT, name), base)
    os.mkdir(os.path.join(base, "www"))
    with open(os.path.join(base, "www", "arquivo.bin"), "wb") as file:
      file.truncate(size)
    port = free_port()
    process = subprocess.Popen(
      [sys.executable, "-u", os.path.join(base, "server.py"), str(port)],
      stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
      encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    output = OutputReader(process.stdout)
    try:
      output.wait_for(f"porta TCP {port}")
      received = _body_size(port)
      if received != size:
        raise AssertionError(f"recebidos {received} bytes de {size}")
      output.wait_for("200")
      peak = _peak_kib(process)
      process.send_signal(signal.SIGINT)
      process.wait(5)
      return peak
    finally:
      if process.returncode is None:
        process.kill()
        process.wait(5)
      output.close()


@unittest.skipUnless(os.path.exists("/proc/self/status"), "lê o pico de memória em /proc/<pid>/status (Linux)")
class MemoryTest(unittest.TestCase):

  SMALL = 1 * MIB
  LARGE = 96 * MIB

  def test_peak_memory_of_the_server_does_not_grow_with_the_file_size(self):
    """Se o arquivo fosse lido inteiro antes do envio, o pico cresceria ao
    menos a diferença de tamanho entre os arquivos. O crescimento admitido é
    um décimo dessa diferença, folga para a variação do alocador."""
    small = peak_memory(self.SMALL)
    large = peak_memory(self.LARGE)

    allowed_kib = (self.LARGE - self.SMALL) // 10 // 1024
    self.assertLess(large - small, allowed_kib,
                    f"pico do servidor: {small} KiB com {self.SMALL // MIB} MiB e {large} KiB com "
                    f"{self.LARGE // MIB} MiB")


if __name__ == "__main__":
  unittest.main()

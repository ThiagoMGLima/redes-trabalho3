"""Memória do servidor e do cliente diante de arquivos de tamanhos diferentes.

Cada medição roda server.py e client.py como no terminal, a partir de cópias
dos programas, sempre com o mesmo roteiro e a mesma carga: um servidor, um
cliente, um download e a saída. Só o tamanho do arquivo muda. Ao fim, cada
processo é colhido com os.wait4, que informa o pico de memória residente
daquele processo (ru_maxrss, em KiB no Linux). O pico inclui tudo o que o
processo manteve na memória, do interpretador aos blocos do arquivo; por isso
o teste compara picos entre tamanhos, sem impor teto absoluto ao processo. O
que cresce de um arquivo pequeno para um grande é a memória que depende do
tamanho do arquivo.
"""
import os
import subprocess
import sys
import tempfile
import unittest

from tests.support import OutputReader, free_port, install_programs
from tests.test_download import CONCLUDED

MIB = 1024 * 1024


def _start(script, *args, cwd):
  process = subprocess.Popen(
    [sys.executable, "-u", script, *args],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd,
    encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
  )
  return process, OutputReader(process.stdout)


def _reap(process):
  """Espera o fim do processo e devolve seu pico de memória residente em KiB."""
  _, status, usage = os.wait4(process.pid, 0)
  process.returncode = os.waitstatus_to_exitcode(status)
  return usage.ru_maxrss


def peak_memory(size):
  """Baixa um arquivo de `size` bytes com server.py e client.py reais e
  devolve (pico do servidor, pico do cliente), em KiB."""
  with tempfile.TemporaryDirectory() as base:
    server_program = install_programs(os.path.join(base, "servidor"))
    client_program = install_programs(os.path.join(base, "cliente"))
    os.mkdir(os.path.join(server_program, "files"))
    os.mkdir(os.path.join(client_program, "downloads"))
    with open(os.path.join(server_program, "files", "arquivo.bin"), "wb") as file:
      file.truncate(size)
    port = str(free_port())
    processes = []
    try:
      server, server_output = _start(os.path.join(server_program, "server.py"), port, cwd=base)
      processes.append((server, server_output))
      server_output.wait_for(port)
      client, client_output = _start(os.path.join(client_program, "client.py"), "127.0.0.1", port, cwd=base)
      processes.append((client, client_output))
      client.stdin.write("2\narquivo.bin\n")
      client.stdin.flush()
      client_output.wait_for(CONCLUDED, timeout=60)
      downloaded = os.path.getsize(os.path.join(client_program, "downloads", "arquivo.bin"))
      if downloaded != size:
        raise AssertionError(f"baixados {downloaded} bytes de {size}")
      client.stdin.write("1\n")
      client.stdin.flush()
      client_peak = _reap(client)
      server.stdin.close()
      server_peak = _reap(server)
      if (server.returncode, client.returncode) != (0, 0):
        raise AssertionError(f"saídas {server.returncode} e {client.returncode}: {server_output.text!r}")
      return server_peak, client_peak
    finally:
      for process, output in reversed(processes):
        if process.returncode is None:
          process.kill()
          process.wait(5)
        try:
          process.stdin.close()
        except BrokenPipeError:
          pass
        output.close()


@unittest.skipUnless(sys.platform.startswith("linux"), "usa os.wait4 e ru_maxrss em KiB, como no Linux")
class MemoryTest(unittest.TestCase):

  SMALL = 1 * MIB
  LARGE = 96 * MIB

  def test_peak_memory_of_server_and_client_does_not_grow_with_the_file_size(self):
    """Se o conteúdo fosse carregado ou acumulado, o pico cresceria ao menos
    a diferença de tamanho entre os arquivos. O crescimento admitido é um
    décimo dessa diferença, folga para a variação do alocador."""
    small = peak_memory(self.SMALL)
    large = peak_memory(self.LARGE)

    allowed_kib = (self.LARGE - self.SMALL) // 10 // 1024
    for program, before, after in zip(("servidor", "cliente"), small, large):
      self.assertLess(after - before, allowed_kib,
                      f"pico do {program}: {before} KiB com {self.SMALL // MIB} MiB e {after} KiB com "
                      f"{self.LARGE // MIB} MiB")


if __name__ == "__main__":
  unittest.main()

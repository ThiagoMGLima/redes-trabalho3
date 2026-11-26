"""Monta o ZIP de entrega com o código-fonte exatamente como aparece no
vídeo: server.py e protocol.py sem comentários nem docstrings, iguais byte
a byte às cópias de preparar_gravacao.py, e o site em www/, dentro de uma
pasta Trabalho3/. Nada mais entra, nem arquivos compilados.

Uso: python3 scripts/empacotar.py [ARQUIVO.zip]   (padrão: dist/trabalho3.zip)
"""
import argparse
import sys
import tokenize
import zipfile
from pathlib import Path

from preparar_gravacao import PROGRAMS, PROJECT_ROOT, without_comments_and_docstrings

SITE = "www"
TOP = "Trabalho3"


def site_files():
  return sorted(path for path in (PROJECT_ROOT / SITE).rglob("*")
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc")


def pack(target):
  programs = {}
  for name in PROGRAMS:
    with tokenize.open(PROJECT_ROOT / name) as file:
      programs[name] = without_comments_and_docstrings(file.read(), name)
  target.parent.mkdir(parents=True, exist_ok=True)
  with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as package:
    for name, text in programs.items():
      package.writestr(f"{TOP}/{name}", text.encode("utf-8"))
    for path in site_files():
      package.write(path, f"{TOP}/{path.relative_to(PROJECT_ROOT).as_posix()}")
  print(f"ZIP de entrega: {target} ({target.stat().st_size} bytes)")


def main():
  parser = argparse.ArgumentParser(description="Monta o ZIP de entrega com o código-fonte como no vídeo")
  parser.add_argument("target", nargs="?", type=Path, default=PROJECT_ROOT / "dist" / "trabalho3.zip",
                      help="arquivo ZIP a criar")
  args = parser.parse_args()
  missing = [name for name in PROGRAMS + (SITE,) if not (PROJECT_ROOT / name).exists()]
  if missing:
    print(f"Erro: faltam {', '.join(missing)}", file=sys.stderr)
    sys.exit(1)
  try:
    pack(args.target)
  except ValueError as error:
    print(f"Erro: {error}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
  main()

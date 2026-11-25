"""Monta o ZIP de entrega do código-fonte, com os comentários e docstrings
dos fontes: server.py, protocol.py, README.md, o site em www/ e os testes,
dentro de uma pasta Trabalho3/. Nada mais entra, nem arquivos compilados.

Uso: python3 scripts/empacotar.py [ARQUIVO.zip]   (padrão: dist/trabalho3.zip)
"""
import argparse
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FILES = ("server.py", "protocol.py", "README.md")
FOLDERS = ("www", "tests")
TOP = "Trabalho3"


def contents():
  paths = [PROJECT_ROOT / name for name in FILES]
  for folder in FOLDERS:
    paths += sorted(path for path in (PROJECT_ROOT / folder).rglob("*")
                    if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc")
  return paths


def pack(target):
  target.parent.mkdir(parents=True, exist_ok=True)
  with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as package:
    for path in contents():
      package.write(path, f"{TOP}/{path.relative_to(PROJECT_ROOT).as_posix()}")
  print(f"ZIP de entrega: {target} ({target.stat().st_size} bytes)")


def main():
  parser = argparse.ArgumentParser(description="Monta o ZIP de entrega do código-fonte")
  parser.add_argument("target", nargs="?", type=Path, default=PROJECT_ROOT / "dist" / "trabalho3.zip",
                      help="arquivo ZIP a criar")
  args = parser.parse_args()
  missing = [name for name in FILES + FOLDERS if not (PROJECT_ROOT / name).exists()]
  if missing:
    print(f"Erro: faltam {', '.join(missing)}", file=sys.stderr)
    sys.exit(1)
  pack(args.target)


if __name__ == "__main__":
  main()

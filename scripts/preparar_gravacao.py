"""Prepara, numa pasta nova, as cópias do servidor sem comentários nem
docstrings para a gravação do vídeo, junto com o site de demonstração.

O código mostrado no vídeo não pode ter comentários. O repositório e o ZIP
de entrega continuam documentados; só as cópias desta pasta são limpas. As
linhas que eram só comentário ou docstring são retiradas, e a lógica das
cópias é conferida pela AST contra a dos fontes originais.

Uso: python3 scripts/preparar_gravacao.py PASTA_NOVA
"""
import argparse
import ast
import io
import shutil
import sys
import tokenize
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROGRAMS = ("server.py", "protocol.py")


def documentation_nodes(tree):
  """Primeira instrução de cada módulo, classe ou função que é uma docstring."""
  return [node.body[0] for node in ast.walk(tree)
          if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
          and ast.get_docstring(node, clean=False) is not None]


def without_comments_and_docstrings(source, filename):
  """Fonte sem comentários e sem docstrings, com as linhas que ficaram vazias
  por causa da remoção retiradas, sem linha em branco logo depois de um
  cabeçalho de bloco e sem espaços no fim das linhas.
  Uma classe ou função cujo corpo era só a docstring recebe `pass`.
  Levanta ValueError se sobrar documentação ou se a lógica mudar."""
  tree = ast.parse(source, filename=filename)
  lines = source.split("\n")
  removed = set()
  replaced = {}
  cut = {}
  owners = {id(parent.body[0]): parent for parent in ast.walk(tree)
            if isinstance(getattr(parent, "body", None), list) and parent.body}
  for node in documentation_nodes(tree):
    if lines[node.lineno - 1][:node.col_offset].strip() or lines[node.end_lineno - 1][node.end_col_offset:].strip():
      raise ValueError(f"docstring dividindo linha com código em {filename}:{node.lineno}")
    removed.update(range(node.lineno, node.end_lineno + 1))
    owner = owners[id(node)]
    if len(owner.body) == 1 and not isinstance(owner, ast.Module):
      replaced[node.lineno] = " " * node.col_offset + "pass"
  for token in tokenize.generate_tokens(io.StringIO(source).readline):
    if token.type == tokenize.COMMENT:
      line, column = token.start
      cut[line] = min(column, cut.get(line, column))

  kept = []
  for number, line in enumerate(lines, start=1):
    if number in replaced:
      kept.append(replaced[number])
      continue
    if number in removed:
      continue
    if number in cut:
      line = line[:cut[number]].rstrip()
      if not line:
        continue
    if not line.strip() and kept and kept[-1].endswith(":"):
      continue
    kept.append(line.rstrip())
  stripped = "\n".join(kept)

  result = ast.parse(stripped, filename=filename)
  if documentation_nodes(result):
    raise ValueError(f"documentação ainda presente em {filename}")
  if any(token.type == tokenize.COMMENT for token in tokenize.generate_tokens(io.StringIO(stripped).readline)):
    raise ValueError(f"comentários ainda presentes em {filename}")
  for node in documentation_nodes(tree):
    owner = owners[id(node)]
    owner.body = owner.body[1:] or [ast.Pass()]
  if ast.dump(tree, include_attributes=False) != ast.dump(result, include_attributes=False):
    raise ValueError(f"a remoção alterou a lógica de {filename}")
  return stripped


def prepare(destination):
  if destination.is_symlink() or (destination.exists() and (not destination.is_dir() or any(destination.iterdir()))):
    raise ValueError(f"o destino {destination} deve ser uma pasta nova ou vazia; nada foi alterado")
  copies = {}
  for name in PROGRAMS:
    with tokenize.open(PROJECT_ROOT / name) as file:
      copies[name] = without_comments_and_docstrings(file.read(), name)
  destination.mkdir(parents=True, exist_ok=True)
  for name, text in copies.items():
    (destination / name).write_text(text, encoding="utf-8")
  shutil.copytree(PROJECT_ROOT / "www", destination / "www")
  print(f"Cópias sem comentários em: {destination}")
  for name in PROGRAMS:
    print(f"  {destination / name}")
  print(f"Site de demonstração: {destination / 'www'}")
  print(f"Para gravar: cd {destination} && python3 server.py")


def main():
  parser = argparse.ArgumentParser(description="Prepara as cópias sem comentários para a gravação do vídeo")
  parser.add_argument("destination", type=Path, help="pasta nova ou vazia que recebe as cópias")
  args = parser.parse_args()
  try:
    prepare(args.destination)
  except ValueError as error:
    print(f"Erro: {error}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
  main()

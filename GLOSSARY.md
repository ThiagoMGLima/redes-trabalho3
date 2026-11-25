# Trabalho 3

Vocabulário do servidor HTTP simplificado que entrega ao navegador os arquivos de uma pasta.

## Language

**Pasta raiz**:
Pasta do servidor cujos arquivos podem ser entregues ao navegador; nada fora dela é acessível.
_Avoid_: Pasta compartilhada, document root, diretório público

**Recurso**:
Arquivo regular dentro da pasta raiz, nomeado pelo caminho da URL.
_Avoid_: Página, documento, arquivo solicitado

**Índice**:
Recurso entregue quando o caminho da URL é só a raiz (`/`).
_Avoid_: Página inicial, home

**Recurso inexistente**:
Caminho permitido que não corresponde a um recurso, seja porque nada existe ali, seja porque o que existe não é um arquivo regular.
_Avoid_: Arquivo não encontrado, erro de arquivo

**Caminho bloqueado**:
Caminho que a política de caminhos recusa antes de qualquer consulta ao conteúdo da pasta raiz, ou que passa por um link simbólico. É distinto de um recurso inexistente.
_Avoid_: Caminho inválido, acesso proibido, traversal

**Página de erro**:
Documento HTML gerado pelo servidor, e não lido da pasta raiz, que explica ao navegador por que o recurso não foi entregue.
_Avoid_: Página 404, mensagem de erro

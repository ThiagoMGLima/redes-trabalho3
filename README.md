# Trabalho 3 — Servidor HTTP multithread sobre TCP

Servidor Web simplificado construído a partir do servidor TCP multithread do Trabalho 2. Ele interpreta as requisições HTTP enviadas pelo navegador e responde com o cabeçalho HTTP seguido do conteúdo dos arquivos da pasta `www/`. Cada conexão é atendida por uma thread dedicada.

## Ambiente

Linux com Python 3 (ambiente de referência: Python 3.14.6). Só a biblioteca padrão é usada, sem instalar pacotes. Nenhuma biblioteca de HTTP ou de abstração de sockets é usada: o socket TCP é criado com `socket.socket(AF_INET, SOCK_STREAM)`, e a leitura da requisição e a montagem da resposta são feitas pelo próprio código.

## Execução

Na pasta que contém `server.py`, `protocol.py` e `www/`:

```bash
python3 server.py          # porta 8080
python3 server.py 8081     # outra porta, de 1025 a 65535
```

O servidor mostra a porta, a pasta raiz e os endereços para abrir no navegador:

```text
Servidor escutando na porta TCP 8080.
Pasta raiz: /caminho/para/www
Neste computador: http://localhost:8080/
Na rede: http://192.168.0.10:8080/
Ctrl+C encerra.
```

No navegador, abra `http://localhost:8080/` (índice com imagens JPEG e PNG) ou `http://localhost:8080/pagina.html` (modelo de página do enunciado). De outra máquina da mesma rede, use o endereço mostrado em "Na rede". Ctrl+C encerra o servidor.

Cada requisição gera uma linha no terminal, com a thread, o cliente, a linha de requisição, o status, o tamanho do corpo e os horários de início e fim:

```text
[conexão 3] 127.0.0.1:33910 "GET /foto.jpg HTTP/1.1" 200 31739 bytes (início 01:51:55.606, fim 01:51:55.607)
```

## Organização

| Arquivo | Responsabilidade |
|---|---|
| `server.py` | Socket de escuta, laço de `accept`, uma thread por conexão, abertura protegida dos recursos da pasta raiz, envio em blocos, registro no terminal e encerramento |
| `protocol.py` | Regras do HTTP que não dependem do socket: interpretação da requisição, decodificação e política do caminho, tabela de tipos, linha de status, cabeçalhos e página de erro |
| `www/` | Pasta raiz: o site de demonstração |
| `tests/` | Testes automatizados, que conversam com o servidor real por sockets TCP |

## Testes

```bash
python3 -m unittest
```

Os testes iniciam o servidor real numa porta livre, enviam os bytes da requisição por um socket TCP comum e conferem a linha de status, os cabeçalhos e o corpo byte a byte. Eles também não usam bibliotecas de HTTP.

## Decisões de projeto

### Parsing da requisição (D1)

A thread da conexão lê os bytes da requisição até a linha em branco (ver D2) e interpreta a primeira linha como **linha de requisição**. Ela precisa ter exatamente três partes separadas por um espaço: método, alvo e versão (`GET /pagina.html HTTP/1.1`). As linhas seguintes são **cabeçalhos** na forma `Nome: valor`. O nome é guardado em minúsculas, porque o HTTP não diferencia maiúsculas de minúsculas nos nomes de cabeçalho. Os bytes são lidos como ISO-8859-1, que aceita qualquer octeto, para que a sintaxe seja verificada sem erro de decodificação.

A verificação segue uma ordem fixa, e cada falha tem seu código:

| Ordem | Verificação | Falha |
|---|---|---|
| 1 | Três partes separadas por um espaço | 400 Bad Request |
| 2 | Versão `HTTP/1.0` ou `HTTP/1.1` | 505 HTTP Version Not Supported |
| 3 | Método `GET` ou `HEAD` | 405 Method Not Allowed, com `Allow: GET, HEAD` |
| 4 | Alvo começando com `/` e cabeçalhos `Nome: valor`; `Host` obrigatório em HTTP/1.1 (RFC 9112) | 400 Bad Request |
| 5 | Caminho (ver D8) | 400, 403, 404 ou 500 |

O método HEAD recebe exatamente o cabeçalho que o GET receberia, sem o corpo, inclusive nas respostas de erro.

### Fim dos cabeçalhos (D2)

O TCP entrega um fluxo de bytes, não mensagens. Uma requisição pode chegar em vários pedaços, ou junto com bytes seguintes. Por isso o servidor acumula o que recebe até encontrar a primeira ocorrência de **CRLF CRLF** (`\r\n\r\n`): o CRLF que termina a última linha de cabeçalho seguido da linha em branco. Só CRLF delimita as linhas, como manda o HTTP; um LF sozinho não encerra a requisição. O que vier depois da linha em branco é ignorado, porque GET e HEAD não têm corpo.

A leitura tem dois limites, para que um cliente não prenda a thread nem a memória:
- a requisição inteira precisa chegar em **10 segundos**, contados desde o início, senão o servidor responde **408**;
- a requisição precisa caber em **8 KiB**, senão o servidor responde **431**.

### Estrutura da resposta (D3)

Toda resposta segue a ordem **linha de status → cabeçalhos → linha em branco (CRLF) → corpo**:

```text
HTTP/1.1 200 OK
Date: Wed, 25 Nov 2026 04:29:44 GMT
Server: ServidorRedes/1.0
Content-Type: image/jpeg
Content-Length: 31739
Connection: close
Cache-Control: no-store

<31739 bytes do arquivo>
```

Os cabeçalhos são montados como texto ASCII, cada linha terminada em CRLF, e convertidos em bytes antes do envio; o corpo segue logo depois.
- **`Date`:** fica no formato IMF-fixdate da RFC 9110, sempre em GMT, com nomes de dia e mês em inglês, para não depender do idioma do sistema.
- **`Connection: close`:** cada conexão atende uma única requisição e é fechada depois da resposta. Isso deixa a correspondência "uma conexão = uma thread = um recurso" visível (ADR 0001).
- **`Cache-Control: no-store`:** faz cada recarga da página chegar de novo ao servidor.

O fechamento é gracioso, como recomenda a RFC 9112. O servidor encerra primeiro o sentido de envio e descarta por um instante o que o cliente ainda manda. Fechar o socket com bytes não lidos faria o sistema enviar um reset, que pode apagar a resposta antes de o navegador lê-la.

### Content-Type e Content-Length (D4 e D5)

**`Content-Type`** é escolhido pela extensão do arquivo, numa tabela escrita no próprio código, sem diferenciar maiúsculas de minúsculas:

| Extensão | Tipo |
|---|---|
| `html`, `htm` | `text/html; charset=utf-8` |
| `css` | `text/css; charset=utf-8` |
| `js` | `text/javascript; charset=utf-8` |
| `txt` | `text/plain; charset=utf-8` |
| `json` | `application/json` |
| `png` | `image/png` |
| `jpg`, `jpeg` | `image/jpeg` |
| `gif` | `image/gif` |
| `svg` | `image/svg+xml` |
| `ico` | `image/x-icon` |
| `webp` | `image/webp` |
| `pdf` | `application/pdf` |
| outra ou nenhuma | `application/octet-stream` |

- **Tabela escrita no código, sem o módulo `mimetypes`:** o resultado fica visível e igual em qualquer máquina (ADR 0003).
- **`charset=utf-8` nos tipos de texto:** sem ele, o navegador precisaria adivinhar a codificação de páginas sem `<meta charset>`, como o modelo do enunciado, e mostraria "vocÃª" no lugar de "você".

**`Content-Length`** é o tamanho exato do corpo em bytes:
- para um arquivo, vem do `fstat` do arquivo já aberto, sem ler o conteúdo;
- para uma página de erro, é o tamanho da página depois de codificada em UTF-8.

Sem esse cabeçalho, com a conexão ainda aberta, o navegador não saberia onde termina a resposta.

### Texto × dados binários (D6)

O corpo **não é tratado de forma diferente para HTML e para imagens**, e isso é intencional (ADR 0002):
- **Envio do recurso:** todo recurso é aberto em modo binário (`rb`) e enviado em blocos de 64 KiB exatamente como está no disco, sem decodificação nem conversão. Só o `Content-Type` diz ao navegador como interpretar os bytes.
- **Por que não ler HTML como texto:** isso exigiria escolher uma codificação e poderia converter quebras de linha. O corpo deixaria de ser idêntico ao arquivo, e o `Content-Length`, calculado pelo tamanho do arquivo, deixaria de bater.
- **Onde fica a fronteira texto × bytes:** nos **cabeçalhos**, texto ASCII montado pelo servidor e codificado explicitamente, e na **página de erro**, texto gerado pelo servidor e codificado em UTF-8. O corpo dos recursos nunca passa por texto.
- **Memória:** o envio em blocos evita carregar o arquivo inteiro. Um teste compara o pico de memória do servidor entregando 1 MiB e 96 MiB, e ele não cresce com o tamanho do arquivo.

### Respostas de sucesso e de erro (D7)

As linhas de status seguem a RFC 9110: `HTTP/1.1 <código> <frase>`, com a frase padrão em inglês. Toda resposta de erro leva uma **página de erro** HTML em português, gerada pelo próprio servidor e não lida da pasta raiz, para que funcione mesmo se `www/` estiver incompleta. Ela tem os mesmos cabeçalhos de qualquer resposta, com `Content-Type: text/html; charset=utf-8` e o `Content-Length` exato. O caminho pedido aparece na página escapado, para não ser interpretado como HTML.

| Código | Quando |
|---|---|
| 200 OK | Recurso entregue |
| 400 Bad Request | Requisição malformada, `%XX` inválido ou caminho que não é UTF-8 |
| 403 Forbidden | Caminho bloqueado (ver D8) |
| 404 Not Found | Recurso inexistente: nada no caminho, uma pasta (exceto a raiz) ou algo que não é arquivo regular |
| 405 Method Not Allowed | Método diferente de GET e HEAD |
| 408 Request Timeout | Requisição incompleta no prazo |
| 431 Request Header Fields Too Large | Requisição acima de 8 KiB |
| 500 Internal Server Error | Recurso existente que o servidor não consegue ler |
| 503 Service Unavailable | Mais de 64 conexões simultâneas |
| 505 HTTP Version Not Supported | Versão diferente de HTTP/1.0 e 1.1 |

### Caminho bloqueado: proteção contra path traversal (D8)

O caminho da URL passa por estas etapas antes de qualquer arquivo ser aberto:

1. **Separação:** a query string (`?…`) e o fragmento (`#…`) são descartados.
2. **Decodificação:** cada `%XX` é trocado pelo byte que representa, e o resultado é lido como UTF-8. Codificação inválida recebe 400. A decodificação vem **antes** da verificação: assim `..%2F..%2Fetc%2Fpasswd` vira `../../etc/passwd` e é bloqueado, em vez de passar disfarçado.
3. **Política de caminhos (herdada do Trabalho 2):** o caminho, sem a `/` inicial, não pode ser absoluto nem ter componentes vazios, `.` ou `..`, barra invertida, dois-pontos, caracteres de controle (inclusive o byte nulo) ou quebras de linha. A verificação é feita sobre o texto recebido, sem normalizá-lo antes, para que uma entrada proibida nunca vire outra aceita.
4. **Abertura sem seguir links:** cada pasta do caminho é aberta a partir do descritor da anterior, com `O_NOFOLLOW`. Um link simbólico em qualquer ponto, mesmo apontando para dentro da raiz, bloqueia o pedido. Uma pasta trocada por um link durante a requisição também não é seguida, porque o caminho não é resolvido de novo pelo nome.
5. **Contenção:** por fim, o caminho resolvido precisa estar dentro da pasta raiz, pela relação entre diretórios (`realpath` e `commonpath`), e não por comparação de texto.

Um caminho bloqueado recebe **403** com a página "Acesso negado" e fica registrado no terminal com o alvo recebido, o caminho decodificado e o motivo:

```text
[conexão 2] Caminho bloqueado: /..%2F..%2F..%2Fetc%2Fpasswd (decodificado: '../../../etc/passwd'). o caminho contém componentes vazios, '.' ou '..'
```

**Como demonstrar.** O navegador normaliza `../` e `%2e%2e` antes de enviar a requisição. Digitar `http://localhost:8080/../../etc/passwd` faz o navegador pedir `/etc/passwd`, sem traversal nenhum. Para mostrar o bloqueio:
- **no navegador:** use `%2F`, que o navegador não altera: `http://localhost:8080/..%2F..%2F..%2Fetc%2Fpasswd`;
- **no terminal:** use `curl --path-as-is -i http://localhost:8080/../../etc/passwd`.

## Concorrência

O laço de `accept` entrega cada socket aceito a uma thread dedicada, que guarda o estado da conexão:
- o socket;
- o endereço do cliente;
- o nome da thread (`conexão N`).

Assim, um cliente lento não atrasa os outros. Por exemplo, a imagem grande de `www/grande.png` (cerca de 20 MB), carregada com o throttling do DevTools, mantém a sua thread ocupada enquanto outras abas são atendidas na hora. No registro do terminal, o intervalo de início e fim da imagem contém o das outras requisições.

Também há limites para que o servidor continue estável:
- até 64 conexões simultâneas; acima disso, a conexão recebe 503;
- cada bloco enviado tem um prazo de 30 segundos, e um cliente que para de ler perde só a própria conexão.

O Ctrl+C para de aceitar conexões, fecha as ativas e espera todas as threads terminarem.

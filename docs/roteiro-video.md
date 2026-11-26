# Roteiro do vídeo — Trabalho 3

**Meta: 4min30s, com 30 segundos de margem até o limite de 5 minutos.** São duas partes, nesta ordem e sem voltar: **2 minutos de demonstração no navegador + 2min30s de código no VS Code**. Fale só as frases em **Fala** e faça as ações enquanto fala. Os tempos são metas de ensaio.

Faça toda a preparação da seção [Antes de gravar](#antes-de-gravar) e um ensaio cronometrado antes da gravação. Deixe esta cola fora da área capturada.

**Aba A** e **aba B** = as duas abas do Chrome, cada uma com o DevTools aberto na aba Rede (Network). **T1** = terminal do servidor; **T0** = terminal auxiliar. **BASE** = o endereço que o servidor mostra em "Na rede", por exemplo `http://192.168.0.10:8080`. Todo endereço com BASE é digitado na **barra de endereço** do Chrome, não no console do DevTools, com BASE trocado pelo endereço real: `BASE/grande.png` vira, por exemplo, `http://192.168.0.10:8080/grande.png`.

## Parte 1 — Demonstração, 0:00–2:00

### 0:00–0:40 — Concorrência e mídia (V1, V2)

**Mostrar:** o Chrome e T1 lado a lado.

**Fazer, nesta ordem:**

1. Na **aba A**, que tem o throttling `Demo 10 Mbit/s`: digite `BASE/grande.png` → Enter. A imagem começa a carregar devagar.
2. Na **aba B**, sem throttling: digite `BASE/` → Enter. O índice aparece na hora, com a foto JPEG e o diagrama PNG.
3. Aponte em **T1**: o `Conectado: … na thread conexão N` da imagem grande, ainda sem a linha de requisição, e as conexões seguintes, que já responderam `index.html`, `estilo.css`, `foto.jpg`, `diagrama.png` e `favicon.ico` com 200.

**Fala:**

> Primeira parte: demonstração. Na aba A, com a rede limitada, peço uma imagem de vinte megabytes. Enquanto ela carrega, a aba B abre o índice na hora, com uma foto JPEG e um diagrama PNG. No terminal, cada recurso, inclusive o favicon que o navegador pede sozinho, teve uma conexão e uma thread próprias. A thread da imagem grande continua ocupada sem bloquear as outras.

### 0:40–1:10 — Cabeçalhos na aba Rede (V3)

**Fazer:**

1. Na **aba B**: `BASE/pagina.html` → Enter. A página do enunciado aparece.
2. No DevTools da aba B, clique em `pagina.html` → **Headers** → **Response Headers**, com **Raw** marcado. Aponte `HTTP/1.1 200 OK`, `Content-Type: text/html; charset=utf-8` e `Content-Length: 251`.
3. Clique em `foto.jpg`, que ficou no log: aponte `Content-Type: image/jpeg` e `Content-Length: 31739`.
4. Em **T0**: `tamanhos` → Enter. Aponte **251** e **31739**.
5. Em **T1**, se a imagem grande já terminou: aponte a linha `"GET /grande.png HTTP/1.1" 200 20302470 bytes`, com início antes e fim depois das outras. Se ainda não terminou, aponte-a no bloco seguinte.

**Fala:**

> Na aba Rede, a página veio com HTTP/1.1 200 OK, Content-Type text/html e Content-Length 251. A foto veio como image/jpeg, com 31739. O tipo muda conforme a extensão, e os tamanhos batem com os arquivos no disco. No terminal, a imagem grande terminou agora: começou antes e acabou depois das outras requisições.

### 1:10–1:30 — Erro 404 (V4)

**Fazer:** na **aba B**: `BASE/nao-existe.html` → Enter. Aponte a página **Recurso não encontrado** e, na aba Rede, o status **404 Not Found** com `Content-Type: text/html`.

**Fala:**

> Um arquivo que não existe recebe 404 Not Found, com uma página de erro em HTML gerada pelo servidor.

### 1:30–2:00 — Acesso fora da pasta raiz (V5)

**Fazer:**

1. Na **aba B**: `BASE/..%2F..%2F..%2Fetc%2Fpasswd` → Enter. Aponte **Acesso negado** e o status **403 Forbidden**.
2. Em **T1**: aponte `Caminho bloqueado: /..%2F..%2F..%2Fetc%2Fpasswd (decodificado: '../../../etc/passwd')`.
3. Em **T0**: `traversal` → Enter. Aponte `HTTP/1.1 403 Forbidden`.

**Fala:**

> Agora, uma tentativa de sair da pasta raiz. O navegador elimina o ponto-ponto da URL antes de enviar, então uso a barra codificada. O servidor decodifica, encontra o ponto-ponto e responde 403. Pelo curl, com o ponto-ponto puro, o resultado é o mesmo.

## Parte 2 — Código no VS Code, 2:00–4:30

Maximize o VS Code. As duas abas já estão abertas: `server.py` e `protocol.py` **da pasta de gravação**, as cópias sem comentários.

**Para cada linha da tabela:** clique na aba → **Ctrl+G** → número → **Enter**. Enquadre o intervalo indicado, rolando um pouco se precisar, e aponte as linhas enquanto fala. São **10 enquadramentos**; não acrescente outros.

| Tempo | Aba → Ctrl+G | Mostrar e apontar | Fala |
| --- | --- | --- | --- |
| **2:00–2:15** | `server.py` → **323** | **322–334**: `socket.socket(AF_INET, SOCK_STREAM)` **323**, `bind` **326**, `listen` **327**. | Segunda parte: código. O socket de escuta é criado aqui. AF\_INET é IPv4, e SOCK\_STREAM é o identificador que garante TCP; UDP seria SOCK\_DGRAM. Depois vêm bind e listen. |
| **2:15–2:45** | `server.py` → **366** | **357–388**: `accept` **363**, `_open_connection` **366**, `Connection` **377**, início da thread **388**. | A thread de aceitação espera no accept. Cada socket aceito vira um objeto Connection com uma thread própria, iniciada aqui, e o laço volta logo para o accept. |
| Mesmo bloco | `server.py` → **145** | **132–175**: socket **135**, endereço **136**, `Thread(target=self._serve)` **145**, `_serve` **172**. | O estado da conexão fica nesse objeto: o socket, o endereço do cliente e a thread, que executa \_serve só com esse socket. Por isso uma conexão lenta não segura as outras. |
| **2:45–3:15** | `server.py` → **121** | `os.fdopen(descriptor, "rb")` **121**. | Todo recurso é aberto em modo binário, rb. |
| Mesmo bloco | `server.py` → **242** | **226–260**: `fstat` **239**, cabeçalho **240**, `_send_body` **242**, blocos **259–260**. | O corpo vai em blocos de 64 kibibytes, exatamente como está no disco, sem decodificar nada. HTML e imagem seguem o mesmo caminho; só o Content-Type muda. Ler a imagem como texto trocaria bytes e corromperia o arquivo. |
| **3:15–3:45** | `protocol.py` → **198** | **179–209**: página de erro `encode("utf-8")` **195**; linha de status **200**, `Content-Type` **203**, `Content-Length` **204**, linha em branco e `encode("ascii")` **209**. | O que é texto é codificado explicitamente: a página de erro em UTF-8 e o cabeçalho em ASCII. O cabeçalho tem a linha de status, o Content-Type e o Content-Length, e termina com a linha em branco. |
| Mesmo bloco | `protocol.py` → **157** | `content_type` **157–159**. | O Content-Type sai da extensão do arquivo, procurada na tabela de tipos. O Content-Length vem do fstat do arquivo aberto, na linha 239 do servidor; numa página de erro, é o tamanho dela em bytes. |
| **3:45–4:10** | `server.py` → **58** | **44–71**: política de caminhos **46**, `os.stat` **58**, falha **60**, arquivo regular **63**. | Para o 404, o servidor consulta o recurso com stat, dentro da pasta raiz. Se nada existe ali, ou se não é um arquivo regular, o pedido é recusado como recurso inexistente. |
| Mesmo bloco | `server.py` → **246** | **226–256**: `open_resource` **233**, recusa **235**, status **246**, `_send_error` **249**, página **252–253**. | A recusa chega aqui e vira o status 404. Então o servidor monta a página de erro e a envia com o mesmo cabeçalho de sempre e o Content-Length da página. |
| **4:10–4:30** | `protocol.py` → **239** | **226–250**: componentes `.` e `..` **239–240**; `commonpath` **246**. Termine nessa tela. | Por fim, a proteção contra path traversal. Depois de decodificar os %XX, esta linha recusa componentes vazios, ponto e ponto-ponto, antes de abrir qualquer arquivo. E aqui o caminho resolvido precisa continuar dentro da pasta raiz. |

**Pare a gravação ao terminar.** Use a margem para operações que demorarem mais; não a preencha com novas explicações. Depois de parar, encerre o servidor com Ctrl+C em T1.

## Cola de navegação

```
PRÁTICA
A: BASE/grande.png → B: BASE/ → T1: conexões
B: BASE/pagina.html → Rede: pagina.html, foto.jpg → T0: tamanhos → T1: grande terminou
B: BASE/nao-existe.html → 404
B: BASE/..%2F..%2F..%2Fetc%2Fpasswd → 403 → T1: bloqueado → T0: traversal

VS CODE — clicar na aba e usar Ctrl+G
server 323 → 366 → 145 → 121 → 242
protocol 198 → 157
server 58 → 246
protocol 239 → parar
```

## Linhas citadas

As linhas são das cópias sem comentários geradas por `scripts/preparar_gravacao.py`, não dos fontes documentados. O teste `test_entrega.RecordingCopiesTest.test_lines_cited_in_the_video_script_hold_the_code_it_names` confere cada linha desta tabela; se ele falhar, o código mudou e o roteiro precisa ser atualizado antes de gravar.

| Arquivo | Linha | Código |
| --- | --- | --- |
| `server.py` | 46 | `p.check_relative_path(path)` |
| `server.py` | 58 | `mode = os.stat(parts[-1], dir_fd=folder, follow_symlinks=False).st_mode` |
| `server.py` | 60 | `raise _refusal_for(error, shown) from None` |
| `server.py` | 63 | `if not stat.S_ISREG(mode):` |
| `server.py` | 121 | `return os.fdopen(descriptor, "rb")` |
| `server.py` | 135 | `self.sock = sock` |
| `server.py` | 136 | `self.address = address` |
| `server.py` | 145 | `self._thread = threading.Thread(target=self._serve, name=name)` |
| `server.py` | 172 | `def _serve(self):` |
| `server.py` | 233 | `file = open_resource(self._root, path)` |
| `server.py` | 235 | `return self._refuse(request, path, refusal)` |
| `server.py` | 239 | `size = os.fstat(file.fileno()).st_size` |
| `server.py` | 240 | `self.sock.sendall(p.response_head(200, p.content_type(path), size))` |
| `server.py` | 242 | `self._send_body(file)` |
| `server.py` | 246 | `status = p.REFUSAL_STATUS[refusal.code]` |
| `server.py` | 249 | `return self._send_error(request.method, status, p.explanation(status, request.target))` |
| `server.py` | 252 | `page = p.error_page(status, explanation)` |
| `server.py` | 253 | `self.sock.sendall(p.response_head(status, p.CONTENT_TYPES["html"], len(page)` |
| `server.py` | 259 | `while block := file.read(BLOCK_SIZE):` |
| `server.py` | 260 | `self.sock.sendall(block)` |
| `server.py` | 323 | `listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)` |
| `server.py` | 326 | `listener.bind((self._host, self._port))` |
| `server.py` | 327 | `listener.listen()` |
| `server.py` | 363 | `sock, address = self._listener.accept()` |
| `server.py` | 366 | `self._open_connection(sock, f"{address[0]}:{address[1]}")` |
| `server.py` | 377 | `connection = Connection(sock, address, f"conexão {number}"` |
| `server.py` | 388 | `connection.start()` |
| `protocol.py` | 157 | `def content_type(path):` |
| `protocol.py` | 158 | `extension = os.path.splitext(path)[1].lower().removeprefix(".")` |
| `protocol.py` | 159 | `return CONTENT_TYPES.get(extension, UNKNOWN_TYPE)` |
| `protocol.py` | 195 | `return page.encode("utf-8")` |
| `protocol.py` | 198 | `def response_head(status, content_type, length, extra=()):` |
| `protocol.py` | 200 | `f"HTTP/1.1 {status} {REASONS[status]}",` |
| `protocol.py` | 203 | `f"Content-Type: {content_type}",` |
| `protocol.py` | 204 | `f"Content-Length: {length}",` |
| `protocol.py` | 209 | `return (LINE_END.join(lines) + LINE_END + LINE_END).encode("ascii")` |
| `protocol.py` | 239 | `if any(part in ("", ".", "..") for part in path.split("/")):` |
| `protocol.py` | 240 | `raise BlockedPath("o caminho contém componentes vazios, '.' ou '..'")` |
| `protocol.py` | 246 | `inside = os.path.commonpath([root, os.path.realpath(target)]) == root` |

## Cobertura

| Exigência | Onde |
| --- | --- |
| V1 — Duas abas sem bloqueio | 0:00–0:40; intervalo da imagem grande em 0:40–1:10 |
| V2 — HTML, JPEG e PNG | 0:00–0:40 |
| V3 — `200 OK`, `Content-Type` dinâmico e `Content-Length` exato | 0:40–1:10 |
| V4 — Página de erro 404 | 1:10–1:30 |
| V5 — Bloqueio fora da raiz | 1:30–2:00 |
| E1 — Criação do socket e `SOCK_STREAM` | 2:00–2:15 |
| E2 — `accept` → thread dedicada e estado da conexão | 2:15–2:45 |
| E3 — Texto × binário | 2:45–3:15; codificação explícita do texto em 3:15–3:45 |
| E4 — Montagem de `Content-Type` e `Content-Length` | 3:15–3:45; `fstat` já apontado em 2:45–3:15 |
| E5 — Verificação de existência e resposta 404 | 3:45–4:10 |
| E6 — Linha que sanitiza o caminho | 4:10–4:30 |
| R1 — Duas partes, sem voltar | Transição em 2:00 |
| R3 — Código sem comentários | Cópias da pasta de gravação |

## Antes de gravar

### 1. Preparar as cópias, em T0

```
cd /home/taronky/Documents/UT/Redes/Trabalho3
DEMO="$(mktemp -d /tmp/trabalho3-video.XXXXXX)"
python3 scripts/preparar_gravacao.py "$DEMO"
printf '%s\n' "$DEMO" > /tmp/trabalho3-video-atual.txt
```

O script copia `server.py` e `protocol.py` sem comentários nem docstrings, junto com `www/`, e confere pela AST que a lógica é a mesma dos fontes.

### 2. Iniciar o servidor, em T1

```
DEMO="$(cat /tmp/trabalho3-video-atual.txt)"
cd "$DEMO" && python3 server.py
```

Anote o endereço de **Na rede**: é o BASE do roteiro. Usar o IP, e não `localhost`, mostra a URL `http://[IP_do_Servidor]:[Porta]/arquivo.html` pedida no enunciado.

### 3. Definir os atalhos, em T0

Troque o endereço pelo que T1 mostrou e cole o bloco:

```
DEMO="$(cat /tmp/trabalho3-video-atual.txt)"
BASE="http://192.168.0.10:8080"
tamanhos() { stat -c '%n %s bytes' "$DEMO/www/pagina.html" "$DEMO/www/foto.jpg"; }
traversal() { curl --path-as-is -i "$BASE/../../etc/passwd"; }
```

Teste os dois atalhos uma vez e limpe o terminal com `clear`.

### 4. Preparar o Chrome

1. Abra uma janela com duas abas em `about:blank` e o DevTools (F12) na aba **Rede** em cada uma. As abas ficam vazias até a gravação: assim o DevTools e o throttling já estão prontos quando o primeiro endereço for digitado, e a aba Rede registra o pedido desde o início.
2. Nas duas abas, marque **Disable cache**. Na aba B, marque também **Preserve log**, para que `foto.jpg` continue na lista depois de abrir `pagina.html`.
3. Crie um perfil de throttling: DevTools → ⚙ Settings → **Throttling** → **Add profile**, com nome `Demo 10 Mbit/s`, Download `10000` kbit/s, Upload `10000` kbit/s e Latency `0`. Selecione-o **só na aba A**.
4. No ensaio, `grande.png` deve levar de 15 a 20 segundos. Se terminar antes de você abrir o índice na aba B, reduza o Download para `5000`.
5. Com o DevTools aberto, o Chrome pede `/.well-known/appspecific/com.chrome.devtools.json`, que recebe 404 normalmente. Ignore essa linha em T1.

### 5. Preparar o VS Code

Abra `server.py` e `protocol.py` **de** **`$DEMO`**, nunca os do repositório, que têm comentários. Ajuste o zoom para que caibam cerca de 35 linhas, e feche a barra lateral e o painel do terminal.

### 6. Organizar a tela

Para a parte 1, deixe o Chrome à esquerda e T1 à direita, com T0 abaixo de T1. Para a parte 2, maximize o VS Code. Faça um ensaio completo com cronômetro; se passar de 4min45s, corte as pausas, não os itens.

## Depois de gravar

- Publique o vídeo no YouTube, de preferência como **Não listado**, e confira que o link abre em uma janela anônima.
- No Classroom, envie **só o link** do YouTube nos **comentários particulares** da atividade. **Não envie o arquivo de vídeo.**
- Gere o ZIP de novo com `python3 scripts/empacotar.py` e poste `dist/trabalho3.zip` na atividade do Classroom.
- Registre a duração, o link e as datas de envio na seção 4 de [aceitacao.md](aceitacao.md).

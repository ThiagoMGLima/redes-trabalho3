# Registro de aceitação — Trabalho 3

Spec #1, ticket #10. Liga cada exigência de [requisitos.md](requisitos.md) à sua evidência: um teste, uma verificação manual ou um trecho do vídeo.

- **Código verificado:** `server.py` e `protocol.py` de `integracao/spec-1` @ 0b01135 (tickets #2 a #9 e as correções do code-review final), mais o teste das linhas do roteiro acrescentado neste ticket.
- **Ambiente:** Linux 7.0.0-44-generic, Python 3.14.6, só a biblioteca padrão.

`requisitos.md` lista **37 itens** (G1–G3, S1–S7, C1–C2, D1–D8, V1–V5, E1–E6, R1–R6); o ticket #10 fala em 39, mas a lista tem 37, e todos estão aqui.

## 1. Execuções da suíte

| Comando, na raiz | Resultado |
|---|---|
| `timeout 900 python3 -m unittest`, 3 execuções | `Ran 93 tests` em 30,5 s, 30,5 s e 30,5 s; `OK`, nenhum teste pulado |
| `grep -nE "^(import\|from)" server.py protocol.py` | Só módulos da biblioteca padrão; nenhum de HTTP, de abstração de sockets nem `mimetypes` (G2) |

Antes do teste novo, a mesma suíte rodou 3 vezes com `Ran 92 tests` em 30,5 s, 30,5 s e 30,3 s, `OK`.

O teste novo, `test_entrega.RecordingCopiesTest.test_lines_cited_in_the_video_script_hold_the_code_it_names`, lê a tabela "Linhas citadas" de [roteiro-video.md](roteiro-video.md) e confere cada linha nas cópias sem comentários. Ele falhou antes de o roteiro existir. Com a linha 323 trocada por 324 no roteiro, falhou com `'listener = socket.socket(…)' not found in '    try:'`; a mutação foi revertida.

## 2. Requisitos e evidências

Os testes ficam em `tests/`. O README tem uma seção para cada decisão de projeto (D1–D8), e `docs/adr/` registra as três decisões estruturais.

### Requisitos gerais e restrições

| Item | Evidência | Estado |
|---|---|---|
| G1 — Base do Trabalho 2 | Commit 1af0c00 ("start Trabalho 3 from the final code of Trabalho 2"), com o histórico do Trabalho 2 preservado; ticket #2 reduziu o servidor TCP multithread à base HTTP, mantendo o laço de `accept` e a thread por conexão | ✅ |
| G2 — Sem bibliotecas de alto nível | Imports da seção 1; socket criado com `socket.socket(AF_INET, SOCK_STREAM)`; ADR 0003; os testes também falam com o servidor por sockets crus | ✅ |
| G3 — Parsing e resposta pelo próprio código | `protocol.parse_request`, `request_path`, `response_head` e `error_page`; `test_http` e `test_robustness` conferem os bytes enviados e recebidos | ✅ |

### Servidor HTTP

| Item | Evidência | Estado |
|---|---|---|
| S1 — Porta > 1024 | `test_server.PortTest` (8080 por padrão; fora de 1025–65535 é recusada; porta em uso é informada); `ConnectionTest.test_server_listens_on_every_ipv4_interface_by_default`; `StartupMessageTest` | ✅ |
| S2 — Uma thread por conexão | `test_server.ConnectionTest.test_each_accepted_connection_is_served_by_its_own_dedicated_thread` | ✅ |
| S3 — Leitura e parsing da requisição | `test_http.SuccessfulGetTest`: `test_request_arriving_in_pieces_…`, `test_headers_from_the_browser_are_accepted`; `test_robustness.MalformedRequestTest` e `UnsupportedRequestTest` | ✅ |
| S4 — Cabeçalho correto + conteúdo | `test_http.SuccessfulGetTest`: `test_get_of_an_html_resource_gets_200_and_exactly_the_bytes_of_the_file`, `test_response_carries_the_six_headers_…` | ✅ |
| S5 — Conexões simultâneas, inclusive favicon | `test_concurrency.SimultaneousConnectionsTest`; `test_limits.ConnectionLimitTest`; `test_http.DemonstrationSiteTest` (inclui `favicon.ico`); verificação manual M5 | ✅ |
| S6 — Binário sem corrupção | `test_http.BinaryContentTest`; `DemonstrationSiteTest.test_every_resource_of_the_site_is_served_whole_with_its_type` e `test_images_are_real_jpeg_png_and_ico_files`; `test_memory` | ✅ |
| S7 — Erros, no mínimo 404 com página HTML | `test_http.NotFoundTest`; `test_robustness` (400, 405, 408, 431, 500, 505); `test_paths.BlockedPathTest` (403); `test_limits` (503) | ✅ |

### Cliente (browser)

| Item | Evidência | Estado |
|---|---|---|
| C1 — Navegador comum | Chrome headless nos tickets #3, #4, #5 e #9; Chrome do usuário na verificação do ticket #8; verificações manuais M1–M5 | ✅ |
| C2 — `http://IP:porta/arquivo.html` renderiza | `test_http.StatementTemplateTest`; Chrome do usuário abrindo `/pagina.html` (#8); no vídeo, pelo endereço "Na rede" (0:40–1:10) | ✅ |

### Design do protocolo

| Item | Evidência | Estado |
|---|---|---|
| D1 — Linha de requisição e cabeçalhos | README, "Parsing da requisição (D1)"; `test_robustness.MalformedRequestTest`, `UnsupportedRequestTest.test_line_is_checked_before_the_version_…`, `test_header_names_are_not_case_sensitive` | ✅ |
| D2 — Fim dos cabeçalhos (CRLF CRLF) | README, "Fim dos cabeçalhos (D2)"; `test_request_arriving_in_pieces_…`, `test_bytes_after_the_blank_line_are_ignored`, `test_line_feeds_without_carriage_return_do_not_end_the_request`; `OversizedRequestTest`; `DeadlineTest` | ✅ |
| D3 — Status → cabeçalhos → linha em branco → corpo | README, "Estrutura da resposta (D3)"; `test_response_carries_the_six_headers_…`; `test_date_is_the_current_time_in_the_imf_fixdate_format`; ADR 0001 | ✅ |
| D4 — `Content-Type` pela extensão e `Content-Length` | README, "Content-Type e Content-Length (D4 e D5)"; `test_http.ContentTypeTest`; ADR 0003 | ✅ |
| D5 — `Content-Length` exato | `test_response_carries_the_six_headers_with_the_exact_length_…`; `BinaryContentTest.test_file_larger_than_one_block_…` e `test_empty_file_gets_length_zero_…`; `HeadTest`; verificação manual M1 | ✅ |
| D6 — Texto × binário | README, "Texto × dados binários (D6)"; ADR 0002; `BinaryContentTest.test_bytes_that_are_not_text_arrive_unchanged`; `test_memory` | ✅ |
| D7 — `200 OK` e erros formatados | README, "Respostas de sucesso e de erro (D7)"; `NotFoundTest`; `test_robustness`; `HeadTest.test_head_of_a_missing_resource_…` | ✅ |
| D8 — Path traversal | README, "Caminho bloqueado (D8)"; `test_paths.BlockedPathTest`, `ContainmentTest` e `TargetDecodingTest`; verificação manual M4 | ✅ |

### Vídeo — demonstração

| Item | Evidência | Estado |
|---|---|---|
| V1 — Duas abas sem bloqueio | Roteiro 0:00–1:10; verificação manual M5 | ⏳ vídeo |
| V2 — HTML, JPEG e PNG | Roteiro 0:00–0:40; verificação manual M2 | ⏳ vídeo |
| V3 — Aba Rede: status, tipo e tamanho | Roteiro 0:40–1:10; verificação manual M1 | ⏳ vídeo |
| V4 — Página 404 | Roteiro 1:10–1:30; verificação manual M3 | ⏳ vídeo |
| V5 — Bloqueio fora da raiz | Roteiro 1:30–2:00; verificação manual M4 | ⏳ vídeo |

### Vídeo — código e defesa oral

As linhas são das cópias sem comentários e estão travadas pelo teste da seção 1.

| Item | Trecho do roteiro | Estado |
|---|---|---|
| E1 — Socket e `SOCK_STREAM` | 2:00–2:15, `server.py` 323 | ⏳ vídeo |
| E2 — `accept` → thread e estado | 2:15–2:45, `server.py` 363–388 e 135–172 | ⏳ vídeo |
| E3 — Texto × binário | 2:45–3:15, `server.py` 121 e 259–260; `protocol.py` 195 e 209 | ⏳ vídeo |
| E4 — `Content-Type` e `Content-Length` | 3:15–3:45, `protocol.py` 157–159 e 198–209; `server.py` 239–240 | ⏳ vídeo |
| E5 — Existência e 404 | 3:45–4:10, `server.py` 58–63 e 233–253 | ⏳ vídeo |
| E6 — Sanitização do caminho | 4:10–4:30, `protocol.py` 239–240 e 246 | ⏳ vídeo |

### Regras do vídeo e da entrega

| Item | Evidência | Estado |
|---|---|---|
| R1 — Duas partes, nessa ordem | Roteiro: demonstração 0:00–2:00, código 2:00–4:30 | ⏳ vídeo |
| R2 — No máximo 5 minutos | Roteiro com meta de 4min30s; duração do ensaio e do vídeo na seção 4 | ⏳ ensaio |
| R3 — Código sem comentários | `scripts/preparar_gravacao.py`; `test_entrega.RecordingCopiesTest` (sem comentários nem docstrings, lógica idêntica pela AST) | ⏳ vídeo |
| R4 — Link do YouTube nos comentários particulares | Seção 4 | ⏳ envio |
| R5 — ZIP com o código-fonte no Classroom | `scripts/empacotar.py`; `test_entrega.SourcePackageTest` (conteúdo e suíte rodando do ZIP extraído) | ⏳ envio |
| R6 — Sem vestígios de IA; domínio do código | Cópias sem comentários (R3); a equipe ensaia a parte 2 até explicar cada linha citada sem ler | ⏳ equipe |

## 3. Verificações manuais no navegador

Feitas pelo usuário, no Chrome, com o servidor da pasta de gravação (passos 1 e 2 de "Antes de gravar" no roteiro). Preencha o resultado e a data de cada uma.

| # | Verificação | Como | Esperado | Resultado | Data |
|---|---|---|---|---|---|
| M1 | Aba Rede: status, tipo e tamanho | Abrir `BASE/pagina.html`; Headers → Response Headers com Raw; em T0, `stat -c %s www/pagina.html` | `HTTP/1.1 200 OK`, `Content-Type: text/html; charset=utf-8`, `Content-Length: 251` = `stat` | | |
| M2 | Renderização das imagens | Abrir `BASE/` | Foto JPEG e diagrama PNG visíveis; na Rede, `image/jpeg` e `image/png` com 200 | | |
| M3 | Página 404 | Abrir `BASE/nao-existe.html` | Página "Recurso não encontrado"; status 404 | | |
| M4 | 403 com `%2F` | Abrir `BASE/..%2F..%2F..%2Fetc%2Fpasswd`; em T0, `curl --path-as-is -i BASE/../../etc/passwd` | Página "Acesso negado", status 403; `Caminho bloqueado` em T1; `HTTP/1.1 403 Forbidden` no curl | | |
| M5 | Duas abas simultâneas | `BASE/grande.png` na aba A com throttling; `BASE/` na aba B | Aba B carrega na hora; em T1, o intervalo de início e fim de `grande.png` contém o das outras requisições | | |

**Evidência anterior:** no ticket #8, o usuário confirmou M5 no Chrome com throttling. A conexão 14 continuou ocupada com `grande.png` enquanto a conexão 16 atendeu `/pagina.html` e terminou.

## 4. Vídeo e entrega

| Etapa | Registro |
|---|---|
| Ensaio cronometrado (meta 4min30s, limite 5min) | |
| Duração do vídeo publicado | |
| Link do YouTube | |
| Link enviado nos comentários particulares do Classroom (data) | |
| `dist/trabalho3.zip` regenerado e postado no Classroom (data) | |

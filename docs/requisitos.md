# Requisitos do enunciado — Trabalho 3

Fonte: `Enunciado/cursos_icsr30_trab1.2 [Prof. Mauro Fonseca - UTFPR - DAINF].pdf` (página modificada em 2026-11-05).

Cada item é uma exigência do professor e precisa estar cumprido na entrega. Marque `[x]` só quando houver evidência (teste, registro de aceitação ou trecho do vídeo) e cite-a na coluna de observação da issue correspondente.

## 1. Requisitos gerais e restrições

- [ ] **G1** — O código do Trabalho 2 (servidor TCP multithread) é a base do Trabalho 3.
- [ ] **G2** — Sem bibliotecas de alto nível que abstraiam sockets ou HTTP; sockets TCP manipulados diretamente pela API do SO/linguagem.
- [ ] **G3** — Nada que mascare o funcionamento do HTTP: parsing da requisição e montagem da resposta feitos pelo próprio código.

## 2. Servidor HTTP

- [ ] **S1** — O servidor é executado primeiro e aguarda conexões em porta > 1024.
- [ ] **S2** — Cada conexão aceita recebe uma thread dedicada.
- [ ] **S3** — A thread lê os dados enviados pelo browser e faz o parsing da requisição HTTP (ex.: `GET /pagina.html HTTP/1.1`).
- [ ] **S4** — A resposta é o cabeçalho HTTP correto seguido pelo conteúdo do arquivo.
- [ ] **S5** — Múltiplas conexões simultâneas são tratadas, inclusive as requisições automáticas do browser (ex.: `favicon.ico`).
- [ ] **S6** — Recursos binários (JPEG/PNG) são entregues sem corrupção.
- [ ] **S7** — Situações de erro são tratadas (no mínimo 404 com página HTML de erro).

## 3. Cliente (browser)

- [ ] **C1** — Funciona com um navegador comum (Chrome, Firefox etc.).
- [ ] **C2** — A URL `http://[IP_do_Servidor]:[Porta]/arquivo.html` exibe o arquivo renderizado.

## 4. Design do protocolo — projetar e justificar

Cada decisão fica registrada (ADR ou spec) e é defendida no vídeo.

- [ ] **D1** — Parsing: como ler e interpretar a linha de requisição (método, caminho, versão) e os cabeçalhos.
- [ ] **D2** — Parsing: como delimitar o fim dos cabeçalhos (linha em branco / CRLF).
- [ ] **D3** — Resposta: estrutura Linha de Status → Cabeçalhos → Linha em Branco (CRLF) → Conteúdo.
- [ ] **D4** — Resposta: `Content-Type` e `Content-Length` obrigatórios; `Content-Type` determinado dinamicamente pela extensão.
- [ ] **D5** — `Content-Length` é o tamanho exato do corpo em bytes (sem ele o browser fica carregando).
- [ ] **D6** — Binário: diferença entre enviar texto (HTML) e dados binários (JPEG/PNG), sem corrupção de bytes.
- [ ] **D7** — Controle e erros: respostas `HTTP/1.x 200 OK` e de erro (ex.: `404 Not Found`) formatadas corretamente.
- [ ] **D8** — Path traversal: sanitização do caminho impede acesso fora da pasta raiz (ex.: `../../arquivo_do_sistema`).

## 5. Vídeo — Parte 1: demonstração prática

- [ ] **V1** — Multithreading e concorrência: pelo menos duas abas (ou recursos) atendidas simultaneamente, sem que uma conexão bloqueie a outra. *(2,00)*
- [ ] **V2** — Mídia e binário: browser renderizando HTML **e** imagens JPEG/PNG. *(2,00)*
- [ ] **V3** — Cabeçalhos: na aba Rede (Network), mostrar `HTTP/1.x 200 OK` (ou erro pertinente), `Content-Type` dinâmico e `Content-Length` exato. *(2,00)*
- [ ] **V4** — Erro 404: browser exibindo a página HTML de erro enviada pelo servidor para arquivo inexistente. *(2,00)*
- [ ] **V5** — Segurança: servidor bloqueando tentativa de acesso fora da pasta raiz. *(2,00)*

## 6. Vídeo — Parte 2: código e defesa oral

Navegar pelo código-fonte e responder oralmente:

- [ ] **E1** — API de sockets: onde o socket TCP é criado e o identificador que garante que é TCP.
- [ ] **E2** — Multithreading: onde o loop do `accept` delega o socket a uma thread dedicada e como o estado da conexão é mantido.
- [ ] **E3** — Binário: diferença no código entre envio de texto e de imagens, e como isso evita corrupção.
- [ ] **E4** — Cabeçalhos: onde `Content-Type` (pela extensão) e `Content-Length` (bytes exatos) são gerados e montados.
- [ ] **E5** — 404: lógica de verificação de existência do arquivo e montagem da resposta de erro.
- [ ] **E6** — Path traversal: a linha que sanitiza o caminho e impede acesso fora da raiz.

## 7. Regras do vídeo e da entrega

- [ ] **R1** — Vídeo dividido estritamente em duas partes: demonstração, depois código.
- [ ] **R2** — Vídeo com no máximo 5 minutos.
- [ ] **R3** — Código exibido no vídeo **sem comentários** (penalização se houver).
- [ ] **R4** — Vídeo publicado no YouTube; enviar apenas o link nos comentários particulares da atividade no Classroom (não enviar o arquivo de vídeo).
- [ ] **R5** — Arquivo `.ZIP` com todo o código-fonte postado no Classroom (comentários livres nesse arquivo).
- [ ] **R6** — Alerta do professor: vestígios de código gerado por IA resultam em nota zero. A equipe precisa dominar e saber explicar cada linha.

## 8. Distribuição de pontos

| Seção | Valor | Itens |
|---|---|---|
| Multithreading e Concorrência | 2,00 | S2, S5, V1, E2 |
| Exibição de Mídia e Tratamento Binário | 2,00 | S6, D6, V2, E3 |
| Cabeçalhos HTTP e Inspeção de Protocolo | 2,00 | D3–D5, D7, V3, E4 |
| Tratamento de Erro 404 | 2,00 | S7, V4, E5 |
| Segurança Básica e Robustez | 2,00 | D8, V5, E6 |

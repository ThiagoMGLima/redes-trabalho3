# Roteiro da verificação manual em terminais (#17)

As seis verificações manuais da resolução da #7, com servidor e dois clientes em terminais separados. Anote os resultados na seção "Verificação manual" de `docs/aceitacao.md`.

## Preparação

Num terminal auxiliar, na raiz do projeto:

```sh
V="$HOME/verificacao-t2"
for d in servidor cliente-a cliente-b; do mkdir -p "$V/$d"; cp protocol.py server.py client.py "$V/$d/"; done
mkdir -p "$V/servidor/files" "$V/cliente-a/downloads" "$V/cliente-b/downloads"
truncate -s 1G "$V/servidor/files/grande.bin"
printf 'arquivo pequeno\n' > "$V/servidor/files/pequeno.txt"
```

- As cópias ficam fora do repositório, para que o arquivo grande não entre no projeto.
- `grande.bin` é esparso e não ocupa disco no servidor, mas cada download completo grava 1 GiB na pasta do cliente.

Na conexão local, 1 GiB chega em cerca de 2 s, rápido demais para agir durante o download. Por isso o servidor roda com uma cota de 1% de CPU; os programas não mudam. Neste ambiente, com a cota, 1 GiB levou 151 s, e o servidor levou cerca de 4 s para mostrar "Servidor escutando". Se o download terminar rápido demais, aumente o arquivo (`truncate -s 4G ...`). Sem `systemd-run`, rode `python3 server.py` sem a cota e use um arquivo bem maior, por exemplo de 20 GiB, conferindo antes o espaço livre.

| Terminal | Comando |
|---|---|
| Servidor | `cd "$HOME/verificacao-t2/servidor" && systemd-run --user --scope -q -p CPUQuota=1% python3 server.py` |
| Cliente A | `cd "$HOME/verificacao-t2/cliente-a" && python3 client.py` |
| Cliente B | `cd "$HOME/verificacao-t2/cliente-b" && python3 client.py` |

Conecte A antes de B, para que A seja o Cliente 1 e B o Cliente 2. No menu, `1` é Sair, `2` é Arquivo e `3` é Chat.

## 1. Recebimento antes de Enter

1. Deixe B parado no menu, sem digitar nada.
2. Em A, digite `3` e depois `oi do A`. No servidor, digite `aviso 1`.
3. Em B, digite `3` e escreva `texto parcial` **sem pressionar Enter**.
4. Em A, digite `3` e depois `segundo oi do A`. No servidor, digite `aviso 2`.
5. Em B, pressione Enter.

Esperado:
- B mostra, cada uma em nova linha e assim que é enviada, `[Cliente 1] oi do A`, `[Servidor] aviso 1`, `[Cliente 1] segundo oi do A` e `[Servidor] aviso 2`, sem que se pressione Enter.
- No passo 5, B envia `texto parcial` e mostra `[Você] texto parcial`.

## 2. Origens e confirmação local

Use as mensagens do item 1.

- O servidor registrou `Conectado: … como Cliente 1` e `… como Cliente 2` e mostra `[Cliente 1] oi do A` e `[Cliente 2] texto parcial`.
- A mostra `[Você] oi do A`, não recebe a própria mensagem de volta e mostra `[Cliente 2] texto parcial`.
- A e B mostram `[Servidor] aviso 1` e `[Servidor] aviso 2`.
- `[Você]` confirma só o envio, não a entrega aos demais.

## 3. Chat volta ao menu, também durante um download

1. Em A, digite `3` e depois `ida e volta`. Esperado: `[Você] ida e volta`, seguido do menu.
2. Em B, digite `2` e depois `grande.bin`. Esperado: `Arquivo grande.bin solicitado.`, seguido do menu.
3. Com o download de B ativo:
   - em B, digite `3` e depois `B durante o download`; esperado: `[Você] B durante o download`, seguido do menu;
   - em A, digite `3` e depois `A durante o download de B`;
   - no servidor, digite `aviso durante o download`.
4. Esperado: B mostra `[Cliente 1] A durante o download de B` e `[Servidor] aviso durante o download` antes de `Download concluído: grande.bin publicado em …`.
5. Depois da conclusão, no terminal auxiliar: `cmp "$V/servidor/files/grande.bin" "$V/cliente-b/downloads/grande.bin" && echo iguais`.

## 4. Menu, segundo download e opções com download ativo

1. Em A, confira as opções `1 - Sair`, `2 - Arquivo` e `3 - Chat`.
2. Em A, digite `2` e depois `grande.bin`.
3. Com esse download ativo, digite `2` em A. Esperado: `Já existe um download ativo (grande.bin); …`, sem pedir caminho.
4. Em A, digite `3` e depois `chat com download ativo`. Esperado: `[Você] chat com download ativo`, e B recebe a mensagem.

Siga para o item 5 com o download de A ainda ativo.

## 5. Sair durante a transferência

1. No terminal auxiliar, `ls -la "$V/cliente-a/downloads"` mostra um temporário `.<hex>.parcial`.
2. Em A, digite `1`. Esperado: `Sessão encerrada.`, `Download de grande.bin interrompido: nada foi publicado. …` e o fim do processo.
3. O servidor mostra `Desconectado: … como Cliente 1 (QUIT)`.
4. `ls -la "$V/cliente-a/downloads"` não mostra `grande.bin` nem temporário.
5. B continua funcionando:
   - digite `3` e depois `ainda aqui`; o servidor mostra a mensagem;
   - digite `2` e depois `pequeno.txt`; esperado: `Download concluído`.

Se o download de A terminar antes do passo 2, aumente o arquivo e repita os itens 4 e 5.

## 6. Ctrl+C e EOF no servidor com downloads ativos

Faça duas rodadas: (a) Ctrl+C e (b) Ctrl+D (EOF), ambos no terminal do servidor.

Antes de cada rodada:
- rode `rm -f "$V/cliente-b/downloads/grande.bin"`;
- deixe o servidor, A e B rodando:
  - na rodada (a), reabra A, que recebe um novo identificador (Cliente 3);
  - na rodada (b), reabra os três.

Passos:
1. Em A e em B, digite `2` e depois `grande.bin`, e confira `Arquivo grande.bin solicitado.`.
2. Com os dois downloads ativos, pressione no servidor Ctrl+C (rodada a) ou Ctrl+D (rodada b).
3. Esperado no servidor, sem esperar os downloads:
   - `Ctrl+C recebido: encerrando o servidor` ou `EOF na entrada do operador: encerrando o servidor`;
   - `Desconectado: … (servidor encerrado)` para cada cliente;
   - `Servidor encerrado`.
4. Esperado em A e em B, sem pressionar Enter: `Download de grande.bin interrompido: …` e `Desconectado do servidor. Pressione Enter para sair.`. Nenhum `Download concluído` de `grande.bin`.
5. `ls -la "$V/cliente-a/downloads" "$V/cliente-b/downloads"` não mostra temporários. Na rodada (a), `pequeno.txt`, concluído por B no item 5, continua lá.
6. Pressione Enter em A e B para terminar os clientes.

## Limitações aceitas

- Mensagens recebidas podem se misturar visualmente ao prompt e ao texto parcialmente digitado; não há reconstrução do cursor.
- Depois da desconexão, pode ser preciso pressionar Enter para terminar o cliente.
- Não há latência máxima de chat nem confirmação de entrega coletiva.

## Limpeza

`rm -rf "$HOME/verificacao-t2"`

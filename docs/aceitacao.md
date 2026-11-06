# Registro de aceitação — Trabalho 2

Spec #8, ticket #16. Contratos: resolução da #7 e seção "Testing Decisions" da #8.

- **Código verificado:** `protocol.py`, `server.py` e `client.py` idênticos aos de `integracao/spec-8` @ c039104 (tickets #9 a #15). A #16 acrescentou só testes e documentos.
- **Ambiente:** Linux 7.0.0-42-generic, Python 3.14.6, só a biblioteca padrão.

## 1. Comandos e resultados

| Comando, na raiz | Resultado |
|---|---|
| `timeout 600 python3 -m unittest`, 3 execuções | `Ran 128 tests` em 28,8 s, 28,5 s e 28,9 s; `OK`, nenhum teste pulado |
| `python3 -m unittest -v tests.test_acceptance tests.test_memory` | 3 testes `OK` |
| `python3 -c "from tests.test_memory import peak_memory; [print(m, *peak_memory(m * 1024 * 1024)) for m in (0, 1, 12, 96, 384)]"`, 2 execuções | Tabela da seção 5 |

Os arquivos grandes (12 MiB, 96 MiB, 384 MiB) são gerados em diretórios temporários e removidos ao fim; nenhum entra no repositório.

## 2. Matriz da #7 e testes correspondentes

Os testes ficam em `tests/`. Novos nesta etapa: `test_acceptance` e `test_memory`.

| Linha da matriz | Testes |
|---|---|
| Framing | `test_protocol.FramingTest`: `test_header_and_content_split_in_pieces_are_rebuilt_once`, `test_frames_sent_together_are_separated_in_order`; `test_server.SessionTest.test_messages_from_many_threads_are_written_whole_and_in_order` |
| Formatos e limites | `test_protocol.FramingTest`: `test_file_data_carries_original_bytes_without_json_or_base64`, `test_control_is_utf8_json_and_length_counts_encoded_bytes`; `test_protocol.LimitsTest`; `test_download.ServerDownloadTest.test_file_is_sent_as_meta_8_kib_blocks_and_end_with_its_sha256`; arquivos de 12 MiB e 96 MiB, maiores que o frame (linhas "Download válido" e seção 5) |
| Frame inválido | `test_server.BrokenFrameTest`: `test_unknown_type_…`, `test_length_above_limit_…`, `test_invalid_json_ends_only_the_affected_session`; `test_protocol.InvalidFrameTest` |
| Stream interrompido | `test_server.BrokenFrameTest`: `test_end_inside_header_or_content_is_logged_as_incomplete_frame`, `test_end_between_frames_ends_the_session_without_incomplete_frame`; `test_protocol.StreamEndTest` |
| Campos e recusas normais | `test_server.ChatTest.test_valid_json_with_missing_or_invalid_fields_gets_invalid_request`; `test_download.ServerDownloadTest`: `test_valid_json_with_missing_or_invalid_fields_gets_invalid_request`, `test_missing_file_gets_not_found_…`, `test_forbidden_path_syntax_…`; `test_paths.SharedRootTest`: `test_folder_fifo_and_other_non_regular_objects_get_not_regular`, `test_missing_permission_gets_access_error_not_not_found` |
| Caminhos | `test_paths.SharedRootTest`: `test_examples_of_the_path_policy_are_accepted_or_forbidden_as_agreed`, `test_path_that_is_not_utf_8_text_…`; `test_paths.ContainmentTest`; `test_paths.DownloadsRootTest.test_examples_forbidden_by_the_path_policy_…`; `test_download.ClientDownloadTest.test_files_with_any_name_are_published_preserving_their_subfolders`; `test_download.WorkingDirectoryTest` |
| Links e destino | `test_paths.SharedRootTest`: `test_symbolic_link_as_the_file_…`, `test_symbolic_link_as_an_intermediate_folder_…`, `test_shared_root_that_is_a_symbolic_link_is_forbidden`, `test_shared_folder_swapped_with_a_link_…`; `test_paths.DownloadsRootTest`: `test_symbolic_link_as_a_download_folder_…`, `test_downloads_root_that_is_a_symbolic_link_…`, `test_symbolic_link_at_the_destination_…`, `test_object_in_the_place_of_a_download_folder_is_preserved`; `test_download.ClientDownloadTest.test_existing_object_at_the_destination_is_preserved_and_the_download_refused` |
| Identidade e chat | `test_server.ChatTest`: `test_client_chat_reaches_the_server_terminal_and_the_other_clients_without_echo`, `test_each_connection_gets_a_new_identifier_…`, `test_client_cannot_choose_the_displayed_origin`; `test_server.OperatorBroadcastTest.test_operator_message_reaches_every_connected_client_…`; `test_client.ClientSessionTest.test_chat_sends_one_message_confirms_it_as_voce_…` |
| Validação do chat | `test_server.ChatTest`: `test_text_from_1_to_1024_utf8_bytes_…`, `test_refused_text_gets_invalid_chat_…`, `test_text_that_cannot_be_encoded_as_utf8_…`; `test_client.ClientSessionTest.test_text_outside_the_rules_is_refused_locally_…`; `test_server.OperatorBroadcastTest.test_operator_text_outside_the_rules_…` |
| Download válido | `test_download.ServerDownloadTest.test_empty_file_is_sent_as_meta_with_size_zero_…`; `test_download.ClientDownloadTest`: `test_binary_file_of_12_mib_…`, `test_empty_file_is_published_empty`; `test_download.ScriptedServerDownloadTest.test_content_stays_in_a_temporary_file_until_the_final_check_…`; **novo** `test_acceptance.TerminalAcceptanceTest.test_two_clients_download_12_mib_at_the_same_time_…` |
| Integridade | `test_download.ScriptedServerDownloadTest.test_size_or_hash_mismatch_…`; `test_interruptions.ScriptedServerInterruptionTest` (fim entre frames, no meio do bloco, reset e prazo de recepção antes do FILE_END) |
| Publicação | `test_paths.DownloadsRootTest`: `test_object_created_at_the_destination_during_the_transfer_…`, `test_link_that_replaces_a_download_folder_…`, `test_temporary_replaced_by_a_link_…` |
| Exclusividade | `test_concurrency.ScriptedServerClientTest.test_second_request_is_blocked_locally_while_pending_transferring_or_in_the_final_check`; `test_concurrency.ServerConcurrencyTest.test_overlapping_requests_get_busy_…` |
| Concorrência | `test_concurrency.ServerConcurrencyTest`: `test_two_clients_download_at_the_same_time_…`, `test_chat_and_broadcast_sent_during_the_transfers_…`; `test_download.ServerDownloadTest.test_messages_queued_during_a_transfer_go_between_blocks_…`; `test_concurrency.ScriptedServerClientTest.test_chat_and_broadcast_received_mid_download_…`; **novo** `test_acceptance…test_two_clients_download_12_mib_…` |
| Sair | `test_interruptions.RelayedDownloadTest.test_chat_then_sair_during_a_slow_transfer_…` |
| Recursos | `test_limits.ClientLimitTest`; `test_limits.SlowRecipientTest.test_full_queue_…`; `test_limits.OutgoingQueueTest` |
| Prazos | `test_protocol.SendDeadlineTest`; `test_protocol.ReceiveDeadlineTest`; `test_server.DeadlineTest.test_frame_not_finished_within_deadline_…`; `test_server.SessionTest.test_send_deadline_ends_only_this_session`; `test_limits.SlowRecipientTest.test_blocked_send_expires_…`; `test_interruptions.RelayedDownloadTest.test_send_deadline_expiring_during_a_transfer_…` |
| Ociosidade e limpeza | `test_server.DeadlineTest.test_idle_session_without_frame_start_is_kept`; `test_protocol.ReceiveDeadlineTest.test_idle_wait_before_a_frame_starts_does_not_expire`; `test_server.SessionTest` (limpezas concorrentes, fila descartada, arquivo liberado); `test_interruptions.ServerInterruptionTest`: `test_quit_and_a_reset_during_a_download_…`, `test_shutdown_racing_with_quit_…` |
| Encerramento | `test_server.ShutdownTest`, `OperatorEofTest` e `CtrlCTest`; `test_interruptions.ServerInterruptionTest.test_shutdown_with_active_downloads_…`; `test_interruptions.ServerProcessInterruptionTest`; **novo** `test_acceptance…test_server_shutdown_during_two_downloads_…` |

**Lacunas fechadas nesta etapa:**
- **Sobreposição com o cliente real:** os downloads sobrepostos só eram verificados com participantes TCP crus. O teste novo usa dois `client.py` reais, cada um atrás de um repasse que segura o download no meio. Os dois downloads ficam ativos ao mesmo tempo, e os arquivos publicados conferem com a origem por tamanho e SHA-256. O arquivo vazio é baixado enquanto o outro download continua parado.
- **Encerramento com clientes reais:** o lado cliente do encerramento só era verificado com um servidor simulado. O teste novo encerra o servidor real durante dois downloads de clientes reais.
- **Memória:** não havia teste; ver a seção 5.

**Verificações de mutação:** os testes novos passaram de primeira. Cada mutação abaixo foi aplicada temporariamente, nunca commitada, e revertida com `git checkout`.

| Mutação | Resultado |
|---|---|
| Servidor serializa os downloads de sessões diferentes | `test_two_clients_download_12_mib_…` falhou: "nenhum temporário com 16384 bytes" |
| Servidor usa um SHA-256 compartilhado entre sessões, reiniciado a cada download | `test_two_clients_download_12_mib_…` falhou. Falharam também os dois testes de sobreposição de `test_concurrency`; os de download sequencial passaram |
| Encerramento do servidor não fecha as sessões com download ativo | `test_server_shutdown_…` falhou: "o encerramento esperou as transferências" |
| Cliente não descarta o download ativo quando a sessão termina | `test_server_shutdown_…` falhou |
| Servidor lê o arquivo inteiro antes de enviar | `test_memory` falhou: o pico do servidor cresceu 97.276 KiB, para um limite de 9.728 KiB |
| Cliente acumula os blocos até a verificação final | `test_memory` falhou: o pico do cliente cresceu 197.184 KiB |

## 3. Arquivo vazio e arquivo de 12 MiB

- **Arquivo vazio:**
  - o servidor envia FILE_META com tamanho 0 e FILE_END com o hash do vazio, sem FILE_DATA (`test_empty_file_is_sent_as_meta_with_size_zero_…`);
  - o cliente real publica um arquivo de 0 bytes cujo SHA-256 é `e3b0c442…b855` (`test_two_clients_download_12_mib_…`).
- **Arquivo de 12 MiB (12.582.912 bytes):**
  - o arquivo publicado pelo cliente real tem o tamanho e o SHA-256 da origem, sozinho (`test_binary_file_of_12_mib_…`) e em dois downloads sobrepostos (`test_two_clients_download_12_mib_…`);
  - pela conexão TCP, os bytes recebidos e o hash anunciado conferem nos downloads sobrepostos, inclusive com chat e broadcast durante as transferências (`test_concurrency.ServerConcurrencyTest`).

## 4. Revisão do processamento incremental

Concluída por leitura do código. Não foram encontrados defeitos.

- **Leitura e hash no servidor:** `FileTransfer._read` (`server.py:167`) é um gerador.
  - Cada `next_message` lê um bloco de 8 KiB, atualiza o SHA-256 e entrega só esse bloco.
  - Nada é lido antes; o FILE_META usa `fstat`.
  - Comprovado de fora por `test_each_block_is_read_and_hashed_when_sent_without_reading_the_file_beforehand`.
- **Envio no servidor:**
  - `send_file` (`server.py:220`) enfileira o objeto da transferência, não o conteúdo.
  - A escritora (`server.py:307`) pede uma mensagem por vez, alterna com a fila e envia cada frame inteiro antes do seguinte.
  - A fila guarda até 64 mensagens de controle, de até 64 KiB cada.
- **Protocolo:** `encode` (`protocol.py:79`) monta um único frame, de até 64 KiB mais 5 bytes de cabeçalho. `receive_message` (`protocol.py:176`) lê um frame por vez.
- **Gravação e hash no cliente:**
  - `Download.write` (`client.py:221`) grava cada bloco no temporário, faz flush, atualiza o SHA-256 e soma o tamanho, sem acumular nada.
  - `finish` (`client.py:229`) compara a contagem e o hash já calculados, sem reler o temporário. A publicação é um hard link, sem cópia.
  - O tamanho do FILE_META não reserva memória.
- **Memória retida por download:** um bloco e um frame de cada lado, os buffers fixos de E/S do Python e, no servidor, a fila limitada. Nada disso depende do tamanho do arquivo.

## 5. Memória

**Método:** `tests/test_memory.py`.
- **Roteiro de cada medição:** `server.py` e `client.py` rodam como no terminal, a partir de cópias, sempre com o mesmo roteiro: um servidor, um cliente, um download de arquivo esparso e a saída. Clientes e carga são os mesmos; só o tamanho do arquivo muda.
- **Medida:** depois do download, com os processos vivos, lê-se `VmHWM` em `/proc/<pid>/status`. É o pico de memória residente de cada programa desde o início, inclusive interpretador, buffers e blocos.
- **Critério:** de 1 MiB para 96 MiB, o pico de cada programa cresce menos de um décimo da diferença de tamanho, ou seja, menos de 9.728 KiB. É uma comparação entre tamanhos, sem teto absoluto para o processo. Carregar ou acumular o arquivo faria o pico crescer ao menos a diferença inteira, como mostram as mutações da seção 2.

**Por que este método:**
- **`ru_maxrss`, de `os.wait4` ou `resource`:** foi descartado nesta etapa. No Linux, o processo criado herda o pico do processo que o criou. Uma medição com o processo pai segurando 200 MiB deu `ru_maxrss` de 218.256 KiB para um filho cujo `VmHWM` era 11.348 KiB. Assim, o pico da suíte escondia o dos programas: com a mutação "servidor lê o arquivo inteiro" e o processo pai com 300 MiB, o teste baseado em `ru_maxrss` passou.
- **`tracemalloc`:** veria só alocações do Python e exigiria rodar os programas dentro do processo de teste, acoplando o teste ao código interno.

**Resultados**, em KiB, de duas execuções do comando da seção 1:

| Arquivo | Servidor | Cliente |
|---|---|---|
| 0 (vazio) | 20.700 / 20.836 | 21.060 / 20.992 |
| 1 MiB | 20.900 / 20.896 | 21.108 / 21.104 |
| 12 MiB | 20.960 / 20.968 | 21.212 / 21.120 |
| 96 MiB | 20.904 / 20.968 | 21.180 / 21.192 |
| 384 MiB | 20.964 / 20.928 | 21.392 / 21.156 |

De 0 a 384 MiB, o pico variou no máximo 268 KiB no servidor e 400 KiB no cliente. A memória destinada ao conteúdo não cresce com o tamanho do arquivo.

## 6. Limitações aceitas

Registradas nos contratos e nos comentários das issues; não são defeitos.

- **Contratos (#6, #7, #8):**
  - não há heartbeat, reconexão automática, retomada nem prazo total de download;
  - uma perda silenciosa da conexão pode ficar sem detecção enquanto não houver tráfego;
  - depois da desconexão, o cliente pode precisar de Enter para terminar;
  - mensagens podem interferir visualmente no prompt, e não há reconstrução do cursor;
  - não há latência máxima de chat nem confirmação de entrega coletiva;
  - Ctrl+C e EOF não garantem a entrega das filas.
- **Q7 da #5:** falhas de leitura no servidor e de gravação no cliente durante a transferência não têm política própria. Hoje, uma falha de gravação no cliente, como ENOSPC, derruba a receptora (revisão refeita da #13).
- **#9, #11 e esta etapa:**
  - os testes de envio com avanço parcial e de destinatário lento supõem buffers TCP pequenos do Linux; `SlowRecipientTest` é pulado sem `TCP_MAXSEG`;
  - Ctrl+C é testado só em POSIX;
  - os testes de memória e de arquivos abertos usam `/proc`.
- **#14:** a janela entre o FILE_END e o anúncio do resultado não é segurada por teste; ela é garantida pela estrutura do código. O bloqueio local é testado com o pedido pendente, em transferência e com todos os bytes recebidos antes do FILE_END.
- **#13, revisão refeita, seção 3:**
  - um processo local ainda pode trocar o arquivo depois da conferência final;
  - uma pasta movida para fora da raiz, e não trocada por link, depois de aberta pode receber uma pasta vazia ou um temporário passageiro;
  - sob troca concorrente, a recusa pode sair como NOT_FOUND;
  - em sistemas de arquivos sem hard links, a publicação falha;
  - um hard link dentro de `files/` é servido;
  - os ancestrais das raízes não são examinados;
  - as subpastas de um download que falhou permanecem;
  - caracteres Unicode da categoria Cf são aceitos em nomes;
  - ENAMETOOLONG vira ACCESS_ERROR;
  - Windows não é suportado;
  - uma raiz compartilhada ausente responde NOT_FOUND;
  - dispositivos não são testados, porque `mknod` exige root.

## 7. Achados em aberto

Registrados em revisões anteriores. Não foram aceitos nem corrigidos e não impedem os critérios da #16; ficam para o code-review final.

- **#13 A (baixa):** a limpeza depois de uma conferência pós-publicação que falha pode apagar um objeto alheio salvo no destino no mesmo instante. Se o desfazimento falhar, a mensagem diz "Nada foi publicado".
- **#13 B (baixa):** a recusa de um caminho permitido perto de 64 KiB encerra a sessão. Reconfirmado em c039104: com um caminho de 65.460 bytes, a escritora levanta `ValueError` e o servidor registra "Desconectado: … (falha inesperada no envio)".
- **#13 C e #10 (baixa):** no terminal padrão, uma entrada que não é UTF-8 encerra o cliente com traceback, no menu, no chat ou no caminho.
- **#13, fora do escopo daquele ticket:** um FILE_DATA antes do FILE_META derruba a receptora, e um FILE_META duplicado deixa um temporário órfão. Um servidor conforme nunca envia nenhum dos dois.

## 8. Verificação manual (#17)

Pendente. O roteiro está em `docs/roteiro-verificacao-manual.md`.

| Verificação | Resultado | Observações |
|---|---|---|
| 1. Recebimento antes de Enter, inclusive com texto parcial | pendente | |
| 2. Origens, `[Servidor]` e `[Você]` | pendente | |
| 3. Chat volta ao menu, também durante download | pendente | |
| 4. Menu, segundo download recusado, Chat e Sair com download ativo | pendente | |
| 5. Sair durante a transferência; o outro cliente continua | pendente | |
| 6. Ctrl+C e EOF com downloads ativos | pendente | |

Data, ambiente e limitações visuais observadas: pendente.

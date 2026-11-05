# Trabalho 2

Vocabulário da aplicação de transferência de arquivos e chat.

## Language

**Chat coletivo**:
Conversa em que uma mensagem de um cliente é exibida no servidor e encaminhada aos demais clientes, com identificação da origem.
_Avoid_: Chat privado, conversa individual

**Identificador do cliente**:
Identificação automática atribuída pelo servidor a um participante do chat coletivo. Uma reconexão recebe uma nova identificação.
_Avoid_: Apelido, nome de usuário

**Operador do servidor**:
Pessoa que usa o terminal do servidor e pode enviar mensagens a todos os clientes conectados.
_Avoid_: Administrador, cliente do servidor

**Broadcast do servidor**:
Mensagem iniciada pelo operador do servidor e destinada a todos os clientes conectados.
_Avoid_: Mensagem privada

**Download ativo**:
Solicitação de arquivo pendente ou transferência em andamento, incluindo a verificação final, que ainda não foi concluída nem encerrada por falha ou saída.
_Avoid_: Arquivo concluído

**Download concluído**:
Transferência cujo conteúdo teve tamanho e integridade verificados e cujo arquivo está disponível no destino final do cliente.
_Avoid_: Envio terminado

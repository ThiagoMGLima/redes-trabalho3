# Socket e tabela de tipos escritos sem os atalhos da biblioteca padrão

O socket de escuta é criado com `socket.socket(AF_INET, SOCK_STREAM)`, `bind` e `listen` explícitos, no lugar do `socket.create_server` herdado do Trabalho 2. A tabela de extensão para `Content-Type` é escrita no código, sem o módulo `mimetypes`. O enunciado proíbe abstrações sobre sockets ou HTTP e pede para mostrar no código o identificador que garante TCP. Os atalhos esconderiam justamente o que deve ser mostrado, e `mimetypes` ainda depende das tabelas do sistema, o que poderia mudar o `Content-Type` entre máquinas.

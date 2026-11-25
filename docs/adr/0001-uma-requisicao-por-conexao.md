# Uma requisição por conexão, atendida por uma única thread

Cada conexão aceita recebe exatamente uma requisição e uma resposta com `Connection: close`, e é atendida do início ao fim por uma única thread dedicada. Isso torna a correspondência "uma conexão = uma thread = um recurso" visível no código e no vídeo, como no exemplo de resposta do enunciado. Keep-alive exigiria um laço de requisições, prazo de ociosidade e a explicação de mais estados, sem valer ponto na avaliação.

## Consequences

A dupla de threads (receptora e escritora) e a fila de saída do Trabalho 2 existiam para intercalar chat e transferências na mesma conexão. Com uma requisição por conexão, não há o que intercalar, e elas deixam de existir.

# Trabalho 3

Estas instruções se aplicam ao Trabalho 3: servidor HTTP simplificado sobre TCP, construído a partir do código final do Trabalho 2 (servidor TCP multithread), cujo histórico este repositório preserva. Os Trabalhos 1 e 2 estão concluídos e fora do escopo.

O enunciado está em `Enunciado/`. A lista completa do que o professor exige, com o estado de cada item, está em `docs/requisitos.md`; nenhum item pode ficar de fora da entrega.

## Agent skills

### Issue tracker

Tarefas e especificações são registradas no GitHub Issues. Ao criar, consultar ou atualizar uma issue, leia `docs/agents/issue-tracker.md`.

### Triage labels

A triagem usa os cinco rótulos padrão. Ao classificar uma issue, leia `docs/agents/triage-labels.md`.

### Domain docs

Layout single-context: `GLOSSARY.md` e `docs/adr/` na raiz do Trabalho 3. Antes de explorar o projeto, leia `docs/agents/domain.md`.

## Fluxo de trabalho com skills

Antes de agir em uma etapa, leia o `SKILL.md` correspondente e identifique suas ações, critérios de conclusão e pontos de participação do usuário. Execute a etapa atual e aguarde as respostas exigidas antes de avançar.

Após o planejamento, siga `to-spec` → `to-tickets` → `implement-spec`. Na etapa `to-tickets`, apresente a divisão e as dependências ao usuário e publique somente a versão aprovada. Inicie a execução quando o usuário solicitar a implementação; confirmar interfaces de teste ou concluir uma especificação não substitui essa solicitação.

Após cada merge, faça push da branch de integração e da branch do ticket; nunca faça push direto na `main`.

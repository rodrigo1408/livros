# Governança do repositório `livros`

## Proteções obrigatórias da branch `main` (ruleset)
- Alterações somente via Pull Request; push direto, force-push e exclusão bloqueados; sem bypass.
- Status obrigatório: `book-factory/approval-gate` (origem: GitHub Actions).
- Conversas resolvidas antes do merge; histórico linear opcional.
- `CODEOWNERS` exige o proprietário em `.github/` e recomenda-o em todo o repositório.

## O que o portão verifica (`.github/scripts/book_gate.py`)
1. O PR altera apenas um diretório de livro e nada em `.github/` ou na raiz.
2. Estrutura completa (manuscrito, capítulos, relatórios, EPUB, capa, metadados, pacote de revisão).
3. Nenhum segredo/credencial nos arquivos de texto.
4. Validação técnica dos arquivos editoriais aprovada e `auto_publish=false`.
5. Estado do livro em `AWAITING_HUMAN_REVIEW`.
6. Registro `aprovacao/aprovacao.json` presente e **revalidado na API do GitHub**: o comentário
   `/aprovar` existe neste PR, foi feito por login autorizado, humano, com associação confiável, que não é
   o autor do PR, e cita exatamente o livro e o hash aprovados.
7. O hash atual do conteúdo é idêntico ao hash aprovado.

## Trilha de auditoria
- `<book_id>/.factory/audit.jsonl`: todas as transições de estado (quem, quando, execução, motivo).
- `<book_id>/aprovacao/historico.jsonl`: todas as aprovações registradas.
- Histórico do Git e comentários do PR.

## O que nunca acontece automaticamente
- Merge na `main`.
- Conversão de silêncio/timeout em aprovação.
- Publicação ou alteração de qualquer título no Amazon KDP.

# livros

Repositório oficial dos livros produzidos pela **Fábrica de Livros** (`book-factory-ai`).

> **Regra inegociável:** nenhum livro entra na branch oficial (`main`) sem revisão e aprovação
> explícitas do proprietário. A fábrica trabalha apenas em branches `producao/<book_id>` e abre
> Pull Requests em modo **Draft**.

## Estrutura

```
<book_id>/                      # ex.: agentes-autonomos-de-ia-i12  (slug + número da Issue)
├── ideia.md                    # ideia original e parâmetros
├── planejamento/               # interpretação, briefing, guia de estilo, plano (fonte única de verdade)
├── pesquisas/                  # dossiê (fontes verificadas, afirmações rastreáveis) e referências
├── capitulos/                  # cap-01.md, cap-02.md, ...
├── revisoes/                   # revisões por capítulo, correções, feedback humano, histórico
├── relatorios/                 # qualidade, auditoria, consumo, bloqueio (se houver)
├── publicacao/                 # manuscrito.md, livro.epub, capa.jpg, metadados-kdp.json, validação, instruções KDP
├── revisao/                    # resumo executivo, como revisar, pendências, manifest.json (hashes)
├── aprovacao/                  # aprovacao.json + historico.jsonl (gravados SOMENTE pelo workflow de aprovação)
└── .factory/                   # estado da máquina de estados, trilha de auditoria e logs (sem segredos)
```

## Fluxo de revisão e aprovação

1. A fábrica abre um PR Draft `producao/<book_id>` → `main` com o pacote de revisão.
2. Para pedir mudanças, comente no PR: `/revisar` + instruções. Uma nova versão é publicada no mesmo PR.
3. Para aprovar a versão revisada, comente o comando exibido na descrição do PR:
   `/aprovar <book_id> sha256:<hash>`.
4. O workflow `book-review-commands` valida identidade (proprietário autorizado, humano, não autor do PR)
   e o hash do conteúdo, grava `aprovacao/aprovacao.json` e publica o status `book-factory/approval-gate`.
5. O merge (manual) só é possível com esse status verde. Qualquer alteração de conteúdo após a aprovação
   invalida o status (o hash muda) e exige nova aprovação.

Veja [GOVERNANCA.md](GOVERNANCA.md) para as regras de proteção e auditoria.

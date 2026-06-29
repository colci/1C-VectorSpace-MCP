# Search Backlog

## Goal

Convert the latest live MCP regression run into an actionable search-improvement backlog.
This backlog focuses on universal retrieval behavior for arbitrary 1C configurations, not on one specific export.

## Current Signal

What already works:

- metadata lookup for business documents like `поступление товаров`
- metadata lookup for concrete master data like `договор контрагента`
- some code queries around price selection and contract defaults

What still fails or drifts:

- code search for validation/business-rule queries
- metadata search for abstract financial/register concepts
- noisy conversational queries
- convergence between metadata and code for one business topic

## Priority P0

### P0.1 Stabilize Metadata Search For Abstract Business Domains

Problem:

- queries like `расчеты с покупателями` and `взаиморасчеты с контрагентом` drift to `Subsystem` or `Catalog`, while the useful result is often a register

Planned changes:

- demote more low-signal metadata types during navigation search
- add register-intent detection for terms like `расчеты`, `взаиморасчеты`, `остатки`, `обороты`, `движения`
- add universal semantic expansion for register-oriented business phrasing

Done when:

- `search_metadata("расчеты с покупателями")` returns a register or another directly useful business object in top-3
- `search_metadata("структура взаиморасчетов с контрагентом")` no longer collapses to a generic counterparty catalog as top-1

### P0.2 Improve Code Search For Validation And Business Rules

Problem:

- queries like `где проверяется возможность проведения документа при отрицательных остатках` return irrelevant technical methods

Planned changes:

- detect validation intent: `проверка`, `нельзя`, `ошибка`, `проведение`, `остатки`, `контроль`
- boost methods with domain-condition signals in method name, module path, and code text
- demote UI-only handlers and weak lexical matches more aggressively

Done when:

- validation-style queries return business-rule code in top-5
- report modules and unrelated helper modules no longer dominate those queries

## Priority P1

### P1.1 Improve Noisy Natural-Language Robustness

Problem:

- long human phrasing degrades more than short normalized phrasing

Planned changes:

- better filler-word stripping
- intent extraction before retrieval
- optional internal query rewrite for retrieval only

Done when:

- long and short versions of the same user intent land in the same functional area

### P1.2 Improve Metadata-Code Convergence

Problem:

- metadata and code searches sometimes answer adjacent but different objects for the same business question

Planned changes:

- add domain anchors shared by metadata and code ranking
- reuse semantic expansion terms across both tools

Done when:

- queries like `как работает заказ клиента` and `как ведутся расчеты с контрагентом` converge on one business area

## Priority P2

### P2.1 Add Search Diagnostics

Planned changes:

- add `explain_search_result`
- show which signals influenced ranking
- expose vector score vs lexical score vs semantic-expansion score

Why:

- makes future tuning much faster and safer

### P2.2 Expand Regression Automation

Planned changes:

- keep a small live regression pack
- store expected top-1/top-3 classes for core queries

Why:

- prevents silent regressions after ranking changes

## First Implementation Slice

Start with `P0.1`:

1. Expand low-signal metadata types.
2. Add register-intent detection.
3. Add register-oriented semantic lookup phrases.
4. Re-run these live checks:
   - `расчеты с покупателями`
   - `структура взаиморасчетов с контрагентом`
   - `реквизиты договора контрагента`
   - `структура документа поступления товаров`

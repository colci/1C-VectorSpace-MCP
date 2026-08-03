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

Status: implemented in `mcp_server.py` on 2026-07-05.

Problem:

- queries like `расчеты с покупателями` and `взаиморасчеты с контрагентом` drift to `Subsystem` or `Catalog`, while the useful result is often a register

Planned changes:

- demote more low-signal metadata types during navigation search
- add register-intent detection for terms like `расчеты`, `взаиморасчеты`, `остатки`, `обороты`, `движения`
- add universal semantic expansion for register-oriented business phrasing

Done when:

- `search_metadata("расчеты с покупателями")` returns a register or another directly useful business object in top-3
- `search_metadata("структура взаиморасчетов с контрагентом")` no longer collapses to a generic counterparty catalog as top-1

Implementation notes:

- added a shared register metadata type set for `AccumulationRegister`, `InformationRegister`, `AccountingRegister`, and `CalculationRegister`
- boosted register-like results for register intent and demoted low-signal/navigation objects such as `Subsystem`, `Role`, and generic `Catalog` matches
- added generic semantic expansions for financial/register phrases and common business documents, including поступление/реализация товаров and заказ клиента/поставщику

Live check on current `default` graph/Qdrant index:

- `расчеты с покупателями` -> top-1 `AccumulationRegister.РасчетыСПокупателями`
- `структура взаиморасчетов с контрагентом` -> top-1 `AccumulationRegister.ВзаиморасчетыСКонтрагентамиМП`
- `реквизиты договора контрагента` -> top-1 `Catalog.ДоговорыКонтрагентов`
- `структура документа поступления товаров` -> top-1 `Document.ПриходнаяНакладная`, top-2 `Document.ПриходТовараМП`

### P0.2 Improve Code Search For Validation And Business Rules

Status: first implementation pass completed in `mcp_server.py` on 2026-07-05.

Problem:

- queries like `где проверяется возможность проведения документа при отрицательных остатках` return irrelevant technical methods

Planned changes:

- detect validation intent: `проверка`, `нельзя`, `ошибка`, `проведение`, `остатки`, `контроль`
- boost methods with domain-condition signals in method name, module path, and code text
- demote UI-only handlers and weak lexical matches more aggressively

Done when:

- validation-style queries return business-rule code in top-5
- report modules and unrelated helper modules no longer dominate those queries

Implementation notes:

- added validation/business-rule intent detection for `проверка`, `контроль`, `ошибка`, `нельзя`, `отказ`, `проведение`, `остатки`, `отрицательные`, and `недостаточно`
- added validation-specific vector query expansion for searches around document posting and stock control
- boosted business-rule method shapes such as `ОбработкаПроведения`, `ПередПроведением`, `ВыполнитьКонтроль`, `Проверить*`, and `Отказ`
- demoted low-value UI/form handlers such as `ПриИзменении`, `ПриАктивизацииСтроки`, `ПриНачалеРедактирования`, and `Очистить*`

Live check on current `default` graph/Qdrant index:

- `где проверяется возможность проведения документа при отрицательных остатках` now returns business-rule candidates in top-5:
  `Documents.ПроизводствоМП.МодульОбъекта -> ОбработкаПроведения`,
  then several `Documents.*.МодульМенеджера -> ВыполнитьКонтроль`

## Priority P1

### P1.1 Improve Noisy Natural-Language Robustness

Status: first implementation pass completed in `mcp_server.py` on 2026-07-05.

Problem:

- long human phrasing degrades more than short normalized phrasing

Planned changes:

- better filler-word stripping
- intent extraction before retrieval
- optional internal query rewrite for retrieval only

Done when:

- long and short versions of the same user intent land in the same functional area

Implementation notes:

- expanded conversational/filler stopwords for natural Russian user phrasing
- added retrieval query normalization so vector search uses both the original query and a compact business core
- expanded validation-code query rewrites for `нельзя провести`, `не хватает товара`, and stock-control phrasing
- added metadata business-anchor scoring so explicit terms like `договор`, `поступление`, `товар`, `расчеты`, `цены`, and `заказ` keep the ranking near the intended object

Live check on current `default` graph/Qdrant index:

- `подскажи в каком объекте лежат реквизиты договора для работы с контрагентом` -> top-1 `Catalog.ДоговорыКонтрагентов`
- `мне нужно понять где в конфигурации описана структура документа поступления товаров` -> top-1 `Document.ПриходнаяНакладная`
- `мне нужно понять где в конфигурации проверяют что документ нельзя провести если не хватает товара` -> top-5 includes multiple `ОбработкаПроведения`

### P1.2 Improve Metadata-Code Convergence

Status: first implementation pass completed and covered by live regression cases.

Problem:

- metadata and code searches sometimes answer adjacent but different objects for the same business question

Planned changes:

- add domain anchors shared by metadata and code ranking
- reuse semantic expansion terms across both tools

Done when:

- queries like `как работает заказ клиента` and `как ведутся расчеты с контрагентом` converge on one business area

Implementation notes:

- shared domain anchors are used by metadata and code ranking paths for common 1C business areas
- convergence checks are automated in `run_mcp_regression.py`
- current covered examples:
  - `convergence_customer_order`
  - `convergence_counterparty_settlements`

## Priority P2

### P2.1 Add Search Diagnostics

Status: implemented as `explain_search_result` and covered by a live diagnostics smoke case.

Planned changes:

- add `explain_search_result`
- show which signals influenced ranking
- expose vector score vs lexical score vs semantic-expansion score

Why:

- makes future tuning much faster and safer

Implementation notes:

- `explain_search_result(query, target="auto", limit=...)` reports metadata/code diagnostics
- regression marker coverage is in `diagnostics_customer_order`

### P2.2 Expand Regression Automation

Status: expanded live regression pack exists in `run_mcp_regression.py`; case listing, tool-category filtering, JSON inventory and fail-fast runs are supported.

Planned changes:

- keep a small live regression pack
- store expected top-1/top-3 classes for core queries

Why:

- prevents silent regressions after ranking changes

Current coverage:

- metadata register/business-domain queries from `P0.1`
- metadata operational document query for `поступление товаров`
- metadata concrete catalog query for `договор контрагента`
- code validation/business-rule query from `P0.2`
- noisy long-form metadata/code queries from first `P1.1` pass
- metadata/code convergence checks from `P1.2`
- diagnostics checks for `explain_search_result`
- module navigation, module search, command search and event-subscription search smokes
- workflow/report smokes for `build_implementation_context`, `analyze_task`, `extension_overlay_report`, `form_structure_report`
- validation/report smokes for `validate_bsl`, `validate_changed_files`, `post_change_report`

Operational helpers:

- `run_mcp_regression.py --list`
- `run_mcp_regression.py --summary`
- `run_mcp_regression.py --list --tool diagnostics`
- `run_mcp_regression.py --list --list-format json`
- `run_mcp_regression.py --tool diagnostics --fail-fast`

Next expansion:

- keep adding regression cases when a new MCP workflow graduates from manual validation
- add focused failure-regression cases for any future ranking bug before tuning the scorer

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

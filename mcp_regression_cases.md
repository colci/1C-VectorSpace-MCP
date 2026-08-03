# MCP Regression Cases

## Goal

This set is meant to stress the current MCP server in ways that simple exact-match checks do not.
The cases are intentionally universal: they describe patterns that should exist in many 1C configurations,
without hard-coding one specific metadata object from one database.

## How To Use

Automated subset:

```powershell
.\.venv\Scripts\python.exe run_mcp_regression.py
```

List available automated cases without importing MCP runtime:

```powershell
.\.venv\Scripts\python.exe run_mcp_regression.py --list
```

Summarize selected cases by tool category:

```powershell
.\.venv\Scripts\python.exe run_mcp_regression.py --summary
```

Filter the list or live run by tool category:

```powershell
.\.venv\Scripts\python.exe run_mcp_regression.py --list --tool diagnostics
.\.venv\Scripts\python.exe run_mcp_regression.py --tool diagnostics --fail-fast
```

Machine-readable case inventory:

```powershell
.\.venv\Scripts\python.exe run_mcp_regression.py --list --list-format json
```

The script currently covers the stabilized P0 metadata/validation-code checks, P1.1 noisy-query checks, P1.2 metadata/code convergence, diagnostics, module navigation/search, managed-form command search, event-subscription search, workflow reports, extension overlay, BSL validation, changed-file validation, post-change report, and form-structure smoke checks. The broader cases below remain a guide for manual review and future automation.

For each case:

1. Start with `search_metadata` or `search_code` using the natural-language query.
2. Narrow down the result with `find_metadata_object` or `find_method` when a likely target appears.
3. Verify structure or code with `get_file_snippet`.
4. If the case is about references between objects, finish with `get_dependencies`.

Pass criteria should be evaluated at two levels:

- Retrieval quality: the right class of object appears in top-3.
- Navigation quality: the next deterministic step (`find_*`, snippet, dependencies) is usable without guesswork.

## Case 1. Business Term vs Real Object Name

Purpose: check whether semantic expansion helps when the user asks in business language but the configuration uses an internal object name.

Natural query examples:

- `структура документа поступления товаров`
- `структура документа реализации товаров`
- `структура заказа клиента`

What should happen:

- `search_metadata` returns a `Document` in top-3, not constants, roles, styles, or random reports.
- The returned object should look like an operational business document, not a service or auxiliary object.
- `find_metadata_object` should then resolve the chosen document exactly.

Failure signals:

- top results are `Constant`, `Role`, `StyleItem`, `CommonPicture`, or `Report`
- the result is semantically close only by one token like `поступление`, but belongs to cash flow or service flow instead of goods flow

## Case 2. Structural Query With Competing Objects

Purpose: test ambiguity where many objects share the same business word but differ by type or lifecycle stage.

Natural query examples:

- `реквизиты договора контрагента`
- `структура цен номенклатуры`
- `реквизиты характеристики номенклатуры`

What should happen:

- `search_metadata` should prefer the master object itself over related registers, attached files, forms, or rights.
- Top-1 or top-3 should contain the main catalog or document, not derivative objects with similar names.
- `get_dependencies` should reveal downstream references from the chosen object.

Failure signals:

- attached-file catalogs outrank the real business object
- information registers outrank the source catalog when the user explicitly asks for `реквизиты`

## Case 3. Code Search For Hidden Business Logic

Purpose: test whether code retrieval finds actual implementation points, not generic helper methods.

Natural query examples:

- `где проверяется возможность проведения документа при отрицательных остатках`
- `как рассчитывается скидка клиента`
- `где определяется склад по умолчанию`

What should happen:

- `search_code` returns a concrete procedure or function in top-5.
- The result should contain business conditions, validation logic, or calculation logic, not only wrappers or UI handlers.
- `find_method` should help lock onto the exact method name after the first hit.

Failure signals:

- top hits are report modules or unrelated common modules
- top hits contain only form-opening logic or UI event glue

## Case 4. Cross-Object Dependency Chain

Purpose: test whether metadata lookup plus dependency extraction can support impact analysis.

Scenario:

- Ask for a business object with multiple references, for example a document with counterparty, contract, warehouse, currency, or project fields.
- Then run `get_dependencies` on the chosen object.

Suggested query forms:

- `структура документа закупки`
- `структура документа продажи`
- `структура взаиморасчетов с контрагентом`

What should happen:

- `search_metadata` finds the main object.
- `get_dependencies` returns several referenced object types, ideally a mix of catalogs, enums, and documents.
- The dependency list should be interpretable enough to support a later `where_used` workflow.

Failure signals:

- dependencies are empty for a clearly non-trivial object
- only raw text is returned without enough structure to understand source -> target links

## Case 5. Similar Names Across Metadata Types

Purpose: test disambiguation when the same concept exists in multiple forms: document, register, report, role, subsystem, or enum.

Natural query examples:

- `расчеты с покупателями`
- `заказы поставщикам`
- `движение денежных средств`

What should happen:

- If the query asks for `структура` or `реквизиты`, metadata search should prefer navigable source objects.
- If the query sounds analytical, the result may include registers, but still should not collapse into random roles or reports.
- The ranking should be stable across repeated runs.

Failure signals:

- a role or subsystem outranks the actual business object
- the result type changes unpredictably between runs for the same query

## Case 6. Method Name Unknown, Intent Known

Purpose: test retrieval when the user knows what the code does, but not how the procedure is named.

Natural query examples:

- `как заполняется табличная часть товарами`
- `где выполняется автоподстановка договора`
- `как подбирается цена по виду цены`

What should happen:

- `search_code` finds methods with meaningful bodies, not only event handlers with one-line delegation.
- At least one result should include domain terms from the query in code, comments, parameter names, or nearby method names.
- After reading the hit, `find_method` should be able to locate the exact method directly.

Failure signals:

- only generic methods like `ПриОткрытии`, `Команда1`, `ОбновитьФорму`, `ЗаполнитьДанные` appear without domain context

## Case 7. Metadata and Code Must Converge

Purpose: test whether a metadata object and its handling code can both be found from one business question.

Scenario:

1. Search metadata with a business query.
2. Take the top object and inspect its structure.
3. Search code with the same business query.
4. Check whether returned code is plausibly related to the chosen object.

Natural query examples:

- `как оформляется приемка товаров`
- `как работает заказ клиента`
- `как ведутся расчеты с контрагентом`

What should happen:

- `search_metadata` and `search_code` should converge on the same functional area.
- The code result should reference the same document, catalog, register, or neighboring objects.

Failure signals:

- metadata points to one domain area, code points to a completely different one
- code results are technically valid but belong to reporting or formatting instead of business processing

## Case 8. Noisy Query With Extra Human Wording

Purpose: test resilience to realistic user phrasing instead of short keyword input.

Natural query examples:

- `мне нужно понять где в конфигурации проверяют что документ нельзя провести если не хватает товара`
- `подскажи в каком объекте лежат реквизиты договора для работы с контрагентом`
- `ищу место где при заполнении документа подставляется цена и пересчитывается сумма`

What should happen:

- The server should ignore filler words and retain the business core.
- Top results should remain relevant even when the query is long and conversational.

Failure signals:

- ranking degrades sharply compared to the short normalized form of the same query
- filler words dominate retrieval

## Case 9. False Friend Query

Purpose: test suppression of superficially similar but semantically wrong matches.

Natural query examples:

- `поступление товаров`
- `реализация товаров`
- `счет клиента`

Why this is hard:

- many configurations contain many objects with one shared token: `поступление`, `товары`, `счет`, `заказ`, `расчеты`
- vector search alone often drifts into adjacent domains

What should happen:

- the top results should share the full intent, not just one token
- service, reporting, and notification objects should be demoted when the query clearly asks for an operational document or business logic

Failure signals:

- `уведомление`, `отчет`, `роль`, `картинка`, `константа` outrank operational objects

## Case 10. Diagnostic Health Check

Purpose: test whether the server is safe to trust before semantic checks even begin.

Run:

- `index_status()`

What should happen:

- collection exists and returns counts
- metadata chunk count is non-zero
- code chunk count is non-zero
- cache state and changed-file state are understandable

Failure signals:

- code count or metadata count is zero
- cache says everything is fresh, but search is obviously empty or irrelevant

## Minimal Regression Set

If you want a short but strong smoke-regression pack after each search change, use only these five:

1. `структура документа поступления товаров`
2. `реквизиты договора контрагента`
3. `где проверяется возможность проведения документа при отрицательных остатках`
4. `как подбирается цена по виду цены`
5. `как работает заказ клиента`

## Evaluation Template

Use this compact template when logging results:

```text
Case:
Query:
Tool:
Top-3 result types:
Top-1 object/method:
Was top-1 acceptable: yes/no
Could I continue with find_* or dependencies: yes/no
Main failure mode:
```

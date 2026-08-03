# Roadmap

## Goal

Move the project from "vector search over 1C export" to a practical MCP toolkit for day-to-day 1C development.

## Strategic Plan

- Outcome-oriented development plan: [effective_1c_development_plan.md](/D:/work/1C-VectorSpace-MCP/effective_1c_development_plan.md:1)
- Universalization plan: [universal_mcp_plan.md](/D:/work/1C-VectorSpace-MCP/universal_mcp_plan.md:1)
- Memgraph requirements: [memgraph_requirements.md](/D:/work/1C-VectorSpace-MCP/memgraph_requirements.md:1)
- Search tuning backlog: [search_backlog.md](/D:/work/1C-VectorSpace-MCP/search_backlog.md:1)
- Session handoff with the latest implementation history: [session_handoff_2026-07-03_memgraph_failure.md](/D:/work/1C-VectorSpace-MCP/session_handoff_2026-07-03_memgraph_failure.md:1)

## Current Snapshot

What is already working in the repository:

- Universal config model is in place:
  - `CONFIG_NAME`, `CONFIG_ID`, `CONFIG_PROFILE`, `platform_version`
  - generic runtime is separated from profile-specific heuristics
- Graph runtime is already migrated behind `GraphRepository`:
  - JSON fallback exists
  - Memgraph is the primary runtime backend
- Multi-config registry baseline is now in place:
  - `config_registry.json`
  - runtime switching via `list_configurations` / `switch_configuration`
  - separate RAG and graph storages for base config and extension
- Structural MCP tools are already implemented:
  - `get_dependencies`
  - `list_object_forms`
  - `find_usages`
  - `get_callers`
  - `get_callees`
- Deterministic lookup tools already exist:
  - `find_metadata_object`
  - `find_form`
  - `find_module`
  - `find_common_module`
  - `find_method`
  - `list_module_methods`
- Search quality improvements already shipped:
  - register/business-domain metadata tuning
  - validation/business-rule code tuning
  - noisy-query handling
  - metadata/code convergence
- Diagnostics and repair tools already exist:
  - `index_status`
  - `explain_search_result`
  - `reindex_file`
  - `reindex_path`
- Live regression coverage exists in [run_mcp_regression.py](/D:/work/1C-VectorSpace-MCP/run_mcp_regression.py:1) and is currently green.

## Remaining Gaps

The main gaps are no longer in the basic MCP skeleton. They are now in depth, precision, and developer workflow coverage:

- graph coverage is still below the target:
  - managed forms have `form -> command -> handler -> method` and `form -> element -> handler -> method`
  - non-form command sources and event subscriptions are not first-class graph nodes yet
- indexing coverage is still incomplete:
  - module-summary, managed-form command, and event-subscription chunks are implemented
  - no dedicated chunks for templates, roles, and several other non-code artifacts
- BSL structural analysis still depends on regex/heuristics instead of AST-first parsing
- cross-config navigation between base config and extension is not implemented yet
- offline/full indexing path still needs hardening when embedding model download is blocked
- docs and backlog files need periodic reconciliation with actual code state

## Next Work Queue

These are the most useful next steps in execution order.

1. Synchronize planning docs with reality.
   - refresh `search_backlog.md` statuses after `P1.2`, `P2.1`, and targeted reindex tools
   - keep the handoff file as the factual implementation log

2. Add the highest-value workflow tools for MCP-assisted programming. Done for the first pass.
   - `change_impact_report`
   - `build_implementation_context`
   - `trace_business_flow`
   - target outcome: MCP becomes not only a search toolbox, but a real "prepare and guide the coding task" assistant

3. Expand index coverage beyond current metadata/form/method slices.
   - module-summary chunks are implemented
   - managed-form command chunks are implemented
   - event-subscription chunks are implemented
   - revisit templates and other skipped XML artifacts

4. Extend the structural graph beyond the first UI-entrypoint pass.
   - `command` and `handler` nodes are present for form XML
   - edges `form -> command`, `command -> handler`, `form -> handler`, `handler -> method` are present for supported managed forms
   - rich form structure inspired by `Pradushkoai/1c-ai-dev-env` is being added:
     UI elements, `DataPath`, `CommandName`, visibility flags, element events, and parent/depth
   - next target: rebuild active graph projections and expand coverage for non-form command sources

5. Add form-aware MCP workflows.
   - `form_structure_report`
   - form quality hints: empty/overloaded forms, duplicate elements, buttons without commands
   - graph-backed lookup from UI element or DataPath to handler/method

6. Start AST-first BSL analysis migration.
   - keep regex heuristics as fallback
   - move `CALLS` and `USES` toward AST-derived edges
   - target outcome: fewer false positives in callers/callees/usages

7. Harden indexing operations.
   - finish offline/full indexing workflow when model download is unavailable
   - add safer long-run operational scripts for full reindex, graph-only rebuild, and backup

8. Extend multi-configuration runtime from switching to cross-config analysis.
   - optional `config_id` on MCP tools where switching is not enough
   - base/extension relationship-aware reports
   - no mixed results across different exports

## Priority View

### Priority 1. Developer Navigation

- richer module/form entry points above the current baseline
- better developer workflows on top of deterministic navigation

Why:

- this navigation layer is now present and should become the base for higher-level programming workflows

### Priority 2. Workflow-Level MCP Assistance

- `change_impact_report`
- `build_implementation_context`
- `trace_business_flow`

Why:

- these features directly reduce the amount of manual chaining between search, snippets, dependencies, and call graph when implementing a real task

### Priority 3. Structural Precision

- `form -> command -> handler`
- command/handler graph nodes. Done for the first pass.
- `form -> element -> handler -> method`. Done for the first pass in the graph builder.
- AST-based `CALLS` / `USES`

Why:

- this is the biggest remaining step toward reliable impact analysis and UI flow tracing

### Priority 4. Index Coverage

- module summary chunks
- commands
- event subscriptions
- common forms
- templates where they matter

Why:

- better coverage improves both semantic search and exact navigation

### Priority 5. Ops and Maintainability

- stable offline indexing flow
- backup/reindex scripts
- continued regression growth
- roadmap/backlog/doc synchronization

Why:

- the system is already useful; now it needs to become easier to operate safely

### Priority 6. Cross-Config Platform Mode

- tool-level config scoping
- base/extension cross-config navigation
- merged impact analysis across related exports

Why:

- config registry already gives us separate storages; the next value is understanding related exports together

## Current Step

1. Deterministic lookup tools. Done.
2. `index_status`. Done.
3. GraphRepository + Memgraph runtime migration. Done.
4. Graph-first dependencies/usages/callers/callees. Done.
5. Search ranking improvements for metadata/code/noisy queries. Done for the first pass.
6. Metadata/code convergence. Done for the first pass.
7. `explain_search_result`. Done.
8. `reindex_file` / `reindex_path`. Done.
9. Module-level navigation tools. Done:
   - `find_module`
   - `find_common_module`
   - `list_module_methods`
10. Config registry + runtime switching for multiple exports. Done.
11. Workflow-level assistance tools. Done for the first pass:
    - `change_impact_report`
    - `build_implementation_context`
    - `trace_business_flow`
12. Form UI-entrypoint graph layer. Done for the first pass:
    - `command` nodes
    - `handler` nodes
    - `form -> command -> handler -> method`
13. Rich form structure layer. Done for the first graph-builder pass:
    - `form_element` nodes
    - `DataPath`, `CommandName`, visibility/read-only flags
    - `form -> element -> handler -> method`
14. Active graph projections rebuilt for `do_cf` and `do_cfe` on 2026-07-31.
15. First form-aware MCP report implemented:
    - `form_structure_report`
    - reads form module, commands, top-level elements, events, handlers and resolved handler methods from graph projection
    - includes first form quality hints for overloaded forms, duplicate element names, unresolved command links, passive command-like elements and handlers without resolved methods
16. Module-summary index layer implemented:
    - one `code_module_summary` chunk per BSL module
    - method count and method-name preview in module payload
    - `search_modules` tool with vector + lexical ranking
    - `find_module` enrichment from module-summary payloads
17. Form-command index layer implemented:
    - one `metadata_command` chunk per managed-form command
    - command name, title, tooltip, action, form and owner payload
    - `search_commands` with Qdrant semantic search and Memgraph lexical fallback
18. Outcome-oriented plan adopted: `task -> change -> validation -> diff`.
19. First `analyze_task` contract implemented:
    - metadata/code evidence candidates
    - graph coverage and concrete files/methods
    - base/extension scope and extension-first recommendation
    - risks, implementation plan and validation checklist
20. First base/extension overlay inventory implemented:
    - `extension_overlay_report`
    - compares metadata objects, forms, modules, methods and commands by stable keys
    - keeps configs isolated and distinguishes stable-key overlay candidates from annotation-resolved method overlays
21. Extension method annotations implemented:
    - parses `&Перед`, `&После`, `&Вместо`, `&ИзменениеИКонтроль`
    - resolves extension methods to base methods by normalized module path and annotation target
    - skips nested duplicate export roots discovered under registered export directories
    - current `do_cfe`: `745/753` annotation targets resolved
22. First changed-file validation slice implemented:
    - `validate_bsl`
    - method boundary and duplicate declaration checks
    - directive, region and preprocessor structure checks
    - explicitly marked as bootstrap until BSL Language Server integration
23. Multi-file validation implemented:
    - `validate_changed_files`
    - Git changed/untracked BSL discovery or explicit file/directory scope
    - aggregate status and per-file diagnostics
    - nested export roots are excluded consistently
24. Post-change workflow implemented:
    - `post_change_report`
    - combines structural validation, changed-method graph impact and index/graph freshness
    - returns actionable overall status and required next actions
25. Explicit embedding provider and offline guardrails implemented:
    - `EMBEDDING_PROVIDER=local|openai` with safe `local` default
    - OpenAI key no longer enables paid embeddings implicitly
    - strict `EMBEDDING_LOCAL_ONLY` + `EMBEDDING_MODEL_PATH` mode
    - `embedding_status` readiness diagnostics without model/API initialization
26. Controlled local Qdrant reindex started; `do_cf` is running in the background.
27. Event-subscription index layer implemented:
    - one `metadata_event_subscription` chunk per subscription
    - source types, event, handler module and handler method in payload
    - `search_event_subscriptions` combines semantic retrieval with deterministic export fallback
    - `do_cf` contains `251` event-subscription chunks
    - full schema-4 `do_cf` reindex is running in the background; `do_cfe` follows after it completes
28. Optional BSL Language Server validation adapter implemented:
    - executable and `.jar` launch modes via `BSL_LS_BINARY`
    - isolated temporary source tree, JSON reporter parsing and configurable timeout
    - explicit `unavailable`, `timeout`, `failed`, and `completed` states with bootstrap fallback
    - integrated into `validate_bsl`, `validate_changed_files`, and `post_change_report`
    - real `v1.0.6` Windows-runtime analysis verified: JSON diagnostics are mapped back to original export files
    - direct `v1.0.6` exec JAR is incompatible with the installed Java 17 and requires Java 21; readiness probe detects this before analysis
    - persistent BSL Language Server installation still needs to be configured through `BSL_LS_BINARY`
29. Lightweight maintenance checks added:
    - `run_mcp_regression.py --list` lists live regression cases without importing MCP runtime
    - `smoke_test_runtime_config.py` verifies registry/default/extension/fallback runtime selection without Qdrant or Memgraph
    - `search_backlog.md` synchronized with completed P1.2 convergence and P2.1 diagnostics work
30. Local verification ergonomics improved:
    - `run_mcp_regression.py` supports `--tool`, `--list-format json` and `--fail-fast`
    - `smoke_test_graph_backends.py` supports explicit `--targets json|memgraph|json,memgraph`
    - `smoke_test_metadata_parsers.py` covers event-subscription XML parsing without live services
    - `.gitignore` added for Python caches, local env files, logs, temp files and generated index/graph snapshots
31. Offline maintenance coverage expanded:
    - `run_mcp_regression.py --summary` reports selected live regression counts by tool category
    - `smoke_test_runtime_config.py` now covers embedding provider aliases/errors, custom cache paths, list-shaped registries and invalid registry JSON
    - `smoke_test_bsl_language_server.py` covers severity normalization and JSON report parsing without BSL LS installed
    - `run_registered_index_background.py` supports `--dry-run`, `--graph-only`, `--force-reindex` and `--index-filter`
32. Bootstrap BSL validation extracted and tightened:
    - `bsl_structure.py` now owns the lightweight structural analyzer used by MCP validation fallback
    - function-without-return warnings and procedure-returning-value errors are detected before optional BSL LS runs
    - inline `//` comments inside string literals are handled when analyzing `Возврат` statements
    - `smoke_test_bsl_structure.py` covers method boundaries, duplicate methods, directives, regions, preprocessor blocks and return checks
33. Memgraph Docker healthcheck verified:
    - `docker-compose.memgraph.yml` now checks Bolt readiness with `mgconsole`
    - live `smoke_test_graph_backends.py --targets json,memgraph --memgraph-batch-size 2` passed against the local Docker container
34. Docker volume backup/reload procedure added:
    - `scripts/backup_docker_volume.ps1` creates `.tar.gz` archives for Qdrant/Memgraph Docker volumes
    - `scripts/restore_docker_volume.ps1` restores an archive into a volume with `-DryRun` and required `-Force` safety
    - `README.md` documents backup and restore commands, including stopping Memgraph before a real restore

## Long-Tail Ideas

These ideas stay intentionally behind the main execution queue. They can be reconsidered later, but they are not part of the highest-value near-term plan.

### 1. `compare_exports` or `diff_configs`

Compare two exports or two indexed configs and answer:

- what metadata objects changed
- what methods changed
- what graph links changed

Why it may matter:

- useful for upgrades, vendor merges, and regression triage

### 2. `find_extension_points`

Given an object or business task, find:

- subscription candidates
- common modules already used for similar logic
- typical validation/posting hooks

Why it may matter:

- helps the AI suggest where to place a change, not only where similar code already exists

### 3. `platform_help_search`

Optional platform knowledge layer:

- search in local/offline platform help
- link configuration code patterns to 1C platform semantics

Why it may matter:

- closes the gap between "what exists in this config" and "how the platform feature should work"

### 4. `safe_refactor_plan`

Given a method/object, produce:

- impacted nodes
- rename/update candidates
- manual verification checklist

Why it may matter:

- especially useful when AI participates in larger refactorings, not only small feature tasks

## Definition of Done For The Current Phase

The current near-term phase can be considered complete when all of the following are true:

- module-level deterministic navigation exists
- workflow-level assistance exists at least for:
  - impact analysis
  - context assembly for implementation
  - business-flow tracing
- graph covers at least `form -> command -> handler -> method` for the supported cases
- index coverage includes module summaries and more non-method entities
- search diagnostics and targeted repair stay green in regression
- the next migration track to AST-first analysis is started with a concrete internal format

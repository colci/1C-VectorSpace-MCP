# Effective 1C Development Plan

## Product Goal

Сократить время и риск разработки изменений в конфигурациях 1С, предоставляя AI-ассистенту проверяемый контекст конкретной конфигурации, безопасный выбор точки расширения, анализ влияния, поддержку подготовки изменения и автоматическую проверку результата.

Основной workflow проекта:

```text
ТЗ -> анализ -> выбор точки изменения -> реализация -> validation -> tests -> impact/diff -> поставка
```

Проект оценивается не количеством MCP tools и типов chunks, а тем, насколько быстрее и безопаснее разработчик выполняет реальное ТЗ.

## Product Principles

- Конкретная конфигурация важнее общих шаблонов 1С.
- Детерминированная навигация предпочтительнее semantic search, когда сущность уже известна.
- Qdrant хранит semantic retrieval, Memgraph хранит структурные связи, исходные файлы остаются source of truth.
- Изменение через расширение предпочтительнее изменения основной конфигурации, если это технически возможно.
- Любая рекомендация должна сопровождаться источниками: объектами, файлами, методами, строками и связями.
- Любое изменение должно завершаться validation и повторным impact analysis.
- Готовые инструменты BSL/1С следует интегрировать раньше разработки собственных сложных анализаторов.

## Phase 1. Reliable Read-Only Assistant

Цель: AI надежно понимает текущую конфигурацию.

Состав:

- metadata/code/form/module/command search;
- deterministic `find_*` navigation;
- dependencies, usages, callers/callees;
- form and module structure reports;
- incremental indexing and index diagnostics;
- Qdrant + Memgraph health and freshness checks.

Текущее состояние: реализован первый рабочий слой.

Definition of Done:

- правильная функциональная область находится в top-3 минимум для 85% контрольных задач;
- точная сущность находится не более чем за 3 MCP-вызова;
- incremental reindex воспроизводим и не оставляет смешанную схему индекса;
- активная конфигурация имеет понятный health/freshness status.

## Phase 2. Task Analysis

Цель: превратить ТЗ в конкретный и проверяемый план реализации.

Основной инструмент:

- `analyze_task`

Результат анализа:

- кандидаты metadata/code/form/module;
- существующие похожие реализации;
- рекомендуемая точка изменения;
- base/extension context;
- зависимости и потенциальное влияние;
- риски и неизвестные данные;
- план реализации;
- validation checklist.

Definition of Done:

- типовое ТЗ анализируется одним верхнеуровневым MCP-вызовом;
- вывод содержит конкретные файлы и методы, а не только semantic matches;
- явно указано, какие выводы подтверждены графом, а какие являются best-effort рекомендацией.

## Phase 3. Base And Extension Awareness

Цель: выбирать безопасный способ изменения с учетом основной конфигурации и расширений.

Состав:

- объединенное представление base + extension;
- overlay metadata objects, forms, modules and methods;
- поддержка `&Перед`, `&После`, `&Вместо`, `&ИзменениеИКонтроль`;
- поиск заимствованных и расширенных объектов;
- extension-aware impact report;
- поиск конфликтов между расширениями;
- `find_extension_points`.

Definition of Done:

- для найденной точки изменения видно, существует ли override/augmentation в расширении;
- отчет не смешивает одноименные сущности разных конфигураций;
- AI может обосновать выбор основной конфигурации или расширения.

## Phase 4. Safe Change Preparation

Цель: подготовить однозначное изменение, совместимое с архитектурой текущей базы.

Инструменты:

- `prepare_change`;
- `find_similar_implementation`;
- `suggest_target_module`;
- `safe_refactor_plan`.

Результат:

- целевые файлы и диапазоны строк;
- рекомендуемый паттерн реализации;
- существующие аналоги;
- затрагиваемые интерфейсы и данные;
- тестовые сценарии;
- rollback checklist.

## Phase 5. Validation Loop

Цель: автоматически проверить подготовленное или выполненное изменение.

Приоритет интеграций:

1. BSL Language Server или совместимый parser/linter.
2. Синтаксическая проверка BSL.
3. Проверка клиент-серверного контекста.
4. Проверка стандартов разработки 1С.
5. Проверка запросов, транзакций и привилегированного режима.
6. YAxUnit и/или Vanessa Automation.
7. Проверка конфигурации средствами платформы, если она доступна.

Инструменты:

- `validate_bsl`;
- `validate_changed_files`;
- `run_tests`;
- `review_change`;
- `post_change_impact_report`.

Definition of Done:

- validation запускается одной командой для измененных файлов;
- ошибки содержат файл, строку, правило и предлагаемое следующее действие;
- результат тестов включается в итоговый отчет задачи.

## Phase 6. Delivery And Diff

Цель: подтвердить выполнение ТЗ и безопасно подготовить поставку.

Инструменты:

- `compare_exports` / `diff_configs`;
- `summarize_change`;
- `verify_task_completion`;
- `release_checklist`.

Проверки:

- измененные metadata objects and methods;
- изменение graph links;
- роли и права;
- совместимость расширения;
- наличие и результат тестов;
- актуальность индекса после изменения.

## Phase 7. Advanced Structural Precision

Цель: повысить точность после замыкания полезного end-to-end workflow.

Состав:

- AST-first `CALLS` / `USES`;
- точное разрешение клиентского и серверного контекста;
- query AST;
- event subscriptions and scheduled jobs;
- roles/RLS;
- SKD and query structure;
- deeper data-flow and security analysis.

Regex/heuristics остаются fallback до подтверждения AST-покрытия regression-набором.

## Execution Priorities

### P0. Operability And Trust

- offline/local embedding workflow;
- Qdrant/Memgraph healthchecks;
- reproducible full/incremental reindex;
- backup/recovery;
- freshness diagnostics;
- regression on at least base config and extension.

### P1. Base/Extension Awareness

- overlay navigation;
- extension method annotations;
- safe extension points;
- extension-aware impact analysis.

### P2. Task-To-Change Workflow

- `analyze_task`;
- `prepare_change`;
- similar implementation lookup;
- concrete implementation and validation plan.

### P3. Validation

- BSL LS/parser integration;
- syntax and standards checks;
- changed-file validation;
- automated tests.

### P4. Structural Precision

- AST-first analysis;
- event subscriptions;
- non-form commands and handlers;
- scheduled jobs and integrations.

### P5. Additional Coverage

- roles/RLS;
- SKD;
- templates/layouts;
- platform help;
- rare metadata types.

## Success Metrics

### Retrieval

- top-1 and top-3 accuracy;
- Mean Reciprocal Rank;
- result stability between reindexes;
- percentage of searches that continue into deterministic navigation.

### Graph

- precision/recall for callers and callees;
- percentage of handlers resolved to methods;
- percentage of metadata usages with confirmed targets;
- graph coverage by entity and edge type.

### Development Workflow

- time from task to selected change point;
- MCP calls required before implementation;
- percentage of generated plans accepted without substantial correction;
- percentage of changes passing validation on first attempt;
- regressions detected before running 1С manually.

### Operations

- full and incremental reindex duration;
- peak RAM usage;
- MCP response P50/P95;
- index freshness lag;
- embedding cost per configuration;
- recovery time after backend failure.

## Immediate Work Queue

1. Run and verify controlled local Qdrant indexing for `do_cf` and `do_cfe` using the explicit local provider.
2. Keep the remaining unresolved extension annotation targets explicit and add attribute-level overlays.
3. Add regression cases for overlay precision and task-analysis evidence quality.
4. Evaluate BSL Language Server integration before building a custom AST pipeline.
5. Extend `validate_changed_files` and `post_change_report` with BSL LS diagnostics.

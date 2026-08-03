# Session Handoff: Memgraph Write Failure After Full Reindex

## Дата

- 2026-07-03

## Симптом

Полный reindex по выгрузке `D:\Export\UNF` успешно дошел до конца индексации в Qdrant:

- `32833` файлов
- примерно `2:29:52`

После этого индексатор перешел к записи graph projection в Memgraph и упал на Bolt-соединении:

```text
neo4j.exceptions.ServiceUnavailable: Failed to read from defunct connection
IPv4Address(('localhost', 7687)) (ResolvedIPv4Address(('127.0.0.1', 7687)))
```

Корневой фрагмент:

- `OSError("No data")`
- далее `ServiceUnavailable`
- падение произошло внутри `graph_writers.py` на `session.run(query, rows=batch).consume()`

## Где смотреть логи

- [logs/full_reindex_20260703_000351.out.log](/D:/work/1C-VectorSpace-MCP/logs/full_reindex_20260703_000351.out.log:1)
- [logs/full_reindex_20260703_000351.err.log](/D:/work/1C-VectorSpace-MCP/logs/full_reindex_20260703_000351.err.log:1)

Ключевой хвост stdout:

- `Построение graph projection...`
- `Запись graph projection в Memgraph 'bolt://localhost:7687' (config_id='default', nodes=202901, edges=540420)...`
- затем ошибка записи в `memgraph`

## Что уже выяснено

- Индексация Qdrant завершилась успешно.
- Сбой произошел не во время эмбеддингов и не во время Qdrant-upsert.
- Проблемная стадия:
  - bulk write в Memgraph
  - объем graph projection:
    - `202901` узлов
    - `540420` ребер
- После сбоя порт `localhost:7687` перестал отвечать.
- Это похоже либо на:
  - обрыв long-lived Bolt session
  - либо на падение/рестарт самого Memgraph под нагрузкой

## Что уже изменено в коде

### [graph_writers.py](/D:/work/1C-VectorSpace-MCP/graph_writers.py:1)

Внесена защита от long-running write:

- добавлен `keep_alive=True` для драйвера
- длинная запись через один session заменена на короткие session/query-вызовы по батчам
- добавлен retry для retryable ошибок:
  - `ServiceUnavailable`
  - `SessionExpired`
  - ошибки вида `defunct connection`, `failed to read`, `no data`, `timed out`
- после retry принудительно закрывается и пересоздается driver
- добавлены прогресс-логи по батчам записи

Новые параметры writer:

- `retry_attempts`
- `retry_backoff_seconds`
- `progress_every`

### [index_config.py](/D:/work/1C-VectorSpace-MCP/index_config.py:1)

Добавлены env-параметры:

- `MEMGRAPH_RETRY_ATTEMPTS`
- `MEMGRAPH_RETRY_BACKOFF_SECONDS`

Они передаются в `build_graph_writers(...)`.

### [.env.memgraph.example](/D:/work/1C-VectorSpace-MCP/.env.memgraph.example:1)

Добавлены примеры:

- `MEMGRAPH_RETRY_ATTEMPTS=3`
- `MEMGRAPH_RETRY_BACKOFF_SECONDS=2`

## Что еще не подтверждено

- Smoke test `json+memgraph` после правки не был завершен успешно, потому что сам Memgraph уже не отвечал на `7687`.
- Последняя проверка:
  - `Test-NetConnection localhost -Port 7687` => `False`

## Важный операционный контекст

- Была попытка перезапустить Memgraph через `docker compose -f docker-compose.memgraph.yml up -d memgraph`
- Эта ветка не была доведена до конца, потому что пользователь прервал ход
- Нужно заново проверить:
  - жив ли контейнер
  - поднялся ли Memgraph
  - есть ли crash/restart loop

## Рекомендуемая точка продолжения в следующем сеансе

1. Проверить, отвечает ли `localhost:7687`.
2. Если нет, поднять или перезапустить Memgraph.
3. Посмотреть состояние контейнера и логи Memgraph.
4. Прогнать короткий smoke на обновленном writer.
5. Повторить только стадию graph write:
   - либо через `GRAPH_ONLY=1`
   - либо через отдельный запуск записи graph projection
6. Если проблема повторится:
   - уменьшить `MEMGRAPH_BATCH_SIZE` до `250` или `500`
   - посмотреть, не падает ли сам Memgraph по памяти/timeout
   - при необходимости добавить staging-режим записи или более щадящую стратегию delete/write

## Практические гипотезы

- Наиболее вероятная причина: слишком длинная или тяжелая bulk-сессия в Memgraph/Bolt.
- Второй вероятный вариант: Memgraph контейнер падает под нагрузкой на объеме `202k/540k`.
- Уже внесенная правка должна уменьшить риск за счет:
  - коротких сессий
  - reconnect
  - retry
- Но если сам Memgraph умирает как процесс, этого может быть недостаточно без уменьшения батча или ops-настройки контейнера.

## Продолжение 2026-07-04

Проблема записи в Memgraph была доведена до успешного полного прогона.

Что изменено:

- `MemgraphGraphWriter` больше не пишет тяжелое поле `document` в узлы Memgraph.
- Полные тексты и карточки остаются в JSON/Qdrant, а Memgraph хранит только структурные свойства графа.
- Перед bulk write добавлено создание structural indexes:
  - `GraphProjectionMeta(config_id)`
  - `GraphNode(config_id)`
  - `GraphNode(id)`
- `smoke_test_graph_backends.py` теперь проверяет, что JSON сохраняет `document`, а Memgraph его не получает.

Проверки:

- `GRAPH_WRITE_TARGETS=json` smoke: успешно.
- `GRAPH_WRITE_TARGETS=json,memgraph` smoke: успешно.
- `python -m py_compile graph_writers.py smoke_test_graph_backends.py`: успешно.
- Полный `GRAPH_ONLY=1` с `GRAPH_WRITE_TARGETS=memgraph`, `MEMGRAPH_BATCH_SIZE=500`: успешно.

Фактический результат полного прогона:

- `nodes=202901`
- `edges=540420`
- `nodes_with_document=0`
- `GraphProjectionMeta.generated_at=2026-07-04T23:31:11`

Важное наблюдение:

- Первый повторный полный запуск без индексов был остановлен по таймауту через 15 минут и оставил частичный `default` в Memgraph (`48427` узлов, `0` ребер).
- После добавления индексов повторный полный запуск сам удалил частичный `default` и полностью перезаписал graph projection примерно за 2.5 минуты.

## Продолжение 2026-07-05

Memgraph включен как runtime backend MCP-сервера.

Что изменено:

- В `.env` добавлены параметры:
  - `GRAPH_BACKEND=memgraph`
  - `GRAPH_WRITE_TARGETS=json,memgraph`
  - `MEMGRAPH_BATCH_SIZE=500`
  - retry-настройки Memgraph writer
- В `GraphRepository` добавлен `find_nodes(kind, properties, limit)`:
  - JSON backend сканирует локальный snapshot
  - Memgraph backend выполняет точный Cypher lookup по свойствам
- Графовые MCP-инструменты переведены на graph-first resolution:
  - `get_dependencies`
  - `list_object_forms`
  - `find_usages`
  - `get_callers`
  - `get_callees`
- Для графовых инструментов Qdrant fallback теперь используется только если graph repository недоступен.
- В Memgraph writer добавлены дополнительные индексы:
  - `GraphNode(kind)`
  - `GraphNode(object_type)`
  - `GraphNode(object_name)`
  - `GraphNode(method_name)`
  - `GraphNode(module_path)`

Проверки MCP runtime:

- `get_dependencies("Организации", "Catalog")`: успешно, примерно `0.6s`
- `list_object_forms("Организации", "Catalog")`: успешно, примерно `0.5s`
- `find_usages("Организации", "Catalog", limit=5)`: успешно, примерно `0.4s`
- `get_callees(...)` на реальном method node: успешно, примерно `0.45s`
- `get_callers(...)` на реальном method node: успешно, примерно `0.25s`

Важное наблюдение:

- При проверках из PowerShell кириллицу лучше передавать через UTF-8/Unicode escapes, иначе тестовый heredoc может превратить строку в `????` и вызвать ложный miss.
- Реальный MCP JSON-вызов передает Unicode нормально; данные в Memgraph проверены через `json.dumps(..., ensure_ascii=True)`.

## Продолжение 2026-07-05: Search Backlog P0

Продолжено развитие по `search_backlog.md`.

Что изменено:

- Закрыт первый слой `P0.1` для metadata search:
  - добавлен общий набор register metadata types
  - усилены регистры для запросов с intent `расчеты`, `взаиморасчеты`, `остатки`, `движения`, `задолженность`, `оплата`
  - понижены низкосигнальные типы вроде `Subsystem`, `Role`, generic `Catalog` при register-intent
  - добавлены универсальные semantic expansions для регистров и частых документов: поступление/реализация товаров, заказ клиента/поставщику
- Сделан первый проход `P0.2` для code search:
  - добавлен validation/business-rule intent detection
  - добавлены дополнительные vector-запросы для `проверка`, `контроль`, `проведение`, `остатки`, `отрицательные`, `недостаточно`
  - усилены методы формы `ОбработкаПроведения`, `ПередПроведением`, `ВыполнитьКонтроль`, `Проверить*`, `Отказ`
  - понижены UI/form handlers вроде `ПриИзменении`, `ПриАктивизацииСтроки`, `ПриНачалеРедактирования`, `Очистить*`

Проверки:

- `python -m py_compile mcp_server.py`: успешно.
- `search_metadata("расчеты с покупателями")` -> top-1 `AccumulationRegister.РасчетыСПокупателями`.
- `search_metadata("структура документа поступления товаров")` -> top-1 `Document.ПриходнаяНакладная`.
- `search_code("где проверяется возможность проведения документа при отрицательных остатках")` теперь возвращает business-rule candidates в top-5:
  - `Documents.ПроизводствоМП.МодульОбъекта -> ОбработкаПроведения`
  - несколько `Documents.*.МодульМенеджера -> ВыполнитьКонтроль`

Следующая логичная точка:

1. Расширить live regression pack (`P2.2`) отдельным скриптом/фикстурой, чтобы эти проверки не делать вручную.
2. Затем перейти к `P1.1`/`P1.2`: noisy natural-language normalization и convergence metadata/code по одному бизнес-топику.

## Продолжение 2026-07-05: Regression Pack

Выполнен следующий шаг из `search_backlog.md` / `P2.2`.

Что добавлено:

- `run_mcp_regression.py` - компактный live regression runner для MCP-поиска.
- Скрипт изначально проверял 5 стабилизированных сценариев:
  - `расчеты с покупателями`
  - `структура взаиморасчетов с контрагентом`
  - `реквизиты договора контрагента`
  - `структура документа поступления товаров`
  - `где проверяется возможность проведения документа при отрицательных остатках`
- В `README.md` добавлена команда запуска regression pack.
- В `mcp_regression_cases.md` добавлена ссылка на автоматизированный subset.
- В `roadmap.md` обновлен `Current Step`: GraphRepository/Memgraph runtime помечен как выполненный, следующим шагом указаны noisy-query handling и metadata/code convergence.

Проверки:

- `python -m py_compile run_mcp_regression.py mcp_server.py`: успешно.
- `.\.venv\Scripts\python.exe run_mcp_regression.py`: успешно, `5/5`.

Следующая логичная точка:

1. Перейти к `P1.1`: нормализация длинных разговорных запросов.
2. Добавить пары `short query` vs `noisy query` в `run_mcp_regression.py`.
3. После этого перейти к `P1.2`: проверять, что metadata и code search сходятся в одной бизнес-области.

## Продолжение 2026-07-05: Noisy Query Handling

Выполнен первый проход `P1.1` из `search_backlog.md`.

Что изменено:

- Расширены conversational/filler stopwords для длинных русскоязычных запросов.
- Добавлен `build_retrieval_queries(...)`: vector search теперь использует исходный запрос и компактное смысловое ядро.
- `search_metadata(...)` использует несколько retrieval-запросов и объединяет результаты.
- `search_code(...)` расширяет validation-запросы для формулировок `нельзя провести`, `не хватает товара`, `контроль остатков`.
- Добавлен metadata business-anchor scoring для терминов вроде `договор`, `поступление`, `товар`, `расчеты`, `цены`, `заказ`.
- В `run_mcp_regression.py` добавлены noisy-кейсы.

Проверки:

- `python -m py_compile mcp_server.py run_mcp_regression.py`: успешно.
- `.\.venv\Scripts\python.exe run_mcp_regression.py`: успешно, `8/8`.
- `подскажи в каком объекте лежат реквизиты договора для работы с контрагентом` -> top-1 `Catalog.ДоговорыКонтрагентов`.
- `мне нужно понять где в конфигурации описана структура документа поступления товаров` -> top-1 `Document.ПриходнаяНакладная`.
- `мне нужно понять где в конфигурации проверяют что документ нельзя провести если не хватает товара` -> top-5 содержит несколько `ОбработкаПроведения`.

Следующая логичная точка:

1. Перейти к `P1.2`: metadata/code convergence.
2. Добавить regression cases, которые проверяют, что metadata search и code search по одному бизнес-вопросу сходятся в одной функциональной области.
3. После этого начать `P2.1`: диагностический инструмент `explain_search_result`.

## Продолжение 2026-07-05: Universal Runtime Guardrail

Зафиксировано важное архитектурное правило: runtime-код MCP не должен быть привязан к конкретной загруженной конфигурации.

Что проверено и изменено:

- В `mcp_server.py` нет hardcode-имен объектов текущей базы вроде `ДоговорыКонтрагентов`, `ПриходнаяНакладная`, `РасчетыСПокупателями`.
- Профильные semantic patterns переименованы в `UNF_SEMANTIC_LOOKUP_PATTERNS` и подключаются только при `CONFIG_PROFILE=unf`.
- Generic runtime использует только универсальные 1С-понятия и бизнес-якоря: документы, справочники, регистры, договоры, товары, расчеты, остатки, цены, проведение, проверки.
- `run_mcp_regression.py` больше не проверяет точные имена объектов текущей базы; он проверяет классы результатов и смысловые якоря в top-3/top-5.
- В `README.md` добавлен раздел `Универсальность`.

Проверки:

- `python -m py_compile mcp_server.py run_mcp_regression.py`: успешно.
- `.\.venv\Scripts\python.exe run_mcp_regression.py`: успешно, `8/8`.

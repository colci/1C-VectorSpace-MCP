# 1C-VectorSpace-MCP

`1C-VectorSpace-MCP` - поставочный репозиторий MCP/RAG-сервера для разработки и анализа конфигураций 1С по файловой выгрузке `XML + BSL`.

Проект превращает выгруженную конфигурацию 1С в два дополняющих друг друга индекса:

- `Qdrant` - векторная база для семантического поиска по коду, метаданным и формам.
- `Memgraph` - графовая база для точной структурной навигации: зависимости, формы объекта, usages, callers/callees.

MCP-сервер предоставляет эти возможности локальному AI-клиенту через Model Context Protocol.

## Зачем Это Нужно

Цель проекта - дать AI-ассистенту практический доступ к большой конфигурации 1С: не только к отдельным файлам, а к поиску, структуре и связям внутри выгрузки.

MCP помогает:

- искать BSL-код, объекты метаданных, формы и методы по смыслу;
- понимать связи между объектами, формами, модулями и методами;
- находить использования объектов и проходить call graph;
- быстро получать нужные фрагменты исходных файлов по найденным путям и строкам.

Проект не заменяет 1С, EDT или Git. Он строит локальный индекс файловой выгрузки конфигурации и отдает его в MCP-клиент.

## Ощутимая Выгода

Если AI-модель просто имеет доступ к исходникам конфигурации, она все равно ограничена теми файлами и фрагментами, которые попали в ее текущий контекст. В большой 1С-конфигурации этого часто недостаточно: нужная логика может быть разбросана между документом, формой, модулем менеджера, общими модулями, регистрами и вызовами из других мест.

Этот проект добавляет поверх исходников поисково-навигационный слой. AI-клиент получает карту конфигурации: где находятся похожие реализации, какие объекты связаны между собой, где используется метод или объект, какие реальные паттерны разработки уже применяются.

Благодаря этому модель меньше опирается на "типовой код 1С вообще" и чаще использует контекст конкретной базы: реальные имена объектов, существующие общие модули, принятые способы проверок, проведения, заполнения и сообщений пользователю. Это особенно полезно для задач, где важно не просто написать синтаксически корректный код, а встроить доработку в уже существующую архитектуру конфигурации.

## Универсальность

Runtime-логика MCP не должна быть привязана к конкретной конфигурации, например только к УНФ, ERP, БП или текущей тестовой базе. В `mcp_server.py` допустимы только универсальные 1С-понятия и эвристики: типы метаданных, формы, модули, регистры, документы, проведение, проверки, остатки, расчеты, договоры, цены и похожие бизнес-термины.

Если нужны правила для конкретной линейки конфигураций, они должны включаться через профиль, например `CONFIG_PROFILE=unf`, и не влиять на режим `generic`.

Регрессионные проверки могут использовать текущий индекс как живой пример, но не должны превращать runtime-код MCP в набор hardcode-имен объектов конкретной базы.

## Как MCP Помогает Писать Код По ТЗ

Основной план развития проекта зафиксирован в [effective_1c_development_plan.md](effective_1c_development_plan.md). Он строится вокруг полного цикла `ТЗ -> анализ -> изменение -> validation -> tests -> impact/diff`, а не вокруг количества отдельных индексов и MCP-инструментов.

При работе по техническому заданию MCP позволяет сначала разобраться, как похожая логика уже устроена в текущей базе, и только потом писать новую доработку:

1. Пользователь дает ТЗ.
2. AI через MCP ищет похожие места в проиндексированной конфигурации.
3. AI формирует план изменения на основе найденных объектов, методов и зависимостей.
4. Новый код пишется с учетом реальных паттернов, имен, модулей и бизнес-правил этой конфигурации.
5. Через графовые инструменты можно проверить затронутые зависимости и потенциальные места влияния.

Важно: MCP не гарантирует автоматическую правильность доработки и не заменяет проверку разработчиком. Его роль - улучшить качество контекста, чтобы код писался не "вслепую", а с опорой на уже существующую архитектуру и бизнес-логику конфигурации.

## Архитектура

```text
AI client
  |
  | MCP stdio
  v
mcp_server.py
  |
  | semantic search
  v
Qdrant

mcp_server.py
  |
  | graph navigation
  v
Memgraph

mcp_server.py
  |
  | snippets
  v
1C export folder: XML + BSL
```

Текущая типовая схема на одной машине:

```text
Windows / Docker Desktop
  ├─ Qdrant: http://localhost:6333
  ├─ Memgraph: bolt://localhost:7687
  ├─ project: D:\work\1C-VectorSpace-MCP
  └─ export: D:\Export\UNF или D:\Export\1C
```

Целевая командная схема:

```text
Developer workstation
  └─ AI client
      └─ ssh -> MCP server host

MCP server host
  ├─ mcp_server.py
  ├─ Qdrant
  ├─ Memgraph
  └─ 1C export folder
```

Важно: `get_file_snippet` читает файлы с диска там, где запущен `mcp_server.py`. Если MCP-сервер работает на отдельном сервере, выгрузка `XML + BSL` должна быть доступна именно на сервере.

## Возможности MCP

Семантический и детерминированный поиск:

- `search_code` - поиск BSL-кода по смыслу.
- `search_metadata` - поиск объектов метаданных и форм по смыслу.
- `find_metadata_object` - точный или почти точный поиск объекта метаданных.
- `find_form` - поиск формы по имени и владельцу.
- `form_structure_report` - отчет по структуре формы: модуль, команды, элементы, события, методы-обработчики и quality hints по перегруженности, дублям и битым связям.
- `search_modules` - семантический и лексический поиск модулей по назначению и списку объявленных методов.
- `search_commands` - поиск команд управляемых форм по имени, заголовку, обработчику и форме-владельцу.
- `search_event_subscriptions` - поиск подписок на события по имени, источнику, событию и обработчику.
- `find_module` - точный или почти точный поиск модуля по имени или `module_path`.
- `find_common_module` - специализированный поиск общего модуля.
- `find_method` - поиск метода по имени, модулю и типу модуля.
- `list_module_methods` - список методов указанного модуля.

Графовая навигация:

- `get_dependencies` - зависимости объекта метаданных, связанные формы и модули.
- `list_object_forms` - формы объекта метаданных.
- `find_usages` - где используется объект или метод.
- `get_callers` - кто вызывает метод.
- `get_callees` - что вызывает метод.
- `change_impact_report` - отчет по влиянию изменения объекта или метода: зависимости, usages, формы/модули, callers и callees.
- `analyze_task` - единый evidence-based анализ ТЗ: кандидаты изменения, base/extension scope, риски, implementation plan и validation checklist.
- `build_implementation_context` - сбор metadata, code и graph-контекста для старта разработки по задаче.
- `trace_business_flow` - best-effort трассировка бизнес-запроса к объектам, методам и связям call graph.
- `extension_overlay_report` - inventory пересечений и новых объектов, форм, модулей, методов и команд между основной конфигурацией и расширением, включая разрешение `&Перед`, `&После`, `&Вместо`, `&ИзменениеИКонтроль`.

Graph projection also includes the first UI-entrypoint layer for managed forms: `form -> command -> handler -> method`, plus direct `form -> handler` event links where the exported form XML exposes event subscriptions.
The form layer now also stores `form_element` nodes with `DataPath`, `CommandName`, visibility/read-only flags and element-level event links, inspired by the form parsing approach in `Pradushkoai/1c-ai-dev-env`.

Диагностика и чтение файлов:

- `index_status` - состояние Qdrant, кэшей, graph backend и свежести индекса.
- `list_configurations` - показать зарегистрированные конфигурации и расширения из `config_registry.json`.
- `switch_configuration` - переключить активную конфигурацию MCP runtime без перезапуска сервера.
- `explain_search_result` - объяснение ранжирования metadata/code результатов, retrieval queries и score breakdown.
- `reindex_file` - принудительная переиндексация одного BSL/XML файла внутри `EXPORT_PATH`.
- `reindex_path` - принудительная переиндексация каталога внутри `EXPORT_PATH`.
- `bsl_ls_status` - готовность optional-интеграции с BSL Language Server без запуска анализа.
- `validate_bsl` - структурная проверка BSL-файла с optional BSL LS diagnostics и безопасным bootstrap fallback.
- `validate_changed_files` - пакетная проверка измененных BSL из Git либо явно заданного файла/каталога; BSL LS включается через `use_bsl_ls=true`.
- `post_change_report` - единый post-change отчет: BSL validation, callers/callees/entrypoints и свежесть index cache/graph; BSL LS включается через `use_bsl_ls=true`.
- `get_file_snippet` - чтение фрагмента файла по пути и диапазону строк.

## Что Хранится Где

Qdrant хранит:

- embeddings;
- текстовые карточки метаданных;
- BSL-методы;
- summary-чанки BSL-модулей с количеством и списком методов;
- формы;
- команды управляемых форм;
- payload для семантического и точного поиска.

Memgraph хранит:

- структурные узлы `metadata`, `form`, `module`, `method`;
- связи `references_metadata`, `contains_form`, `contains_module`, `declares_method`, `calls`, `uses_metadata`;
- легкие свойства узлов и ребер.

Memgraph намеренно не хранит тяжелое поле `document`. Полные тексты остаются в Qdrant и JSON snapshot.

JSON-файлы в репозитории или рабочей папке:

- `indexing_cache_*.json` - состояние файлов для инкрементальной индексации.
- `graph_cache_*.json` - debug/snapshot графа.

## Состав Репозитория

- `mcp_server.py` - MCP-сервер и инструменты для AI-клиента.
- `index_config.py` - индексатор выгрузки 1С в Qdrant и graph projection.
- `bsl_structure.py` - bootstrap-анализ структуры BSL для validation fallback.
- `graph_repository.py` - абстракция чтения графа из JSON или Memgraph.
- `graph_writers.py` - запись graph projection в JSON и Memgraph.
- `export_config.ps1` - выгрузка конфигурации 1С через `ibcmd`.
- `run_index.ps1` - запуск индексации.
- `scripts/backup_docker_volume.ps1` - backup Docker volumes Qdrant/Memgraph в `.tar.gz`.
- `scripts/restore_docker_volume.ps1` - restore Docker volume из backup-архива с защитой `-Force`.
- `docker-compose.memgraph.yml` - локальный Memgraph и Memgraph Lab.
- `.env.memgraph.example` - пример настроек Memgraph.
- `smoke_test_graph_backends.py` - проверка JSON/Memgraph writer и repository.
- `smoke_test_runtime_config.py` - offline-проверка multi-config registry и runtime selection.
- `smoke_test_metadata_parsers.py` - offline-проверка парсинга event-subscription XML.
- `smoke_test_bsl_language_server.py` - offline-проверка парсинга JSON-отчетов BSL Language Server.
- `smoke_test_bsl_structure.py` - offline-проверка bootstrap-анализатора BSL.
- `run_mcp_regression.py` - live regression pack для ключевых MCP-поисковых сценариев.

## Требования

На машине, где запускается индексатор и MCP-сервер:

- Windows 10/11 или Windows Server.
- Python 3.11+.
- Docker Desktop или Docker Engine.
- Доступ к файловой выгрузке конфигурации 1С.
- Для выгрузки из файловой базы 1С: установленная платформа 1С с `ibcmd.exe`.

Рекомендуемые ресурсы для сервера с Qdrant + Memgraph:

- пилот: 4 CPU, 16 GB RAM, SSD;
- комфортно для команды: 4-8 CPU, 32 GB RAM, SSD от 100 GB.

## Установка С Нуля На Новом Сервере

### 1. Подготовить проект

```powershell
git clone <repo-url> D:\work\1C-VectorSpace-MCP
cd D:\work\1C-VectorSpace-MCP
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. Поднять Qdrant

Если Qdrant еще не развернут, можно поднять локальный контейнер:

```powershell
docker volume create qdrant_storage
docker run -d --name qdrant-local `
  -p 6333:6333 `
  -p 6334:6334 `
  -v qdrant_storage:/qdrant/storage `
  qdrant/qdrant
```

Проверка:

```powershell
Invoke-RestMethod http://localhost:6333
```

### 3. Поднять Memgraph

```powershell
docker compose -f docker-compose.memgraph.yml up -d memgraph
```

Проверка порта:

```powershell
Test-NetConnection localhost -Port 7687
```

Memgraph Lab, если нужен UI:

```powershell
docker compose -f docker-compose.memgraph.yml --profile ui up -d
```

После этого UI будет доступен на `http://localhost:3000`.

### 4. Подготовить `.env`

Создать или отредактировать `.env`:

```env
EXPORT_PATH=D:\Export\1C
QDRANT_URL=http://localhost:6333

CONFIG_NAME=default
CONFIG_ID=default
CONFIG_PROFILE=generic

INDEX_BATCH_SIZE=100
FASTEMBED_THREADS=4
MAX_RAM_PERCENT=90

EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2

GRAPH_BACKEND=memgraph
GRAPH_WRITE_TARGETS=json,memgraph
MEMGRAPH_URI=bolt://localhost:7687
MEMGRAPH_USER=
MEMGRAPH_PASSWORD=
MEMGRAPH_DATABASE=
MEMGRAPH_BATCH_SIZE=500
MEMGRAPH_RETRY_ATTEMPTS=3
MEMGRAPH_RETRY_BACKOFF_SECONDS=2
```

Для гарантированно офлайн-запуска локальной модели добавить:

```env
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL_PATH=D:\Models\paraphrase-multilingual-MiniLM-L12-v2
EMBEDDING_LOCAL_ONLY=true
```

Если сознательно используются OpenAI embeddings, добавить:

```env
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=...
```

Наличие `OPENAI_API_KEY` само по себе не включает OpenAI. Это защищает полный reindex от случайных API-затрат.

### 5. Выгрузить конфигурацию 1С

Если выгрузка уже есть, достаточно указать ее в `EXPORT_PATH`.

Если нужно выгружать файловую базу через `ibcmd`, отредактировать пути в `export_config.ps1`:

```powershell
$DBPATH = "D:\Base\1C"
$EXPORTPATH = "D:\Export\1C"
$USER = "Администратор"
$PASSWORD = ""
```

Запуск:

```powershell
.\export_config.ps1
```

Скрипт использует `ibcmd config export --sync`, поэтому повторные выгрузки должны быть инкрементальными.

### 6. Запустить полную индексацию

```powershell
.\run_index.ps1
```

Или напрямую:

```powershell
.\.venv\Scripts\python.exe index_config.py
```

Результат:

- Qdrant collection `1c_configuration_<config_id>_<model_suffix>`;
- `indexing_cache_*.json`;
- `graph_cache_*.json`;
- graph projection в Memgraph.

### 6.1. Несколько конфигураций и расширения

В репозитории поддержан `config_registry.json`. Для текущего проекта в нем уже зарегистрированы:

- `do_cf` - основная конфигурация `D:\DO\cf`
- `do_cfe` - расширение `D:\DO\cfe`, связанное с `do_cf`

Если файл `config_registry.json` лежит в корне репозитория, он подхватывается автоматически. При необходимости можно указать его явно:

```env
CONFIG_REGISTRY_FILE=D:\work\1C-VectorSpace-MCP\config_registry.json
ACTIVE_CONFIG_ID=do_cf
```

Полная индексация основной конфигурации:

```powershell
$env:ACTIVE_CONFIG_ID="do_cf"
.\.venv\Scripts\python.exe index_config.py
```

Полная индексация расширения:

```powershell
$env:ACTIVE_CONFIG_ID="do_cfe"
.\.venv\Scripts\python.exe index_config.py
```

В результате получаются отдельные хранилища:

- Qdrant: `1c_configuration_do_cf_<model_suffix>` и `1c_configuration_do_cfe_<model_suffix>`
- Graph cache: `graph_cache_do_cf.json` и `graph_cache_do_cfe.json`
- Memgraph projection разделяется по `config_id`

MCP runtime после запуска умеет показать доступные конфигурации через `list_configurations` и переключаться между ними через `switch_configuration`.

### 7. Быстрая проверка

```powershell
$env:GRAPH_WRITE_TARGETS="json,memgraph"
.\.venv\Scripts\python.exe smoke_test_graph_backends.py
```

Проверка MCP runtime:

```powershell
.\.venv\Scripts\python.exe -c "import mcp_server; print(mcp_server.index_status(False))"
```

## Обновление Индекса

Обычный цикл:

```powershell
.\export_config.ps1
.\run_index.ps1
```

Если используется config registry и нужно переиндексировать конкретную конфигурацию или расширение:

```powershell
$env:ACTIVE_CONFIG_ID="do_cf"
.\run_index.ps1
```

```powershell
$env:ACTIVE_CONFIG_ID="do_cfe"
.\run_index.ps1
```

Только перестроить граф без пересчета embeddings и без Qdrant:

```powershell
$env:GRAPH_ONLY="1"
$env:GRAPH_WRITE_TARGETS="json,memgraph"
.\.venv\Scripts\python.exe index_config.py
```

Частичная индексация для тестов:

```powershell
.\run_index.ps1 -Filter "Catalogs\Организации"
```

Важно: при активном `INDEX_FILTER` полный graph cache не перестраивается, потому что граф был бы неполным.

## Настройка Локального MCP-Клиента

MCP-сервер работает через `stdio`. Клиент должен запускать команду Python и общаться с процессом через stdin/stdout.

### Вариант A. Все Локально

Подходит для одиночной разработки на одной машине.

Пример конфигурации MCP-клиента:

```json
{
  "mcpServers": {
    "1c-vectorspace": {
      "command": "D:\\work\\1C-VectorSpace-MCP\\.venv\\Scripts\\python.exe",
      "args": ["D:\\work\\1C-VectorSpace-MCP\\mcp_server.py"],
      "cwd": "D:\\work\\1C-VectorSpace-MCP"
    }
  }
}
```

В этом варианте локальная машина должна видеть:

- `QDRANT_URL`;
- `MEMGRAPH_URI`;
- `EXPORT_PATH`.

### Вариант B. MCP И Базы На Сервере, Клиент Локально

Рекомендуемый командный вариант.

На сервере:

- развернут репозиторий;
- подняты Qdrant и Memgraph;
- есть выгрузка 1С;
- выполнена индексация;
- `.env` настроен на серверные пути.

На локальной машине MCP-клиент запускает серверный процесс через SSH:

```json
{
  "mcpServers": {
    "1c-vectorspace": {
      "command": "ssh",
      "args": [
        "user@graph-server",
        "cd /d D:\\work\\1C-VectorSpace-MCP && .\\.venv\\Scripts\\python.exe mcp_server.py"
      ]
    }
  }
}
```

Для Linux-сервера команда будет другой:

```json
{
  "mcpServers": {
    "1c-vectorspace": {
      "command": "ssh",
      "args": [
        "user@graph-server",
        "cd /opt/1C-VectorSpace-MCP && ./.venv/bin/python mcp_server.py"
      ]
    }
  }
}
```

Плюсы этого варианта:

- локальному клиенту не нужен прямой доступ к Qdrant/Memgraph;
- файлы выгрузки читаются на сервере;
- один сервер обслуживает нескольких разработчиков;
- индексация выполняется централизованно.

### Вариант C. MCP Локально, Базы На Сервере

Возможен, но менее удобен для `get_file_snippet`.

В `.env` локального проекта нужно указать серверные адреса:

```env
QDRANT_URL=http://graph-server:6333
MEMGRAPH_URI=bolt://graph-server:7687
GRAPH_BACKEND=memgraph
```

При этом `EXPORT_PATH` должен существовать локально или быть подключен как сетевой диск с теми же путями, которые лежат в payload индекса.

## Настройка Отдельного Сервера Для Команды

Рекомендуемая модель:

- Qdrant и Memgraph работают на одном сервере или в одной серверной зоне.
- `mcp_server.py` запускается на этом же сервере через SSH/MCP stdio.
- Индексацию запускает один ответственный процесс или администратор.
- Разработчики подключаются к MCP через SSH.

Минимальный checklist:

1. Открыть SSH для разработчиков.
2. Не публиковать Memgraph/Qdrant наружу без необходимости.
3. Если порты открываются в сеть, ограничить firewall.
4. Настроить backup Docker volumes.
5. Хранить `.env` отдельно от публичного репозитория, если там есть ключи.
6. Регулярно выполнять `index_status`.

## Перенос С Локальной Машины На Сервер

1. Развернуть репозиторий на сервере.
2. Поднять Qdrant и Memgraph.
3. Скопировать или заново создать выгрузку `XML + BSL`.
4. Скопировать `.env` и заменить пути/адреса.
5. Запустить `.\run_index.ps1` или `python index_config.py`.
6. Проверить `smoke_test_graph_backends.py`.
7. Подключить локальный MCP-клиент через SSH.

Не рекомендуется копировать Docker volumes вручную без понимания версии Docker и путей хранения. Надежнее заново построить индексы из выгрузки 1С.

## Основные Настройки

| Переменная | Назначение |
| --- | --- |
| `EXPORT_PATH` | Путь к выгрузке `XML + BSL`. |
| `QDRANT_URL` | URL Qdrant HTTP API. |
| `CONFIG_NAME` | Человекочитаемое имя конфигурации. |
| `CONFIG_ID` | Стабильный идентификатор конфигурации для коллекций и графа. |
| `ACTIVE_CONFIG_ID` | Активная запись из `config_registry.json`, если включен multi-config runtime. |
| `CONFIG_PROFILE` | Профиль эвристик, сейчас обычно `generic`. |
| `CONFIG_REGISTRY_FILE` | Путь к JSON-реестру нескольких выгрузок и расширений. |
| `CONFIG_KIND` | Тип активной записи: `configuration`, `main` или `extension`. |
| `BASE_CONFIG_ID` | Идентификатор базовой конфигурации для расширения. |
| `COLLECTION_NAME` | Явное имя коллекции Qdrant, если нужно переопределить auto-name. |
| `EMBEDDING_PROVIDER` | Явный provider: `local` или `openai`; по умолчанию `local`. |
| `EMBEDDING_MODEL` | Локальная модель FastEmbed. |
| `EMBEDDING_MODEL_PATH` | Явный путь к локальной модели FastEmbed для offline-режима. |
| `EMBEDDING_LOCAL_ONLY` | Запрещает неявную загрузку; требует `EMBEDDING_MODEL_PATH`. |
| `FASTEMBED_CACHE_DIR` | Явный каталог cache FastEmbed. |
| `OPENAI_API_KEY` | Ключ OpenAI, используется только при `EMBEDDING_PROVIDER=openai`. |
| `INDEX_BATCH_SIZE` | Размер батча чанков для индексации. |
| `FASTEMBED_THREADS` | Количество CPU threads для FastEmbed. |
| `MAX_RAM_PERCENT` | Порог RAM, при котором индексатор делает паузы. |
| `BSL_LS_BINARY` | Путь к executable или `.jar` BSL Language Server; без него используется bootstrap fallback. |
| `BSL_LS_CONFIG` | Optional путь к `.bsl-language-server.json`; иначе используется минимальная временная конфигурация. |
| `BSL_LS_TIMEOUT` | Timeout одного запуска BSL LS в секундах, по умолчанию `60`. |
| `GRAPH_BACKEND` | `json` или `memgraph` для чтения графа MCP-сервером. |
| `GRAPH_WRITE_TARGETS` | `json`, `memgraph` или `json,memgraph` для записи графа индексатором. |
| `MEMGRAPH_URI` | Bolt URI Memgraph. |
| `MEMGRAPH_BATCH_SIZE` | Размер батча записи graph projection. |
| `MEMGRAPH_RETRY_ATTEMPTS` | Количество retry при ошибках Bolt. |
| `MEMGRAPH_RETRY_BACKOFF_SECONDS` | Пауза между retry. |

## Типовые Команды

Проверить статус индекса:

```powershell
.\.venv\Scripts\python.exe -c "import mcp_server; print(mcp_server.index_status(False))"
```

Проверить embedding provider без загрузки модели и API-запросов:

```powershell
.\.venv\Scripts\python.exe -c "import mcp_server; print(mcp_server.embedding_status())"
```

Проверить доступность BSL Language Server:

```powershell
.\.venv\Scripts\python.exe -c "import mcp_server; print(mcp_server.bsl_ls_status())"
```

Для BSL LS `v1.0.6` на Windows рекомендуется официальный Windows distribution со встроенным runtime. Прямой `exec.jar` этого релиза требует Java 21, даже если в общей документации BSL LS указана поддержка Java 17. `bsl_ls_status` выполняет короткий version probe и показывает runtime incompatibility до запуска анализа.

Запустить smoke по JSON без Memgraph:

```powershell
.\.venv\Scripts\python.exe smoke_test_graph_backends.py --targets json
```

Запустить smoke по JSON и Memgraph:

```powershell
.\.venv\Scripts\python.exe smoke_test_graph_backends.py --targets json,memgraph
```

Проверить multi-config runtime без Qdrant/Memgraph:

```powershell
.\.venv\Scripts\python.exe smoke_test_runtime_config.py
```

Проверить парсинг XML-подписок на события без Qdrant/Memgraph:

```powershell
.\.venv\Scripts\python.exe smoke_test_metadata_parsers.py
```

Проверить парсинг отчетов BSL Language Server без установленного BSL LS:

```powershell
.\.venv\Scripts\python.exe smoke_test_bsl_language_server.py
```

Проверить bootstrap-анализатор BSL без Qdrant/Memgraph:

```powershell
.\.venv\Scripts\python.exe smoke_test_bsl_structure.py
```

Посмотреть список live regression cases без запуска MCP runtime:

```powershell
.\.venv\Scripts\python.exe run_mcp_regression.py --list
```

Посмотреть сводку по категориям live regression cases:

```powershell
.\.venv\Scripts\python.exe run_mcp_regression.py --summary
```

Посмотреть regression cases только для одной категории:

```powershell
.\.venv\Scripts\python.exe run_mcp_regression.py --list --tool diagnostics
```

Получить список regression cases в JSON:

```powershell
.\.venv\Scripts\python.exe run_mcp_regression.py --list --list-format json
```

Запустить live regression pack для MCP-поиска:

```powershell
.\.venv\Scripts\python.exe run_mcp_regression.py
```

Запустить только одну категорию live regression pack:

```powershell
.\.venv\Scripts\python.exe run_mcp_regression.py --tool diagnostics --fail-fast
```

Проверить параметры фонового индексатора без запуска индексации:

```powershell
.\.venv\Scripts\python.exe run_registered_index_background.py --config-id do_cf --graph-only --dry-run
```

Запустить фоновую graph-only переиндексацию с логами:

```powershell
.\.venv\Scripts\python.exe run_registered_index_background.py --config-id do_cf --graph-only --fastembed-threads 4
```

Запустить MCP-сервер вручную:

```powershell
.\.venv\Scripts\python.exe mcp_server.py
```

Проверить контейнер Memgraph:

```powershell
docker compose -f docker-compose.memgraph.yml ps
```

В `docker-compose.memgraph.yml` есть healthcheck через `mgconsole`; в статусе контейнера Docker должен появляться `healthy` после старта Memgraph.

Проверить порт Memgraph:

```powershell
Test-NetConnection localhost -Port 7687
```

## Backup И Restore Docker Volumes

Backup Qdrant и Memgraph volumes:

```powershell
.\scripts\backup_docker_volume.ps1
```

Backup одного volume в отдельный каталог:

```powershell
.\scripts\backup_docker_volume.ps1 -VolumeName 1c-vectorspace-mcp_memgraph_data -BackupDir .tmp\volume-backups
```

Проверить restore-команду без записи в volume:

```powershell
.\scripts\restore_docker_volume.ps1 `
  -VolumeName 1c-vectorspace-mcp_memgraph_data `
  -BackupFile .tmp\volume-backups\1c-vectorspace-mcp_memgraph_data_YYYYMMDD_HHMMSS.tar.gz `
  -DryRun
```

Перед реальным restore остановите сервис, который пишет в volume. Для Memgraph:

```powershell
docker compose -f docker-compose.memgraph.yml stop memgraph
.\scripts\restore_docker_volume.ps1 `
  -VolumeName 1c-vectorspace-mcp_memgraph_data `
  -BackupFile .tmp\volume-backups\1c-vectorspace-mcp_memgraph_data_YYYYMMDD_HHMMSS.tar.gz `
  -Force
docker compose -f docker-compose.memgraph.yml up -d memgraph
```

## Диагностика

Если MCP не видит Qdrant:

- проверить `QDRANT_URL`;
- открыть `http://localhost:6333`;
- проверить имя коллекции в `index_status`.

Если MCP не видит Memgraph:

- проверить `MEMGRAPH_URI`;
- выполнить `docker compose -f docker-compose.memgraph.yml ps`;
- проверить `Test-NetConnection localhost -Port 7687`;
- выполнить `smoke_test_graph_backends.py`.

Если графовые инструменты отвечают медленно:

- убедиться, что `GRAPH_BACKEND=memgraph`;
- проверить, что graph projection записан в Memgraph;
- проверить, что индексы Memgraph созданы через smoke или полный graph write.

Если не работает `get_file_snippet`:

- проверить, что файл существует именно на машине, где запущен `mcp_server.py`;
- проверить, совпадают ли пути в payload индекса с реальными путями сервера.

Если используется расширение 1С:

- индексируйте основную конфигурацию и расширение отдельно;
- переключайте MCP runtime на нужный `config_id` перед поиском;
- учитывайте, что текущая версия MCP не строит автоматические cross-config graph edges между `cf` и `cfe`, поэтому навигация идет в пределах активной конфигурации.

## Текущий Статус Проекта

На текущем этапе проект уже работает как гибридный MCP:

- Qdrant используется для семантического поиска.
- Memgraph используется для графовой навигации.
- Полный graph projection по текущей выгрузке успешно записан в Memgraph.
- MCP runtime настроен на `GRAPH_BACKEND=memgraph`.

Ближайшие практические улучшения:

- подготовить production compose для Qdrant + Memgraph вместе;
- расширять regression tests для MCP-инструментов;
- перейти к более точному AST-анализу BSL вместо regex/heuristic call graph.

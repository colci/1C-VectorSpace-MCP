import os
import re
import json
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from tqdm import tqdm
import psutil
import time
import gc
from config_runtime import (
    INDEX_SCHEMA_CACHE_KEY,
    INDEX_SCHEMA_VERSION,
    resolve_embedding_provider,
    resolve_runtime_config,
    sync_runtime_env,
)
from graph_writers import build_graph_writers
from metadata_parsers import parse_event_subscription_xml

# Отключаем прокси для локальных запросов к Qdrant, чтобы избежать ошибки 503 на Windows
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

from qdrant_client import QdrantClient
from qdrant_client.http import models

# Загрузка переменных окружения
load_dotenv()

# Настройки
# Эмбеддинги и настройки модели
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-large")
EMBEDDING_PROVIDER = resolve_embedding_provider(os.environ)
USE_OPENAI_EMBEDDINGS = EMBEDDING_PROVIDER == "openai"
OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
OPENAI_VECTOR_SIZE = int(os.getenv(
    "OPENAI_VECTOR_SIZE",
    "3072" if OPENAI_EMBEDDING_MODEL == "text-embedding-3-large" else "1536",
))
EMBEDDING_MODEL_PATH = os.getenv("EMBEDDING_MODEL_PATH", "").strip()
FASTEMBED_CACHE_DIR = os.getenv("FASTEMBED_CACHE_DIR", "").strip()
EMBEDDING_LOCAL_ONLY = os.getenv("EMBEDDING_LOCAL_ONLY", "").strip().lower() in {"1", "true", "yes", "on"}
MAX_RAM_PERCENT = float(os.getenv("MAX_RAM_PERCENT", "70"))

RUNTIME_CONFIG = resolve_runtime_config()
sync_runtime_env(RUNTIME_CONFIG)

EXPORT_PATH = RUNTIME_CONFIG.export_path
QDRANT_URL = RUNTIME_CONFIG.qdrant_url
QDRANT_TIMEOUT_SECONDS = int(os.getenv("QDRANT_TIMEOUT_SECONDS", "300"))
CONFIG_NAME = RUNTIME_CONFIG.config_name
CONFIG_ID = RUNTIME_CONFIG.config_id
CONFIG_PROFILE = RUNTIME_CONFIG.config_profile
PLATFORM_VERSION = RUNTIME_CONFIG.platform_version
COLLECTION_NAME = RUNTIME_CONFIG.collection_name
CACHE_FILE = Path(RUNTIME_CONFIG.cache_file)
GRAPH_CACHE_FILE = Path(RUNTIME_CONFIG.graph_cache_file)
CONFIG_KIND = RUNTIME_CONFIG.config_kind
BASE_CONFIG_ID = RUNTIME_CONFIG.base_config_id
GRAPH_WRITE_TARGETS = [
    item.strip().lower()
    for item in os.getenv("GRAPH_WRITE_TARGETS", "json").split(",")
    if item.strip()
]
MEMGRAPH_URI = os.getenv("MEMGRAPH_URI", "bolt://localhost:7687")
MEMGRAPH_USER = os.getenv("MEMGRAPH_USER", "")
MEMGRAPH_PASSWORD = os.getenv("MEMGRAPH_PASSWORD", "")
MEMGRAPH_DATABASE = os.getenv("MEMGRAPH_DATABASE", "")
MEMGRAPH_BATCH_SIZE = int(os.getenv("MEMGRAPH_BATCH_SIZE", "1000"))
MEMGRAPH_RETRY_ATTEMPTS = int(os.getenv("MEMGRAPH_RETRY_ATTEMPTS", "3"))
MEMGRAPH_RETRY_BACKOFF_SECONDS = float(os.getenv("MEMGRAPH_RETRY_BACKOFF_SECONDS", "2"))
RECREATE_ON_INDEX_SCHEMA_CHANGE = os.getenv(
    "RECREATE_ON_INDEX_SCHEMA_CHANGE",
    "true",
).strip().lower() in {"1", "true", "yes", "on"}

def check_memory_limit(max_percent=70.0, pbar=None):
    """
    Проверяет загрузку оперативной памяти. Если она превышает лимит,
    вызывает gc.collect() и ждет освобождения памяти.
    """
    def log_msg(msg):
        if pbar:
            pbar.write(msg)
        else:
            print(msg)
            
    wait_time = 0
    max_wait = 30  # Ждем до 30 секунд падения ниже лимита
    while True:
        mem = psutil.virtual_memory()
        if mem.percent > max_percent:
            if mem.percent > 85.0:
                # Критическая перегрузка — спим безусловно
                log_msg(f"\n[КРИТИЧЕСКАЯ НАГРУЗКА] RAM: {mem.percent}% (лимит {max_percent}%). Пауза для высвобождения ресурсов...")
                gc.collect()
                time.sleep(10)
                continue
            
            if wait_time < max_wait:
                log_msg(f"\n[ВНИМАНИЕ] Загрузка RAM ({mem.percent}%) выше лимита {max_percent}%. Ожидание 5 секунд...")
                gc.collect()
                time.sleep(5)
                wait_time += 5
            else:
                log_msg(f"\n[ПРЕДУПРЕЖДЕНИЕ] Память не снизилась ниже {max_percent}% после ожидания. Продолжаем работу с осторожностью.")
                break
        else:
            break


print("Инициализация клиента Qdrant...")
qclient = QdrantClient(url=QDRANT_URL, timeout=QDRANT_TIMEOUT_SECONDS)
print(
    f"Конфигурация индексации: "
    f"config_name='{CONFIG_NAME}', config_id='{CONFIG_ID}', profile='{CONFIG_PROFILE}'"
)
print(f"Embedding provider: {EMBEDDING_PROVIDER}")
if CONFIG_KIND != "configuration" or BASE_CONFIG_ID:
    print(
        f"Config topology: kind='{CONFIG_KIND}', "
        f"base_config_id='{BASE_CONFIG_ID or 'none'}'"
    )
graph_writers = build_graph_writers(
    targets=GRAPH_WRITE_TARGETS,
    graph_file=GRAPH_CACHE_FILE,
    memgraph_uri=MEMGRAPH_URI,
    memgraph_username=MEMGRAPH_USER,
    memgraph_password=MEMGRAPH_PASSWORD,
    memgraph_database=MEMGRAPH_DATABASE,
    memgraph_batch_size=MEMGRAPH_BATCH_SIZE,
    memgraph_retry_attempts=MEMGRAPH_RETRY_ATTEMPTS,
    memgraph_retry_backoff_seconds=MEMGRAPH_RETRY_BACKOFF_SECONDS,
)
print(
    "Graph targets: "
    + (", ".join(writer.target_name for writer in graph_writers) if graph_writers else "none")
)

encoder = None
openai_client = None
vector_size = None


def ensure_index_runtime():
    global encoder, openai_client, vector_size
    if vector_size is not None:
        return

    if USE_OPENAI_EMBEDDINGS:
        if not OPENAI_API_KEY:
            raise RuntimeError(
                "EMBEDDING_PROVIDER=openai требует OPENAI_API_KEY. "
                "Для локальной модели установите EMBEDDING_PROVIDER=local."
            )
        from openai import OpenAI

        openai_client = OpenAI(api_key=OPENAI_API_KEY)
        vector_size = OPENAI_VECTOR_SIZE
        print(f"Используются эмбеддинги OpenAI: {OPENAI_EMBEDDING_MODEL} (размерность {vector_size})")
        return

    from fastembed import TextEmbedding

    fastembed_threads_env = os.getenv("FASTEMBED_THREADS")
    if fastembed_threads_env is not None:
        fastembed_threads = int(fastembed_threads_env)
    else:
        import multiprocessing
        try:
            # Используем половину логических ядер, но не более 2 по умолчанию (для безопасности)
            fastembed_threads = min(2, max(1, multiprocessing.cpu_count() // 2))
        except Exception:
            fastembed_threads = 2

    model_kwargs = {}
    if FASTEMBED_CACHE_DIR:
        model_kwargs["cache_dir"] = FASTEMBED_CACHE_DIR
    if EMBEDDING_MODEL_PATH:
        model_path = Path(EMBEDDING_MODEL_PATH).resolve()
        if not model_path.exists():
            raise RuntimeError(f"EMBEDDING_MODEL_PATH не найден: `{model_path}`.")
        model_kwargs["specific_model_path"] = str(model_path)
    elif EMBEDDING_LOCAL_ONLY:
        raise RuntimeError(
            "EMBEDDING_LOCAL_ONLY=true требует EMBEDDING_MODEL_PATH к локальной модели FastEmbed."
        )

    print(f"Загрузка локальной модели FastEmbed: {EMBEDDING_MODEL} с лимитом потоков CPU: {fastembed_threads}...")
    encoder = TextEmbedding(model_name=EMBEDDING_MODEL, threads=fastembed_threads, **model_kwargs)
    vector_size = len(next(encoder.embed(["dummy"])))
    print(f"Модель FastEmbed загружена. Размерность векторов: {vector_size}")

# Создание коллекции и индексов
def setup_collection():
    ensure_index_runtime()
    collections = qclient.get_collections().collections
    exists = any(c.name == COLLECTION_NAME for c in collections)
    
    recreate = False
    if exists:
        try:
            collection_info = qclient.get_collection(COLLECTION_NAME)
            vectors_config = collection_info.config.params.vectors
            current_size = getattr(vectors_config, "size", None)
            if current_size is not None and current_size != vector_size:
                print(f"ВНИМАНИЕ: Размерность существующей коллекции ({current_size}) не совпадает с размерностью модели ({vector_size}).")
                print("Пересоздаем коллекцию...")
                recreate = True
        except Exception as e:
            print(f"Не удалось проверить размерность коллекции: {e}")
            
    if recreate:
        qclient.delete_collection(COLLECTION_NAME)
        exists = False

    if not exists:
        print(f"Коллекция '{COLLECTION_NAME}' не найдена. Создаем...")
        qclient.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=models.VectorParams(
                size=vector_size,
                distance=models.Distance.COSINE
            )
        )
        # Индексы для быстрой фильтрации по метаданным
        qclient.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name="chunk_type",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
        qclient.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name="file_path",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
        qclient.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name="config_id",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
        qclient.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name="form_name",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
        qclient.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name="owner_name",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )

# Генерация эмбеддингов
def get_embedding(text):
    ensure_index_runtime()
    if USE_OPENAI_EMBEDDINGS:
        response = openai_client.embeddings.create(
            input=[text],
            model=OPENAI_EMBEDDING_MODEL
        )
        return response.data[0].embedding
    else:
        return list(encoder.embed([text]))[0].tolist()

# Кэширование состояния файлов
def read_cache_data() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        return cache if isinstance(cache, dict) else {}
    except Exception:
        return {}


def cache_requires_schema_rebuild() -> bool:
    if not CACHE_FILE.exists():
        return False
    return read_cache_data().get(INDEX_SCHEMA_CACHE_KEY) != INDEX_SCHEMA_VERSION


def load_cache():
    cache = read_cache_data()
    if cache and cache.get(INDEX_SCHEMA_CACHE_KEY) != INDEX_SCHEMA_VERSION:
        print(
            f"Схема индекса изменилась до версии {INDEX_SCHEMA_VERSION}. "
            "Файлы будут переиндексированы."
        )
        return {}
    return cache

def save_cache(cache, pbar=None):
    try:
        cache[INDEX_SCHEMA_CACHE_KEY] = INDEX_SCHEMA_VERSION
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        msg = f"Ошибка записи кэша: {e}"
        if pbar:
            pbar.write(msg)
        else:
            print(msg)

# Парсинг BSL программного кода
METHOD_START_RE = re.compile(
    r'^\s*(?:Процедура|Procedure|Функция|Function)\s+([a-zA-Zа-яА-Я0-9_]+)\s*\(',
    re.IGNORECASE
)
METHOD_END_RE = re.compile(
    r'^\s*(?:КонецПроцедуры|EndProcedure|КонецФункции|EndFunction)',
    re.IGNORECASE
)
EXTENSION_METHOD_ANNOTATION_RE = re.compile(
    r'^\s*&(?P<kind>Перед|После|Вместо|ИзменениеИКонтроль|Before|After|Instead|ChangeAndValidate)'
    r'\s*\(\s*["\'](?P<target>[^"\']+)["\']\s*\)',
    re.IGNORECASE,
)
EXTENSION_ANNOTATION_KIND_MAP = {
    "перед": "before",
    "before": "before",
    "после": "after",
    "after": "after",
    "вместо": "instead",
    "instead": "instead",
    "изменениеиконтроль": "change_and_validate",
    "changeandvalidate": "change_and_validate",
}


def parse_extension_method_annotation(line: str) -> dict | None:
    match = EXTENSION_METHOD_ANNOTATION_RE.match(line)
    if not match:
        return None
    raw_kind = match.group("kind")
    normalized_kind = EXTENSION_ANNOTATION_KIND_MAP.get(raw_kind.lower(), raw_kind.lower())
    return {
        "kind": normalized_kind,
        "target_method": match.group("target").strip(),
        "raw": line.strip(),
    }

def parse_bsl_file(filepath: Path):
    chunks = []
    current_method = None
    method_name = ""
    method_lines = []
    start_line = 0
    comments_buffer = []
    annotation_lines = []
    pending_extension_annotation = None

    try:
        with open(filepath, 'r', encoding='utf-8-sig', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Ошибка чтения файла BSL {filepath}: {e}")
        return []

    for idx, line in enumerate(lines):
        line_num = idx + 1
        stripped = line.strip()

        if stripped.startswith("//") and not current_method:
            comments_buffer.append(stripped)
        elif stripped == "" and not current_method:
            comments_buffer = []

        if not current_method and stripped.startswith("&"):
            annotation_lines.append(line)
            extension_annotation = parse_extension_method_annotation(line)
            if extension_annotation:
                pending_extension_annotation = extension_annotation
            continue

        match = METHOD_START_RE.match(line)
        if match and not current_method:
            method_name = match.group(1)
            current_method = "METHOD"
            start_line = line_num
            method_lines = list(comments_buffer) + list(annotation_lines) + [line]
            comments_buffer = []
            annotation_lines = []
            continue

        if current_method:
            method_lines.append(line)
            if METHOD_END_RE.match(line):
                end_line = line_num
                body = "".join(method_lines)
                chunks.append({
                    "method_name": method_name,
                    "body": body,
                    "start_line": start_line,
                    "end_line": end_line,
                    "extension_annotation": (pending_extension_annotation or {}).get("kind", ""),
                    "extension_target_method": (pending_extension_annotation or {}).get("target_method", ""),
                    "extension_annotation_raw": (pending_extension_annotation or {}).get("raw", ""),
                })
                current_method = None
                method_lines = []
                comments_buffer = []
                annotation_lines = []
                pending_extension_annotation = None
        
        if not stripped.startswith("//") and not current_method:
            comments_buffer = []
            if not stripped.startswith("&"):
                annotation_lines = []
                pending_extension_annotation = None

    return chunks


def build_module_summary(module_path: str, module_type: str, methods: list[dict]) -> str:
    method_names = [method.get("method_name", "") for method in methods if method.get("method_name")]
    visible_names = method_names[:200]
    lines = [
        f"Модуль: {module_path}",
        f"Тип модуля: {module_type}",
        f"Количество методов: {len(method_names)}",
    ]
    if visible_names:
        lines.append("Методы:")
        lines.extend(f"- {name}" for name in visible_names)
    if len(method_names) > len(visible_names):
        lines.append(f"- ... еще {len(method_names) - len(visible_names)} методов")
    return "\n".join(lines)


def build_form_command_card(form_info: dict, command: dict) -> str:
    owner_label = (
        f"{form_info.get('owner_object_type')}.{form_info.get('owner_name')}"
        if form_info.get("owner_name")
        else form_info.get("owner_type", "unknown")
    )
    return "\n".join([
        "Тип сущности: FormCommand",
        f"Владелец: {owner_label}",
        f"Форма: {form_info.get('form_name', '')}",
        f"Команда: {command.get('name', '')}",
        f"Заголовок: {command.get('title', '')}",
        f"Подсказка: {command.get('tooltip', '')}",
        f"Обработчик: {command.get('action', '')}",
    ])


REFERENCE_OBJECT_TYPES = {
    "CatalogRef": "Catalog",
    "DocumentRef": "Document",
    "EnumRef": "Enum",
    "AccumulationRegisterRef": "AccumulationRegister",
    "InformationRegisterRef": "InformationRegister",
    "ChartOfAccountsRef": "ChartOfAccounts",
    "ChartOfCharacteristicTypesRef": "ChartOfCharacteristicTypes",
    "BusinessProcessRef": "BusinessProcess",
    "TaskRef": "Task",
    "ConstantRef": "Constant",
}

REFERENCE_TYPE_RE = re.compile(r"([A-Za-z]+Ref)\.([A-Za-zА-Яа-яЁё0-9_]+)")

FOLDER_TO_OBJECT_TYPE = {
    "Catalogs": "Catalog",
    "Documents": "Document",
    "Enums": "Enum",
    "InformationRegisters": "InformationRegister",
    "AccumulationRegisters": "AccumulationRegister",
    "ChartsOfAccounts": "ChartOfAccounts",
    "ChartsOfCharacteristicTypes": "ChartOfCharacteristicTypes",
    "CommonForms": "CommonForm",
    "CommonModules": "CommonModule",
    "Reports": "Report",
    "DataProcessors": "DataProcessor",
}

METHOD_CALL_KEYWORDS = {
    "процедура",
    "procedure",
    "функция",
    "function",
    "если",
    "иначеесли",
    "иначе",
    "для",
    "каждого",
    "пока",
    "попытка",
    "исключение",
    "возврат",
    "новый",
    "выполнить",
    "экспорт",
    "знач",
    "тогда",
    "конецесли",
    "конеццикла",
    "конецпопытки",
    "конецпроцедуры",
    "конецфункции",
}

QUALIFIED_CALL_RE = re.compile(
    r"\b([A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*)\.([A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*)\s*\(",
    re.IGNORECASE,
)
SIMPLE_CALL_RE = re.compile(
    r"(?<!\.)\b([A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*)\s*\(",
    re.IGNORECASE,
)
METADATA_NAMESPACE_TO_OBJECT_TYPE = {
    "Справочники": "Catalog",
    "Catalogs": "Catalog",
    "Документы": "Document",
    "Documents": "Document",
    "Перечисления": "Enum",
    "Enums": "Enum",
    "РегистрыСведений": "InformationRegister",
    "InformationRegisters": "InformationRegister",
    "РегистрыНакопления": "AccumulationRegister",
    "AccumulationRegisters": "AccumulationRegister",
    "ПланыСчетов": "ChartOfAccounts",
    "ChartsOfAccounts": "ChartOfAccounts",
    "ПланыВидовХарактеристик": "ChartOfCharacteristicTypes",
    "ChartsOfCharacteristicTypes": "ChartOfCharacteristicTypes",
    "БизнесПроцессы": "BusinessProcess",
    "BusinessProcesses": "BusinessProcess",
    "Задачи": "Task",
    "Tasks": "Task",
    "Константы": "Constant",
    "Constants": "Constant",
}
METADATA_USAGE_RE = re.compile(
    r"\b("
    + "|".join(re.escape(key) for key in sorted(METADATA_NAMESPACE_TO_OBJECT_TYPE, key=len, reverse=True))
    + r")\.([A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*)\b"
)


def resolve_reference_object_type(raw_type: str) -> str:
    return REFERENCE_OBJECT_TYPES.get(raw_type, raw_type.removesuffix("Ref"))


def extract_type_references(
    type_names: list[str],
    section: str,
    source_name: str,
    container: str = "",
) -> list[dict]:
    references: list[dict] = []
    for type_name in type_names:
        for raw_type, target_name in REFERENCE_TYPE_RE.findall(type_name or ""):
            references.append({
                "section": section,
                "container": container,
                "source": source_name,
                "target_type": resolve_reference_object_type(raw_type),
                "target_name": target_name,
                "raw_type": raw_type,
            })
    return references


def strip_bsl_comments_and_strings(body: str) -> str:
    cleaned_lines: list[str] = []
    for raw_line in body.splitlines():
        code_part = raw_line.split("//", 1)[0]
        code_part = re.sub(r'"(?:[^"]|"")*"', '""', code_part)
        cleaned_lines.append(code_part)
    return "\n".join(cleaned_lines)


def extract_call_candidates(body: str) -> list[dict]:
    cleaned = strip_bsl_comments_and_strings(body)
    calls: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for module_name, method_name in QUALIFIED_CALL_RE.findall(cleaned):
        if module_name.lower() in METHOD_CALL_KEYWORDS or method_name.lower() in METHOD_CALL_KEYWORDS:
            continue
        key = ("qualified", module_name, method_name)
        if key in seen:
            continue
        seen.add(key)
        calls.append({
            "kind": "qualified",
            "module_name": module_name,
            "method_name": method_name,
        })

    for method_name in SIMPLE_CALL_RE.findall(cleaned):
        if method_name.lower() in METHOD_CALL_KEYWORDS:
            continue
        key = ("local", "", method_name)
        if key in seen:
            continue
        seen.add(key)
        calls.append({
            "kind": "local",
            "module_name": "",
            "method_name": method_name,
        })

    return calls


def extract_metadata_usages_from_bsl(body: str) -> list[dict]:
    cleaned = strip_bsl_comments_and_strings(body)
    usages: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for namespace, object_name in METADATA_USAGE_RE.findall(cleaned):
        object_type = METADATA_NAMESPACE_TO_OBJECT_TYPE.get(namespace)
        if not object_type:
            continue
        key = (namespace, object_type, object_name)
        if key in seen:
            continue
        seen.add(key)
        usages.append({
            "namespace": namespace,
            "object_type": object_type,
            "object_name": object_name,
            "source_token": f"{namespace}.{object_name}",
        })

    return usages

# Парсинг XML метаданных 1С
def parse_metadata_xml(filepath: Path):
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except Exception as e:
        print(f"Ошибка парсинга XML {filepath}: {e}")
        return None

    ns = '{http://v8.1c.ru/8.3/MDClasses}'
    v8_ns = '{http://v8.1c.ru/8.1/data/core}'
    
    child = None
    for item in root:
        if not item.tag.endswith('MetaDataObject'):
            child = item
            break
    
    if child is None:
        return None

    obj_type = child.tag.split('}')[-1]
    properties = child.find(f'{ns}Properties')
    if properties is None:
        return None

    name = properties.find(f'{ns}Name')
    name_val = name.text if name is not None else ""
    
    synonym_val = ""
    synonym = properties.find(f'{ns}Synonym')
    if synonym is not None:
        content = synonym.find(f'.//{v8_ns}content')
        if content is not None:
            synonym_val = content.text

    comment_val = ""
    comment = properties.find(f'{ns}Comment')
    if comment is not None:
        comment_val = comment.text if comment.text else ""

    references = []

    lines = [
        f"Тип метаданных: {obj_type}",
        f"Имя объекта: {name_val}",
        f"Синоним: {synonym_val}"
    ]
    if comment_val:
        lines.append(f"Описание: {comment_val}")

    child_objects = child.find(f'{ns}ChildObjects')
    if child_objects is not None:
        # 1. Сбор стандартных реквизитов объекта
        attrs = []
        for attr in child_objects.findall(f'{ns}Attribute'):
            attr_props = attr.find(f'{ns}Properties')
            if attr_props is not None:
                attr_name = attr_props.find(f'{ns}Name').text
                attr_synonym = ""
                syn = attr_props.find(f'{ns}Synonym')
                if syn is not None:
                    c = syn.find(f'.//{v8_ns}content')
                    if c is not None:
                        attr_synonym = c.text
                
                attr_type_val = ""
                t_el = attr_props.find(f'{ns}Type')
                if t_el is not None:
                    types = [t.text for t in t_el.findall(f'.//{v8_ns}Type') if t.text]
                    attr_type_val = ", ".join(types)
                    references.extend(extract_type_references(types, "attributes", attr_name))
                
                attrs.append(f"  - {attr_name} ({attr_type_val}): {attr_synonym}")
        if attrs:
            lines.append("Реквизиты:")
            lines.extend(attrs)

        # 2. Сбор табличных частей и их реквизитов
        for ts in child_objects.findall(f'{ns}TabularSection'):
            ts_props = ts.find(f'{ns}Properties')
            if ts_props is not None:
                ts_name = ts_props.find(f'{ns}Name').text
                ts_synonym = ""
                syn = ts_props.find(f'{ns}Synonym')
                if syn is not None:
                    c = syn.find(f'.//{v8_ns}content')
                    if c is not None:
                        ts_synonym = c.text
                lines.append(f"Табличная часть: {ts_name} ({ts_synonym})")
                
                ts_attrs = []
                ts_childs = ts.find(f'{ns}ChildObjects')
                if ts_childs is not None:
                    for ts_attr in ts_childs.findall(f'{ns}Attribute'):
                        ts_attr_props = ts_attr.find(f'{ns}Properties')
                        if ts_attr_props is not None:
                            ta_name = ts_attr_props.find(f'{ns}Name').text
                            ta_synonym = ""
                            syn = ts_attr_props.find(f'{ns}Synonym')
                            if syn is not None:
                                c = syn.find(f'.//{v8_ns}content')
                                if c is not None:
                                    ta_synonym = c.text
                            
                            ta_type_val = ""
                            t_el = ts_attr_props.find(f'{ns}Type')
                            if t_el is not None:
                                types = [t.text for t in t_el.findall(f'.//{v8_ns}Type') if t.text]
                                ta_type_val = ", ".join(types)
                                references.extend(extract_type_references(types, "tabular_section", ta_name, ts_name))
                            ts_attrs.append(f"    * {ta_name} ({ta_type_val}): {ta_synonym}")
                if ts_attrs:
                    lines.extend(ts_attrs)

        # 3. Измерения для регистров
        dims = []
        for dim in child_objects.findall(f'{ns}Dimension'):
            dim_props = dim.find(f'{ns}Properties')
            if dim_props is not None:
                dim_name = dim_props.find(f'{ns}Name').text
                dim_synonym = ""
                syn = dim_props.find(f'{ns}Synonym')
                if syn is not None:
                    c = syn.find(f'.//{v8_ns}content')
                    if c is not None:
                        dim_synonym = c.text
                
                t_el = dim_props.find(f'{ns}Type')
                dim_type = ""
                if t_el is not None:
                    types = [t.text for t in t_el.findall(f'.//{v8_ns}Type') if t.text]
                    dim_type = ", ".join(types)
                    references.extend(extract_type_references(types, "dimensions", dim_name))
                dims.append(f"  - [Измерение] {dim_name} ({dim_type}): {dim_synonym}")
        if dims:
            lines.append("Измерения:")
            lines.extend(dims)

        # 4. Ресурсы для регистров
        res_list = []
        for res in child_objects.findall(f'{ns}Resource'):
            res_props = res.find(f'{ns}Properties')
            if res_props is not None:
                res_name = res_props.find(f'{ns}Name').text
                res_synonym = ""
                syn = res_props.find(f'{ns}Synonym')
                if syn is not None:
                    c = syn.find(f'.//{v8_ns}content')
                    if c is not None:
                        res_synonym = c.text
                
                t_el = res_props.find(f'{ns}Type')
                res_type = ""
                if t_el is not None:
                    types = [t.text for t in t_el.findall(f'.//{v8_ns}Type') if t.text]
                    res_type = ", ".join(types)
                    references.extend(extract_type_references(types, "resources", res_name))
                res_list.append(f"  - [Ресурс] {res_name} ({res_type}): {res_synonym}")
        if res_list:
            lines.append("Ресурсы:")
            lines.extend(res_list)

    card_text = "\n".join(lines)
    return {
        "object_type": obj_type,
        "object_name": name_val,
        "synonym": synonym_val,
        "card_text": card_text,
        "references": references,
    }


def xml_local_name(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def extract_v8_content(element: ET.Element | None) -> str:
    if element is None:
        return ""

    texts: list[str] = []
    for item in element.iter():
        text = (item.text or "").strip()
        if text:
            texts.append(text)

    return " ".join(texts).strip()


def dedupe_preserve_order(items: list[str]) -> list[str]:
    result: list[str] = []
    seen = set()
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


FORM_ELEMENT_TYPES = {
    "InputField",
    "LabelField",
    "Button",
    "UsualGroup",
    "Pages",
    "Page",
    "Table",
    "CheckBox",
    "CheckBoxField",
    "RadioButton",
    "ProgressBar",
    "Picture",
    "PictureField",
    "Calendar",
    "Chart",
    "SpreadSheetDocument",
    "TextDocument",
    "HTMLDocument",
    "CommandBar",
    "AutoCommandBar",
    "ContextMenu",
    "ExtendedTooltip",
    "SearchStringAddition",
    "ViewStatusAddition",
    "SearchControlAddition",
    "Label",
    "LabelDecoration",
    "GroupBox",
    "Popup",
    "CommandGroup",
    "Field",
    "Column",
}


def get_child_text(element: ET.Element, child_name: str) -> str:
    for child in element:
        if xml_local_name(child.tag) == child_name:
            return extract_v8_content(child)
    return ""


def get_child(element: ET.Element, child_name: str) -> ET.Element | None:
    for child in element:
        if xml_local_name(child.tag) == child_name:
            return child
    return None


def extract_localized_string(element: ET.Element | None) -> str:
    if element is None:
        return ""

    for item in element.iter():
        if xml_local_name(item.tag) == "content":
            text = (item.text or "").strip()
            if text:
                return text

    return (element.text or "").strip()


def parse_bool_text(value: str, default: bool) -> bool:
    normalized = (value or "").strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return default


def normalize_form_command_name(command_name: str) -> str:
    normalized = (command_name or "").strip()
    if normalized.startswith("Form.Command."):
        return normalized.rsplit(".", 1)[-1]
    return normalized


def extract_direct_events(element: ET.Element) -> list[dict]:
    events: list[dict] = []
    events_element = get_child(element, "Events")
    if events_element is None:
        return events

    for event in events_element:
        if xml_local_name(event.tag) != "Event":
            continue
        handler_name = extract_v8_content(event).strip()
        event_name = str(event.attrib.get("name") or "").strip()
        if handler_name:
            events.append({
                "event": event_name,
                "handler": handler_name,
            })
    return events


def extract_form_elements(root: ET.Element) -> list[dict]:
    elements: list[dict] = []

    def walk(element: ET.Element, parent_id: str = "", parent_name: str = "", depth: int = 0) -> None:
        local_name = xml_local_name(element.tag)
        element_name = str(element.attrib.get("name") or "").strip()
        element_id = str(element.attrib.get("id") or "").strip()

        current_id = parent_id
        current_name = parent_name
        current_depth = depth

        if local_name in FORM_ELEMENT_TYPES and (element_name or element_id):
            title = extract_localized_string(get_child(element, "Title"))
            data_path = get_child_text(element, "DataPath").strip()
            command_name = get_child_text(element, "CommandName").strip()
            record = {
                "element_id": element_id or f"auto_{len(elements) + 1}",
                "element_name": element_name or local_name,
                "element_type": local_name,
                "title": title,
                "data_path": data_path,
                "command_name": command_name,
                "command_ref": normalize_form_command_name(command_name),
                "visible": parse_bool_text(get_child_text(element, "Visible"), True),
                "enabled": parse_bool_text(get_child_text(element, "Enabled"), True),
                "read_only": parse_bool_text(get_child_text(element, "ReadOnly"), False),
                "parent_element_id": parent_id,
                "parent_element_name": parent_name,
                "depth": depth,
                "events": extract_direct_events(element),
            }
            elements.append(record)
            current_id = record["element_id"]
            current_name = record["element_name"]
            current_depth = depth + 1

        child_items = get_child(element, "ChildItems")
        if child_items is None:
            return
        for child in child_items:
            walk(child, current_id, current_name, current_depth)

    for child in root:
        local_name = xml_local_name(child.tag)
        if local_name == "AutoCommandBar":
            walk(child)
        elif local_name == "ChildItems":
            for item in child:
                walk(item)
    return elements


def resolve_form_identity(filepath: Path, export_dir: Path) -> dict:
    rel_parts = filepath.relative_to(export_dir).parts
    form_name = filepath.stem
    owner_type = ""
    owner_name = ""

    if "Forms" in rel_parts:
        forms_index = rel_parts.index("Forms")
        if forms_index >= 2:
            owner_type = rel_parts[forms_index - 2]
            owner_name = rel_parts[forms_index - 1]
        elif forms_index >= 1:
            owner_type = rel_parts[forms_index - 1]

        if len(rel_parts) > forms_index + 1:
            form_part = rel_parts[forms_index + 1]
            form_name = Path(form_part).stem if form_part.lower().endswith(".xml") else form_part
    elif len(rel_parts) >= 2 and rel_parts[0] == "CommonForms":
        owner_type = rel_parts[0]
        owner_name = rel_parts[1]
        form_name = rel_parts[1]
    elif len(rel_parts) >= 2:
        owner_type = rel_parts[0]
        owner_name = rel_parts[1]

    return {
        "form_name": form_name,
        "owner_type": owner_type,
        "owner_object_type": FOLDER_TO_OBJECT_TYPE.get(owner_type, owner_type),
        "owner_name": owner_name,
    }


def is_form_xml_file(filepath: Path, export_dir: Path) -> bool:
    if filepath.suffix.lower() != ".xml":
        return False

    try:
        rel_parts = filepath.relative_to(export_dir).parts
    except ValueError:
        return False

    if "Forms" in rel_parts:
        forms_index = rel_parts.index("Forms")
        if len(rel_parts) == forms_index + 2:
            return True
        return len(rel_parts) >= forms_index + 4 and rel_parts[-2:] == ("Ext", "Form.xml")

    return len(rel_parts) >= 4 and rel_parts[0] == "CommonForms" and rel_parts[-2:] == ("Ext", "Form.xml")


def extract_form_commands(root: ET.Element) -> list[dict]:
    commands: list[dict] = []
    for element in root.iter():
        if xml_local_name(element.tag) != "Command":
            continue

        command_name = str(element.attrib.get("name") or get_child_text(element, "Name") or "").strip()
        action = get_child_text(element, "Action").strip()
        title = get_child_text(element, "Title").strip()
        tooltip = get_child_text(element, "ToolTip").strip()
        if not command_name and not action:
            continue

        commands.append({
            "name": command_name or action,
            "action": action,
            "title": title,
            "tooltip": tooltip,
        })

    return commands


def extract_form_event_handlers(root: ET.Element) -> list[dict]:
    handlers: list[dict] = []

    def walk(element: ET.Element, source: dict | None = None) -> None:
        local_name = xml_local_name(element.tag)
        current_source = source
        element_name = str(element.attrib.get("name") or "").strip()
        if element_name and local_name not in {"Event", "Events"}:
            current_source = {"kind": local_name, "name": element_name}

        if local_name == "Event":
            handler_name = extract_v8_content(element).strip()
            event_name = str(element.attrib.get("name") or "").strip()
            if handler_name:
                handlers.append({
                    "event": event_name,
                    "handler": handler_name,
                    "source_kind": (current_source or {}).get("kind", ""),
                    "source_name": (current_source or {}).get("name", ""),
                })

        for child in element:
            walk(child, current_source)

    walk(root)
    return handlers


def parse_form_xml(filepath: Path, export_dir: Path):
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except Exception as e:
        print(f"Ошибка парсинга XML формы {filepath}: {e}")
        return None

    identity = resolve_form_identity(filepath, export_dir)
    form_name = identity["form_name"]
    owner_type = identity["owner_type"]
    owner_name = identity["owner_name"]
    owner_object_type = identity["owner_object_type"]
    root_type = xml_local_name(root.tag)
    title_candidates: list[str] = []
    command_names: list[str] = []
    attribute_names: list[str] = []
    item_names: list[str] = []
    data_paths: list[str] = []
    commands = extract_form_commands(root)
    event_handlers = extract_form_event_handlers(root)
    form_elements = extract_form_elements(root) if root_type == "Form" else []

    for element in root.iter():
        local_name = xml_local_name(element.tag)
        text = extract_v8_content(element)
        if not text:
            continue

        if local_name in {"Title", "Caption", "ToolTip"}:
            title_candidates.append(text)
        elif local_name == "CommandName":
            command_names.append(text)
        elif local_name == "DataPath":
            data_paths.append(text)
        elif local_name == "Attribute":
            attribute_names.append(text)
        elif local_name in {"Item", "Group", "Button", "Table"}:
            item_names.append(text)

    title_candidates = dedupe_preserve_order(title_candidates)[:10]
    command_names = dedupe_preserve_order(command_names)[:20]
    attribute_names = dedupe_preserve_order(attribute_names)[:20]
    item_names = dedupe_preserve_order(item_names)[:20]
    data_paths = dedupe_preserve_order(data_paths)[:20]

    owner_label = f"{owner_type}.{owner_name}" if owner_name else (owner_type or "unknown")
    lines = [
        f"Тип сущности: Form",
        f"Тип XML формы: {root_type}",
        f"Владелец: {owner_label}",
        f"Имя формы: {form_name}",
    ]

    if title_candidates:
        lines.append("Заголовки:")
        lines.extend(f"  - {item}" for item in title_candidates)

    if command_names:
        lines.append("Команды:")
        lines.extend(f"  - {item}" for item in command_names)

    if commands:
        lines.append("Commands:")
        lines.extend(
            f"  - {item['name']} -> {item.get('action', '') or 'no action'}"
            for item in commands[:20]
        )

    if event_handlers:
        lines.append("Event handlers:")
        lines.extend(
            f"  - {item['source_name'] or item['source_kind']}.{item['event']} -> {item['handler']}"
            for item in event_handlers[:20]
        )

    if attribute_names:
        lines.append("Реквизиты формы:")
        lines.extend(f"  - {item}" for item in attribute_names)

    if item_names:
        lines.append("Элементы формы:")
        lines.extend(f"  - {item}" for item in item_names)

    if data_paths:
        lines.append("Пути данных:")
        lines.extend(f"  - {item}" for item in data_paths)

    if form_elements:
        lines.append("UI elements:")
        lines.extend(
            f"  - {item['element_type']} {item['element_name']} -> "
            f"{item.get('data_path') or item.get('command_name') or 'no binding'}"
            for item in form_elements[:30]
        )

    return {
        "form_name": form_name,
        "owner_type": owner_type,
        "owner_object_type": owner_object_type,
        "owner_name": owner_name,
        "root_type": root_type,
        "card_text": "\n".join(lines),
        "data_paths": data_paths,
        "commands": commands,
        "event_handlers": event_handlers,
        "form_elements": form_elements,
    }


def build_module_identity(filepath: Path, export_dir: Path) -> tuple[str, str, str]:
    parts = filepath.relative_to(export_dir).parts
    module_type = parts[0] if parts else ""
    module_name = parts[1] if len(parts) >= 2 else filepath.stem

    if "Forms" in parts:
        forms_index = parts.index("Forms")
        if len(parts) > forms_index + 1 and parts[-3:] == ("Ext", "Form", "Module.bsl"):
            form_name = parts[forms_index + 1]
            module_path = f"{module_type}.{module_name}.Forms.{form_name}.FormModule"
            return module_type, module_name, module_path

    if len(parts) >= 5 and parts[0] == "CommonForms" and parts[-3:] == ("Ext", "Form", "Module.bsl"):
        form_name = parts[1]
        module_path = f"{module_type}.{form_name}.FormModule"
        return module_type, module_name, module_path

    if "ObjectModule.bsl" in filepath.name:
        module_path = f"{module_type}.{module_name}.МодульОбъекта"
    elif "ManagerModule.bsl" in filepath.name:
        module_path = f"{module_type}.{module_name}.МодульМенеджера"
    else:
        module_path = f"{module_type}.{module_name}"

    return module_type, module_name, module_path


def make_metadata_node_id(object_type: str, object_name: str) -> str:
    return f"metadata:{object_type}.{object_name}"


def make_form_node_id(owner_object_type: str, owner_name: str, form_name: str) -> str:
    owner_label = f"{owner_object_type}.{owner_name}" if owner_name else owner_object_type
    return f"form:{owner_label}.{form_name}"


def make_command_node_id(form_id: str, command_name: str) -> str:
    return f"command:{form_id}.{command_name}"


def make_handler_node_id(form_id: str, handler_name: str) -> str:
    return f"handler:{form_id}.{handler_name}"


def make_form_element_node_id(form_id: str, element_id: str, element_name: str) -> str:
    suffix = element_id or element_name
    return f"form_element:{form_id}.{suffix}"


def make_module_node_id(module_path: str) -> str:
    return f"module:{module_path}"


def make_method_node_id(module_path: str, method_name: str, start_line: int) -> str:
    return f"method:{module_path}.{method_name}:{start_line}"


def add_graph_node(nodes_by_id: dict[str, dict], node_id: str, kind: str, **attrs) -> dict:
    node = nodes_by_id.get(node_id)
    if node is None:
        node = {"id": node_id, "kind": kind}
        nodes_by_id[node_id] = node

    node["kind"] = kind
    for key, value in attrs.items():
        if value not in (None, "", [], {}):
            node[key] = value

    if any(attrs.get(key) for key in (
        "file_path",
        "document",
        "synonym",
        "root_type",
        "module_path",
        "method_name",
        "element_name",
        "command_name",
        "handler_name",
    )):
        node["is_stub"] = False
    else:
        node.setdefault("is_stub", True)

    return node


def add_graph_edge(edges: list[dict], edge_keys: set[tuple], from_id: str, to_id: str, edge_type: str, **attrs):
    edge_key = (
        from_id,
        to_id,
        edge_type,
        attrs.get("section", ""),
        attrs.get("container", ""),
        attrs.get("source", ""),
        attrs.get("data_path", ""),
        attrs.get("event", ""),
    )
    if edge_key in edge_keys:
        return

    edge_keys.add(edge_key)
    edge = {"from": from_id, "to": to_id, "type": edge_type}
    for key, value in attrs.items():
        if value not in (None, "", [], {}):
            edge[key] = value
    edges.append(edge)


def build_graph_projection(export_dir: Path, all_files: list[Path], index_filter: str | None = None) -> dict:
    nodes_by_id: dict[str, dict] = {}
    edges: list[dict] = []
    edge_keys: set[tuple] = set()
    method_records: list[dict] = []
    form_records: list[dict] = []

    for filepath in all_files:
        if filepath.suffix == ".xml":
            if is_form_xml_file(filepath, export_dir):
                form_info = parse_form_xml(filepath, export_dir)
                if not form_info:
                    continue

                owner_object_type = form_info["owner_object_type"]
                form_id = make_form_node_id(owner_object_type, form_info["owner_name"], form_info["form_name"])
                form_node = add_graph_node(
                    nodes_by_id,
                    form_id,
                    "form",
                    form_name=form_info["form_name"],
                    owner_type=form_info["owner_type"],
                    owner_object_type=owner_object_type,
                    owner_name=form_info["owner_name"],
                    root_type=form_info["root_type"],
                    data_paths=form_info["data_paths"],
                    form_element_count=len(form_info.get("form_elements", [])),
                    form_event_count=len(form_info.get("event_handlers", [])),
                    form_command_count=len(form_info.get("commands", [])),
                    file_path=str(filepath),
                    document=form_info["card_text"],
                )
                form_records.append({
                    "id": form_id,
                    "node": form_node,
                    "owner_type": form_info["owner_type"],
                    "owner_name": form_info["owner_name"],
                    "form_name": form_info["form_name"],
                    "commands": form_info.get("commands", []),
                    "event_handlers": form_info.get("event_handlers", []),
                    "form_elements": form_info.get("form_elements", []),
                })

                if owner_object_type and form_info["owner_name"]:
                    owner_id = make_metadata_node_id(owner_object_type, form_info["owner_name"])
                    add_graph_node(
                        nodes_by_id,
                        owner_id,
                        "metadata",
                        object_type=owner_object_type,
                        object_name=form_info["owner_name"],
                    )
                    add_graph_edge(edges, edge_keys, owner_id, form_id, "contains_form")
                continue

            if "Forms" in filepath.parts or filepath.name in {"Form.xml", "Help.xml"}:
                continue

            meta_info = parse_metadata_xml(filepath)
            if not meta_info:
                continue

            metadata_id = make_metadata_node_id(meta_info["object_type"], meta_info["object_name"])
            add_graph_node(
                nodes_by_id,
                metadata_id,
                "metadata",
                object_type=meta_info["object_type"],
                object_name=meta_info["object_name"],
                synonym=meta_info["synonym"],
                file_path=str(filepath),
                document=meta_info["card_text"],
            )

            for reference in meta_info.get("references", []):
                target_id = make_metadata_node_id(reference["target_type"], reference["target_name"])
                add_graph_node(
                    nodes_by_id,
                    target_id,
                    "metadata",
                    object_type=reference["target_type"],
                    object_name=reference["target_name"],
                )
                add_graph_edge(
                    edges,
                    edge_keys,
                    metadata_id,
                    target_id,
                    "references_metadata",
                    section=reference.get("section", ""),
                    container=reference.get("container", ""),
                    source=reference.get("source", ""),
                    raw_type=reference.get("raw_type", ""),
                )
            continue

        if filepath.suffix != ".bsl":
            continue

        module_type, module_name, module_path = build_module_identity(filepath, export_dir)
        module_id = make_module_node_id(module_path)
        add_graph_node(
            nodes_by_id,
            module_id,
            "module",
            module_type=module_type,
            module_name=module_name,
            module_path=module_path,
            file_path=str(filepath),
        )

        owner_object_type = FOLDER_TO_OBJECT_TYPE.get(module_type)
        if owner_object_type and module_name:
            owner_id = make_metadata_node_id(owner_object_type, module_name)
            add_graph_node(
                nodes_by_id,
                owner_id,
                "metadata",
                object_type=owner_object_type,
                object_name=module_name,
            )
            add_graph_edge(edges, edge_keys, owner_id, module_id, "contains_module")

        for method in parse_bsl_file(filepath):
            method_id = make_method_node_id(module_path, method["method_name"], method["start_line"])
            add_graph_node(
                nodes_by_id,
                method_id,
                "method",
                module_type=module_type,
                module_name=module_name,
                module_path=module_path,
                method_name=method["method_name"],
                start_line=method["start_line"],
                end_line=method["end_line"],
                extension_annotation=method.get("extension_annotation", ""),
                extension_target_method=method.get("extension_target_method", ""),
                extension_annotation_raw=method.get("extension_annotation_raw", ""),
                file_path=str(filepath),
                document=method["body"],
            )
            add_graph_edge(edges, edge_keys, module_id, method_id, "declares_method")
            method_records.append({
                "id": method_id,
                "module_id": module_id,
                "module_type": module_type,
                "module_name": module_name,
                "module_path": module_path,
                "method_name": method["method_name"],
                "start_line": method["start_line"],
                "extension_annotation": method.get("extension_annotation", ""),
                "extension_target_method": method.get("extension_target_method", ""),
                "body": method["body"],
            })

    modules_by_name: dict[str, list[dict]] = {}
    methods_by_module_and_name: dict[tuple[str, str], list[dict]] = {}
    methods_by_module_path: dict[str, list[dict]] = {}
    for node in nodes_by_id.values():
        kind = node.get("kind")
        if kind == "module":
            module_name = str(node.get("module_name") or "")
            if module_name:
                modules_by_name.setdefault(module_name, []).append(node)
        elif kind == "method":
            module_path = str(node.get("module_path") or "")
            method_name = str(node.get("method_name") or "")
            if module_path and method_name:
                methods_by_module_and_name.setdefault((module_path, method_name), []).append(node)
                methods_by_module_path.setdefault(module_path, []).append(node)

    for form_record in form_records:
        form_id = form_record["id"]
        form_module_path = (
            f"{form_record['owner_type']}.{form_record['owner_name']}.Forms."
            f"{form_record['form_name']}.FormModule"
        )
        if form_record["owner_type"] == "CommonForms":
            form_module_path = f"CommonForms.{form_record['form_name']}.FormModule"
        form_module_id = make_module_node_id(form_module_path)
        if form_module_id in nodes_by_id:
            add_graph_edge(edges, edge_keys, form_id, form_module_id, "has_form_module")

        form_methods_by_name = {
            str(method.get("method_name") or ""): method
            for method in methods_by_module_path.get(form_module_path, [])
        }

        for command in form_record.get("commands", []):
            command_name = str(command.get("name") or "")
            if not command_name:
                continue
            command_id = make_command_node_id(form_id, command_name)
            add_graph_node(
                nodes_by_id,
                command_id,
                "command",
                command_name=command_name,
                action=command.get("action", ""),
                title=command.get("title", ""),
                tooltip=command.get("tooltip", ""),
                form_name=form_record["form_name"],
            )
            add_graph_edge(edges, edge_keys, form_id, command_id, "contains_command")

            action = str(command.get("action") or "").strip()
            if not action:
                continue
            handler_id = make_handler_node_id(form_id, action)
            add_graph_node(
                nodes_by_id,
                handler_id,
                "handler",
                handler_name=action,
                handler_kind="command",
                form_name=form_record["form_name"],
            )
            add_graph_edge(edges, edge_keys, command_id, handler_id, "handled_by", source=command_name)

            target_method = form_methods_by_name.get(action)
            if target_method:
                add_graph_edge(
                    edges,
                    edge_keys,
                    handler_id,
                    str(target_method.get("id") or ""),
                    "implements_handler",
                    source=command_name,
                    event="command",
                )

        element_node_ids: dict[tuple[str, str], str] = {}
        for element in form_record.get("form_elements", []):
            element_id_value = str(element.get("element_id") or "")
            element_name = str(element.get("element_name") or "")
            element_node_id = make_form_element_node_id(form_id, element_id_value, element_name)
            element_node_ids[(element_id_value, element_name)] = element_node_id
            add_graph_node(
                nodes_by_id,
                element_node_id,
                "form_element",
                element_id=element_id_value,
                element_name=element_name,
                element_type=element.get("element_type", ""),
                title=element.get("title", ""),
                data_path=element.get("data_path", ""),
                command_name=element.get("command_name", ""),
                command_ref=element.get("command_ref", ""),
                visible=element.get("visible", True),
                enabled=element.get("enabled", True),
                read_only=element.get("read_only", False),
                parent_element_id=element.get("parent_element_id", ""),
                parent_element_name=element.get("parent_element_name", ""),
                depth=element.get("depth", 0),
                event_count=len(element.get("events", [])),
                form_name=form_record["form_name"],
            )

        for element in form_record.get("form_elements", []):
            element_id_value = str(element.get("element_id") or "")
            element_name = str(element.get("element_name") or "")
            element_node_id = element_node_ids.get((element_id_value, element_name))
            if not element_node_id:
                continue

            parent_key = (
                str(element.get("parent_element_id") or ""),
                str(element.get("parent_element_name") or ""),
            )
            parent_node_id = element_node_ids.get(parent_key)
            if parent_node_id:
                add_graph_edge(edges, edge_keys, parent_node_id, element_node_id, "contains_child_element")
            else:
                add_graph_edge(edges, edge_keys, form_id, element_node_id, "contains_element")

            command_ref = str(element.get("command_ref") or "")
            if command_ref:
                command_id = make_command_node_id(form_id, command_ref)
                if command_id in nodes_by_id:
                    add_graph_edge(
                        edges,
                        edge_keys,
                        element_node_id,
                        command_id,
                        "invokes_command",
                        source=element.get("command_name", ""),
                    )

            for event in element.get("events", []):
                handler_name = str(event.get("handler") or "").strip()
                if not handler_name:
                    continue
                handler_id = make_handler_node_id(form_id, handler_name)
                add_graph_node(
                    nodes_by_id,
                    handler_id,
                    "handler",
                    handler_name=handler_name,
                    handler_kind="element_event",
                    event_name=event.get("event", ""),
                    source_kind=element.get("element_type", ""),
                    source_name=element_name,
                    form_name=form_record["form_name"],
                )
                add_graph_edge(
                    edges,
                    edge_keys,
                    element_node_id,
                    handler_id,
                    "handles_event",
                    source=element_name,
                    event=event.get("event", ""),
                )

                target_method = form_methods_by_name.get(handler_name)
                if target_method:
                    add_graph_edge(
                        edges,
                        edge_keys,
                        handler_id,
                        str(target_method.get("id") or ""),
                        "implements_handler",
                        source=element_name,
                        event=event.get("event", ""),
                    )

        for event_handler in form_record.get("event_handlers", []):
            handler_name = str(event_handler.get("handler") or "").strip()
            if not handler_name:
                continue
            handler_id = make_handler_node_id(form_id, handler_name)
            add_graph_node(
                nodes_by_id,
                handler_id,
                "handler",
                handler_name=handler_name,
                handler_kind="event",
                event_name=event_handler.get("event", ""),
                source_kind=event_handler.get("source_kind", ""),
                source_name=event_handler.get("source_name", ""),
                form_name=form_record["form_name"],
            )
            add_graph_edge(
                edges,
                edge_keys,
                form_id,
                handler_id,
                "handles_event",
                source=event_handler.get("source_name", ""),
                event=event_handler.get("event", ""),
            )

            target_method = form_methods_by_name.get(handler_name)
            if target_method:
                add_graph_edge(
                    edges,
                    edge_keys,
                    handler_id,
                    str(target_method.get("id") or ""),
                    "implements_handler",
                    source=event_handler.get("source_name", ""),
                    event=event_handler.get("event", ""),
                )

    for method_record in method_records:
        source_id = method_record["id"]
        source_module_path = method_record["module_path"]
        source_method_name = method_record["method_name"]

        for call in extract_call_candidates(method_record["body"]):
            target_candidates: list[dict] = []
            resolution = call["kind"]
            via_module = ""

            if call["kind"] == "local":
                target_candidates = methods_by_module_and_name.get(
                    (source_module_path, call["method_name"]),
                    [],
                )
            else:
                via_module = call["module_name"]
                for module_node in modules_by_name.get(call["module_name"], []):
                    module_path = str(module_node.get("module_path") or "")
                    target_candidates.extend(
                        methods_by_module_and_name.get((module_path, call["method_name"]), [])
                    )

            unique_target_candidates = [
                candidate
                for candidate in target_candidates
                if candidate.get("id") != source_id
            ]
            if len(unique_target_candidates) != 1:
                continue

            target_node = unique_target_candidates[0]
            add_graph_edge(
                edges,
                edge_keys,
                source_id,
                str(target_node.get("id") or ""),
                "calls",
                source=call["method_name"],
                resolution=resolution,
                via_module=via_module,
                source_method=source_method_name,
            )

        for usage in extract_metadata_usages_from_bsl(method_record["body"]):
            target_id = make_metadata_node_id(usage["object_type"], usage["object_name"])
            add_graph_node(
                nodes_by_id,
                target_id,
                "metadata",
                object_type=usage["object_type"],
                object_name=usage["object_name"],
            )
            add_graph_edge(
                edges,
                edge_keys,
                source_id,
                target_id,
                "uses_metadata",
                namespace=usage["namespace"],
                source=usage["source_token"],
                source_method=source_method_name,
            )

    nodes = list(nodes_by_id.values())
    by_id = {node["id"]: index for index, node in enumerate(nodes)}
    outgoing: dict[str, list[int]] = {}
    incoming: dict[str, list[int]] = {}

    for edge_index, edge in enumerate(edges):
        outgoing.setdefault(edge["from"], []).append(edge_index)
        incoming.setdefault(edge["to"], []).append(edge_index)

    kind_counts: dict[str, int] = {}
    for node in nodes:
        kind = node["kind"]
        kind_counts[kind] = kind_counts.get(kind, 0) + 1

    edge_type_counts: dict[str, int] = {}
    for edge in edges:
        edge_type = edge["type"]
        edge_type_counts[edge_type] = edge_type_counts.get(edge_type, 0) + 1

    return {
        "version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": {
            "config_id": CONFIG_ID,
            "config_name": CONFIG_NAME,
            "config_profile": CONFIG_PROFILE,
            "config_kind": CONFIG_KIND,
            "base_config_id": BASE_CONFIG_ID,
            "platform_version": PLATFORM_VERSION,
            "export_path": str(export_dir),
            "index_filter": index_filter or "",
        },
        "stats": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "node_kinds": kind_counts,
            "edge_types": edge_type_counts,
        },
        "nodes": nodes,
        "edges": edges,
        "indexes": {
            "by_id": by_id,
            "outgoing": outgoing,
            "incoming": incoming,
        },
    }


def build_graph_cache(export_dir: Path, all_files: list[Path], index_filter: str | None = None) -> dict:
    return build_graph_projection(export_dir, all_files, index_filter=index_filter)


def write_graph_projection(graph_projection: dict, pbar=None):
    if not graph_writers:
        msg = "Graph writers не настроены. Запись graph projection пропущена."
        if pbar:
            pbar.write(msg)
        else:
            print(msg)
        return

    for writer in graph_writers:
        try:
            writer.write(graph_projection, pbar=pbar)
        except Exception as e:
            msg = f"Ошибка записи graph projection в target `{writer.target_name}`: {e}"
            if pbar:
                pbar.write(msg)
            else:
                print(msg)
            raise


def save_graph_cache(graph_cache: dict, pbar=None):
    write_graph_projection(graph_cache, pbar=pbar)

def process_and_index():
    check_memory_limit(MAX_RAM_PERCENT)
    print(f"Поиск файлов в {EXPORT_PATH}...")
    export_dir = Path(EXPORT_PATH)
    graph_only = os.getenv("GRAPH_ONLY", "").strip().lower() in {"1", "true", "yes", "on"}
    force_reindex = os.getenv("FORCE_REINDEX", "").strip().lower() in {"1", "true", "yes", "on"}
    
    if not export_dir.exists():
        print(f"Ошибка: Каталог выгрузки {EXPORT_PATH} не найден!")
        return

    # Фильтр для частичной индексации (полезно при тестировании)
    index_filter = os.getenv("INDEX_FILTER")
    if index_filter:
        print(f"Применяется фильтр индексации: '{index_filter}'")
    if force_reindex:
        print("FORCE_REINDEX активен: выбранные файлы будут переиндексированы даже без изменения mtime.")

    # Сначала сканируем всю файловую систему и собираем файлы
    all_files = []
    for root, dirs, files in os.walk(EXPORT_PATH):
        current_root = Path(root)
        dirs[:] = [directory for directory in dirs if directory != ".git"]
        if current_root != export_dir and "Configuration.xml" in files:
            print(f"Пропускаем вложенный export root: {current_root}")
            dirs[:] = []
            continue
        for file in files:
            filepath = Path(root) / file
            # Индексируем только BSL и XML
            if file.endswith('.bsl') or file.endswith('.xml'):
                # Пропускаем служебные XML
                if file in ["ConfigDumpInfo.xml", "Configuration.xml"]:
                    continue
                # Пока пропускаем шаблоны. Формы индексируем отдельно как самостоятельные сущности.
                if file.endswith('.xml') and "Templates" in filepath.parts:
                    continue
                
                # Фильтруем файлы по INDEX_FILTER, если он задан
                if index_filter and index_filter not in str(filepath):
                    continue
                
                all_files.append(filepath)

    print(f"Всего найдено файлов для проверки: {len(all_files)}")

    if graph_only:
        print("Запущен режим GRAPH_ONLY: индексация в Qdrant и генерация эмбеддингов пропускаются.")
        graph_projection = build_graph_projection(export_dir, all_files, index_filter=index_filter)
        write_graph_projection(graph_projection)
        print(
            f"Graph projection сохранен: узлов={graph_projection['stats']['node_count']}, "
            f"связей={graph_projection['stats']['edge_count']}."
        )
        return

    if cache_requires_schema_rebuild():
        if index_filter:
            raise RuntimeError(
                "INDEX_FILTER нельзя использовать при устаревшей схеме индекса. "
                "Сначала выполните полный reindex без фильтра."
            )
        if not RECREATE_ON_INDEX_SCHEMA_CHANGE:
            raise RuntimeError(
                f"Index cache использует старую схему. Требуется пересоздание коллекции "
                f"до версии {INDEX_SCHEMA_VERSION}; установите RECREATE_ON_INDEX_SCHEMA_CHANGE=true."
            )

        collections = qclient.get_collections().collections
        if any(collection.name == COLLECTION_NAME for collection in collections):
            print(
                f"Схема индекса изменилась. Пересоздаем производную Qdrant коллекцию "
                f"'{COLLECTION_NAME}' перед полным reindex."
            )
            qclient.delete_collection(COLLECTION_NAME)
        try:
            CACHE_FILE.unlink(missing_ok=True)
        except OSError as error:
            raise RuntimeError(f"Не удалось удалить устаревший cache `{CACHE_FILE}`: {error}") from error

    setup_collection()
    cache = load_cache()
    
    # Лимит чанков в одной порции для отправки в Qdrant (по умолчанию 100 для экономии RAM)
    CHUNK_BATCH_SIZE = int(os.getenv("INDEX_BATCH_SIZE", "100"))
    QDRANT_UPSERT_BATCH_SIZE = int(os.getenv("QDRANT_UPSERT_BATCH_SIZE", "100"))
    
    # Списки на обработку во временном батче
    batch_docs = []
    batch_payloads = []
    batch_files_to_delete = []
    batch_cache_updates = {}

    def flush_batch(docs, payloads, files_to_delete, cache_updates, pbar=None):
        if not files_to_delete and not docs:
            return
            
        def log_msg(msg):
            if pbar:
                pbar.write(msg)
            else:
                print(msg)

        # 1. Удаление старых точек для измененных файлов
        if files_to_delete:
            log_msg(f"Удаление старых векторов для {len(files_to_delete)} файлов из Qdrant...")
            del_batch_size = 100
            for i in range(0, len(files_to_delete), del_batch_size):
                batch_del = files_to_delete[i:i+del_batch_size]
                try:
                    qclient.delete(
                        collection_name=COLLECTION_NAME,
                        points_selector=models.Filter(
                            must=[
                                models.FieldCondition(
                                    key="file_path",
                                    match=models.MatchAny(any=batch_del)
                                )
                            ]
                        )
                    )
                except Exception as e:
                    log_msg(f"Ошибка при удалении старых векторов: {e}")

        # 2. Генерация эмбеддингов и загрузка новых точек
        if docs:
            # Проверка памяти перед запуском тяжелой генерации
            check_memory_limit(MAX_RAM_PERCENT, pbar=pbar)
            
            log_msg(f"Вычисление эмбеддингов и загрузка для {len(docs)} чанков...")
            try:
                upsert_batch_size = max(1, QDRANT_UPSERT_BATCH_SIZE)
                for offset in range(0, len(docs), upsert_batch_size):
                    docs_batch = docs[offset:offset + upsert_batch_size]
                    payloads_batch = payloads[offset:offset + upsert_batch_size]
                    batch_ids = [str(uuid.uuid4()) for _ in range(len(docs_batch))]

                    if USE_OPENAI_EMBEDDINGS:
                        log_msg(
                            f"Отправка запроса к OpenAI API "
                            f"({offset + 1}-{offset + len(docs_batch)} из {len(docs)} чанков)..."
                        )
                        response = openai_client.embeddings.create(
                            input=docs_batch,
                            model=OPENAI_EMBEDDING_MODEL
                        )
                        batch_vectors = [item.embedding for item in response.data]
                    else:
                        batch_vectors = []
                        embeddings_gen = encoder.embed(docs_batch, batch_size=32)
                        for vector in tqdm(
                            embeddings_gen,
                            total=len(docs_batch),
                            desc="  ├ Вычисление векторов",
                            leave=False,
                            bar_format="{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"
                        ):
                            batch_vectors.append(vector.tolist())

                    log_msg(
                        f"Загрузка {len(docs_batch)} векторов в коллекцию Qdrant "
                        f"'{COLLECTION_NAME}' ({offset + 1}-{offset + len(docs_batch)} из {len(docs)})..."
                    )
                    qclient.upsert(
                        collection_name=COLLECTION_NAME,
                        points=models.Batch(
                            ids=batch_ids,
                            vectors=batch_vectors,
                            payloads=payloads_batch
                        ),
                        timeout=QDRANT_TIMEOUT_SECONDS,
                    )
            except Exception as e:
                log_msg(f"Ошибка при записи векторов в Qdrant: {e}")
                raise e

        # 3. Сохранение обновленного состояния кэша на диск (чекпоинт)
        if cache_updates:
            cache.update(cache_updates)
            save_cache(cache, pbar=pbar)
            log_msg(f"Прогресс сохранен. Всего файлов в кэше: {len(cache)}")

        # Принудительный сбор мусора для высвобождения RAM
        import gc
        gc.collect()

    # Фильтруем файлы, которые реально изменились или отсутствуют в кэше
    print("Проверка изменений в файлах...")
    changed_files = []
    for filepath in all_files:
        rel_path = str(filepath.relative_to(export_dir))
        mtime = filepath.stat().st_mtime
        if force_reindex or cache.get(rel_path) != mtime:
            changed_files.append((filepath, rel_path, mtime))
            
    if not changed_files:
        print("Все файлы актуальны. Изменений не обнаружено.")
        if index_filter:
            print("Построение graph cache пропущено: активен INDEX_FILTER, граф был бы неполным.")
            return
        if not GRAPH_CACHE_FILE.exists():
            print("Graph cache не найден. Строим его по текущему экспорту...")
            graph_projection = build_graph_projection(export_dir, all_files, index_filter=index_filter)
            write_graph_projection(graph_projection)
            print(
                f"Graph projection сохранен: узлов={graph_projection['stats']['node_count']}, "
                f"связей={graph_projection['stats']['edge_count']}."
            )
        return

    print(f"Найдено измененных/новых файлов для индексации: {len(changed_files)}")

    # Основной цикл обработки файлов
    pbar = tqdm(total=len(changed_files), desc="Индексация изменений")

    for filepath, rel_path, mtime in changed_files:
        # Планируем удаление старых точек и обновление кэша для файла
        batch_files_to_delete.append(str(filepath))
        batch_cache_updates[rel_path] = mtime
        
        file_chunks = []

        # Парсим файл
        if filepath.suffix == '.bsl':
            module_type, module_name, module_path = build_module_identity(filepath, export_dir)
            methods = parse_bsl_file(filepath)
            module_summary = build_module_summary(module_path, module_type, methods)
            file_chunks.append((module_summary, {
                "document": module_summary,
                "config_id": CONFIG_ID,
                "config_name": CONFIG_NAME,
                "config_profile": CONFIG_PROFILE,
                "config_kind": CONFIG_KIND,
                "base_config_id": BASE_CONFIG_ID,
                "platform_version": PLATFORM_VERSION,
                "source_type": "bsl",
                "module_path": module_path,
                "module_type": module_type,
                "module_name": module_name,
                "method_count": len(methods),
                "method_names": [method["method_name"] for method in methods],
                "file_path": str(filepath),
                "chunk_type": "code_module_summary"
            }))
            for m in methods:
                context_text = (
                    f"Модуль: {module_path}\n"
                    f"Тип: {module_type}\n"
                    f"Метод: {m['method_name']}\n"
                    f"Код:\n{m['body']}"
                )
                file_chunks.append((context_text, {
                    "document": context_text,
                    "config_id": CONFIG_ID,
                    "config_name": CONFIG_NAME,
                    "config_profile": CONFIG_PROFILE,
                    "config_kind": CONFIG_KIND,
                    "base_config_id": BASE_CONFIG_ID,
                    "platform_version": PLATFORM_VERSION,
                    "source_type": "bsl",
                    "module_path": module_path,
                    "module_type": module_type,
                    "method_name": m['method_name'],
                    "start_line": m['start_line'],
                    "end_line": m['end_line'],
                    "extension_annotation": m.get('extension_annotation', ''),
                    "extension_target_method": m.get('extension_target_method', ''),
                    "extension_annotation_raw": m.get('extension_annotation_raw', ''),
                    "file_path": str(filepath),
                    "chunk_type": "code"
                }))
        
        elif filepath.suffix == '.xml':
            if "EventSubscriptions" in filepath.parts:
                subscription = parse_event_subscription_xml(filepath)
                if subscription:
                    file_chunks.append((subscription["card_text"], {
                        "document": subscription["card_text"],
                        "config_id": CONFIG_ID,
                        "config_name": CONFIG_NAME,
                        "config_profile": CONFIG_PROFILE,
                        "config_kind": CONFIG_KIND,
                        "base_config_id": BASE_CONFIG_ID,
                        "platform_version": PLATFORM_VERSION,
                        "source_type": "event_subscription_xml",
                        "subscription_name": subscription["name"],
                        "synonym": subscription["synonym"],
                        "event": subscription["event"],
                        "handler": subscription["handler"],
                        "handler_module": subscription["handler_module"],
                        "handler_method": subscription["handler_method"],
                        "sources": subscription["sources"],
                        "file_path": str(filepath),
                        "chunk_type": "metadata_event_subscription",
                    }))
            elif "Forms" in filepath.parts:
                form_info = parse_form_xml(filepath, export_dir)
                if form_info:
                    file_chunks.append((form_info['card_text'], {
                        "document": form_info['card_text'],
                        "config_id": CONFIG_ID,
                        "config_name": CONFIG_NAME,
                        "config_profile": CONFIG_PROFILE,
                        "config_kind": CONFIG_KIND,
                        "base_config_id": BASE_CONFIG_ID,
                        "platform_version": PLATFORM_VERSION,
                        "source_type": "form_xml",
                        "form_name": form_info['form_name'],
                        "owner_type": form_info['owner_type'],
                        "owner_object_type": form_info['owner_object_type'],
                        "owner_name": form_info['owner_name'],
                        "root_type": form_info['root_type'],
                        "file_path": str(filepath),
                        "chunk_type": "metadata_form"
                    }))
                    for command in form_info.get("commands", []):
                        command_card = build_form_command_card(form_info, command)
                        file_chunks.append((command_card, {
                            "document": command_card,
                            "config_id": CONFIG_ID,
                            "config_name": CONFIG_NAME,
                            "config_profile": CONFIG_PROFILE,
                            "config_kind": CONFIG_KIND,
                            "base_config_id": BASE_CONFIG_ID,
                            "platform_version": PLATFORM_VERSION,
                            "source_type": "form_xml",
                            "command_name": command.get("name", ""),
                            "action": command.get("action", ""),
                            "title": command.get("title", ""),
                            "tooltip": command.get("tooltip", ""),
                            "form_name": form_info['form_name'],
                            "owner_type": form_info['owner_type'],
                            "owner_object_type": form_info['owner_object_type'],
                            "owner_name": form_info['owner_name'],
                            "file_path": str(filepath),
                            "chunk_type": "metadata_command"
                        }))
            else:
                meta_info = parse_metadata_xml(filepath)
                if meta_info:
                    file_chunks.append((meta_info['card_text'], {
                        "document": meta_info['card_text'],
                        "config_id": CONFIG_ID,
                        "config_name": CONFIG_NAME,
                        "config_profile": CONFIG_PROFILE,
                        "config_kind": CONFIG_KIND,
                        "base_config_id": BASE_CONFIG_ID,
                        "platform_version": PLATFORM_VERSION,
                        "source_type": "xml",
                        "object_name": meta_info['object_name'],
                        "object_type": meta_info['object_type'],
                        "synonym": meta_info['synonym'],
                        "file_path": str(filepath),
                        "chunk_type": "metadata"
                    }))

        # Переносим чанки файла в общий батч
        for doc_text, payload in file_chunks:
            batch_docs.append(doc_text)
            batch_payloads.append(payload)

        # Сбрасываем батч при превышении лимита по чанкам или файлам (чтобы не копить пустые файлы)
        if len(batch_docs) >= CHUNK_BATCH_SIZE or len(batch_files_to_delete) >= 500:
            flush_batch(batch_docs, batch_payloads, batch_files_to_delete, batch_cache_updates, pbar)
            batch_docs.clear()
            batch_payloads.clear()
            batch_files_to_delete.clear()
            batch_cache_updates.clear()

        pbar.update(1)

    # Сбрасываем оставшийся хвост
    if batch_docs or batch_files_to_delete:
        flush_batch(batch_docs, batch_payloads, batch_files_to_delete, batch_cache_updates, pbar)
        
    pbar.close()
    if index_filter:
        print("Построение graph cache пропущено: активен INDEX_FILTER, граф был бы неполным.")
    else:
        print("Построение graph projection...")
        graph_projection = build_graph_projection(export_dir, all_files, index_filter=index_filter)
        write_graph_projection(graph_projection)
        print(
            f"Graph projection сохранен: узлов={graph_projection['stats']['node_count']}, "
            f"связей={graph_projection['stats']['edge_count']}."
        )
    print("Индексация успешно завершена!")

if __name__ == "__main__":
    process_and_index()

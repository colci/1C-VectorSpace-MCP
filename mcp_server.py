import os
import re
import json
import io
import importlib
import subprocess
from contextlib import redirect_stdout, redirect_stderr
from functools import lru_cache
from pathlib import Path
from datetime import datetime
from typing import Iterable
from dotenv import load_dotenv
from config_runtime import (
    INDEX_SCHEMA_CACHE_KEY,
    INDEX_SCHEMA_VERSION,
    RuntimeConfig,
    list_registered_configs,
    resolve_embedding_provider,
    resolve_runtime_config,
    sync_runtime_env,
)
from graph_repository import GraphRepository, build_graph_repository
from metadata_parsers import parse_event_subscription_xml
from bsl_language_server import analyze_bsl_files, collect_bsl_ls_status
from bsl_structure import analyze_bsl_structure

# Отключаем прокси для локальных запросов к Qdrant, чтобы избежать ошибки 503 на Windows
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

from qdrant_client import QdrantClient
from qdrant_client.http import models
from mcp.server.fastmcp import FastMCP

# Загрузка переменных окружения
load_dotenv()

# Эмбеддинги и настройки модели
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-large")
EMBEDDING_PROVIDER = resolve_embedding_provider(os.environ)
USE_OPENAI_EMBEDDINGS = EMBEDDING_PROVIDER == "openai"
OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_MODEL_PATH = os.getenv("EMBEDDING_MODEL_PATH", "").strip()
FASTEMBED_CACHE_DIR = os.getenv("FASTEMBED_CACHE_DIR", "").strip()
EMBEDDING_LOCAL_ONLY = os.getenv("EMBEDDING_LOCAL_ONLY", "").strip().lower() in {"1", "true", "yes", "on"}
GRAPH_BACKEND = os.getenv("GRAPH_BACKEND", "json").strip().lower()
MEMGRAPH_URI = os.getenv("MEMGRAPH_URI", "bolt://localhost:7687")
MEMGRAPH_USER = os.getenv("MEMGRAPH_USER", "")
MEMGRAPH_PASSWORD = os.getenv("MEMGRAPH_PASSWORD", "")
MEMGRAPH_DATABASE = os.getenv("MEMGRAPH_DATABASE", "")
RUNTIME_CONFIG = resolve_runtime_config()


def apply_runtime_config(runtime_config: RuntimeConfig) -> None:
    global RUNTIME_CONFIG, EXPORT_PATH, QDRANT_URL, CONFIG_NAME, CONFIG_ID, CONFIG_PROFILE
    global PLATFORM_VERSION, COLLECTION_NAME, CACHE_FILE, GRAPH_CACHE_FILE, CONFIG_KIND
    global BASE_CONFIG_ID, qclient

    RUNTIME_CONFIG = runtime_config
    sync_runtime_env(runtime_config)

    EXPORT_PATH = runtime_config.export_path
    QDRANT_URL = runtime_config.qdrant_url
    CONFIG_NAME = runtime_config.config_name
    CONFIG_ID = runtime_config.config_id
    CONFIG_PROFILE = runtime_config.config_profile
    PLATFORM_VERSION = runtime_config.platform_version
    COLLECTION_NAME = runtime_config.collection_name
    CACHE_FILE = Path(runtime_config.cache_file)
    GRAPH_CACHE_FILE = Path(runtime_config.graph_cache_file)
    CONFIG_KIND = runtime_config.config_kind
    BASE_CONFIG_ID = runtime_config.base_config_id
    qclient = QdrantClient(url=QDRANT_URL)


apply_runtime_config(RUNTIME_CONFIG)
graph_repository: GraphRepository = build_graph_repository(
    backend=GRAPH_BACKEND,
    graph_file=GRAPH_CACHE_FILE,
    memgraph_uri=MEMGRAPH_URI,
    memgraph_username=MEMGRAPH_USER,
    memgraph_password=MEMGRAPH_PASSWORD,
    config_id=CONFIG_ID,
    memgraph_database=MEMGRAPH_DATABASE,
)

# Инициализация клиента Qdrant
print(
    f"MCP-Сервер: configuration config_name='{CONFIG_NAME}', "
    f"config_id='{CONFIG_ID}', profile='{CONFIG_PROFILE}'."
)
if CONFIG_KIND != "configuration" or BASE_CONFIG_ID:
    print(
        f"MCP-Сервер: config kind='{CONFIG_KIND}', "
        f"base_config_id='{BASE_CONFIG_ID or 'none'}'."
    )
print(f"MCP-Сервер: graph backend source `{graph_repository.get_source_label()}`.")

encoder = None
openai_client = None
embedding_runtime_ready = False

# Создание экземпляра FastMCP
mcp = FastMCP("1C-VectorSpace-MCP")

TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_]+")
CAMEL_SPLIT_RE = re.compile(
    r"(?<=[a-zа-яё0-9])(?=[A-ZА-ЯЁ])|"
    r"(?<=[A-ZА-ЯЁ])(?=[A-ZА-ЯЁ][a-zа-яё])|"
    r"(?<=[A-Za-z])(?=[А-Яа-яЁё])|"
    r"(?<=[А-Яа-яЁё])(?=[A-Za-z])"
)

METADATA_TYPE_PATTERNS = {
    "Catalog": [r"\bсправочник(?:а|у|ом|е|и|ов)?\b", r"\bcatalogs?\b", r"\bcatalog\b"],
    "Document": [r"\bдокумент(?:а|у|ом|е|ы|ов)?\b", r"\bdocuments?\b", r"\bdocument\b"],
    "DocumentJournal": [r"\bжурнал(?:а|у|ом|е|ы|ов)?\b", r"\bdocumentjournals?\b", r"\bdocumentjournal\b"],
    "InformationRegister": [r"\bрегистр(?:а|у|ом|е|ы|ов)?\s+сведени(?:й|я|ям|ями|ях)\b", r"\binformationregisters?\b", r"\binformationregister\b"],
    "AccumulationRegister": [r"\bрегистр(?:а|у|ом|е|ы|ов)?\s+накоплени(?:я|й|ям|ями|ях)\b", r"\baccumulationregisters?\b", r"\baccumulationregister\b"],
    "Enum": [r"\bперечислени(?:е|я|ю|ем|ях)\b", r"\benums?\b", r"\benum\b"],
    "Constant": [r"\bконстант(?:а|ы|у|е|ой|ами|ах)?\b", r"\bconstants?\b", r"\bconstant\b"],
    "Role": [r"\bрол(?:ь|и|ей|ям|ями|ях)\b", r"\broles?\b", r"\brole\b"],
    "CommonModule": [r"\bобщ(?:ий|его|ему|им|ем)\s+модул(?:ь|я|ю|ем|е|и)\b", r"\bcommonmodules?\b", r"\bcommonmodule\b"],
}

COMMON_STOPWORDS = {
    "как", "где", "когда", "какой", "какая", "какие", "какое", "что", "это", "для", "про", "по",
    "из", "на", "в", "во", "с", "со", "и", "или", "у", "к", "от", "до", "над", "под", "без",
    "а", "же", "ли", "бы", "то", "не", "если", "при", "чтобы", "который", "которая", "которые",
    "мне", "нам", "нужно", "надо", "нужен", "нужна", "нужны", "понять", "подскажи", "подскажите",
    "ищу", "найди", "найти", "покажи", "показать", "посмотри", "посмотреть", "лежит", "лежат",
    "описан", "описана", "описано", "описаны", "работа", "работы", "работать", "работе",
    "конфигурация", "конфигурации", "база", "базе", "место", "месте", "объекте",
    "структура", "реквизиты", "реквизит", "табличная", "табличные", "табличной", "табличных",
    "часть", "части", "список", "справочник", "справочника", "документ", "документа", "журнал",
    "регистр", "сведений", "накопления", "метаданные", "метаданных", "объект", "объекта",
    "процедура", "функция", "метод", "модуль", "клиент", "сервер", "форма", "формы", "данные",
}

CODE_STOPWORDS = COMMON_STOPWORDS | {
    "заполнение", "получить", "получение", "обработка", "обработать", "открыть", "форма", "форму",
}

STRUCTURE_PRIORITY_TYPES = {
    "Catalog",
    "Document",
    "InformationRegister",
    "AccumulationRegister",
    "Enum",
    "ChartOfCharacteristicTypes",
    "ChartOfAccounts",
}

STRUCTURE_LOW_PRIORITY_TYPES = {
    "Report",
    "CommonPicture",
    "Constant",
    "Role",
    "DocumentJournal",
}

PRIMARY_METADATA_TYPES = {
    "Catalog",
    "Document",
    "InformationRegister",
    "AccumulationRegister",
    "Enum",
    "ChartOfCharacteristicTypes",
    "ChartOfAccounts",
    "CommonModule",
}

LOW_SIGNAL_METADATA_TYPES = {
    "Constant",
    "Role",
    "CommonPicture",
    "DocumentJournal",
    "Subsystem",
    "StyleItem",
    "ScheduledJob",
    "XDTOPackage",
    "Style",
    "Language",
    "SessionParameter",
    "CommonCommand",
    "CommonForm",
}

REGISTER_METADATA_TYPES = {
    "AccumulationRegister",
    "InformationRegister",
    "AccountingRegister",
    "CalculationRegister",
}

METADATA_BUSINESS_ANCHOR_PREFIXES = (
    "договор",
    "контрагент",
    "поступлен",
    "приход",
    "товар",
    "расчет",
    "взаиморасчет",
    "остат",
    "цен",
    "заказ",
    "клиент",
    "покупател",
    "поставщик",
)

SHARED_DOMAIN_RULES = (
    {
        "name": "counterparty_contract",
        "triggers": ("договор", "контрагент"),
        "metadata_terms": ("договор", "контрагент"),
        "metadata_types": ("Catalog", "Document"),
        "metadata_queries": (
            "договор контрагента",
            "реквизиты договора контрагента",
        ),
        "code_terms": ("договор", "контрагент"),
        "code_module_types": ("Catalogs", "Documents", "CommonModules"),
        "code_queries": (
            "договор контрагента заполнение реквизитов",
            "контрагент договор обработка",
        ),
    },
    {
        "name": "goods_receipt",
        "triggers": ("поступлен", "товар"),
        "metadata_terms": ("поступлен", "приход", "товар", "закуп"),
        "metadata_types": ("Document",),
        "metadata_queries": (
            "поступление товаров",
            "приходная накладная",
            "закупка товаров",
        ),
        "code_terms": ("поступлен", "приход", "товар", "закуп"),
        "code_module_types": ("Documents", "CommonModules"),
        "code_queries": (
            "поступление товаров проведение заполнение",
            "приходная накладная обработка проведения",
        ),
    },
    {
        "name": "customer_order",
        "triggers": ("заказ", "клиент"),
        "metadata_terms": ("заказ", "клиент", "покупател"),
        "metadata_types": ("Document",),
        "metadata_queries": (
            "заказ клиента",
            "заказ покупателя",
        ),
        "code_terms": ("заказ", "клиент", "покупател"),
        "code_module_types": ("Documents", "CommonModules"),
        "code_queries": (
            "заказ клиента проведение",
            "заказ клиента заполнение обработка",
        ),
    },
    {
        "name": "counterparty_settlements",
        "triggers": ("расчет", "контрагент"),
        "metadata_terms": ("расчет", "взаиморасчет", "контрагент", "задолж"),
        "metadata_types": (
            "AccumulationRegister",
            "InformationRegister",
            "Document",
        ),
        "metadata_queries": (
            "расчеты с контрагентами",
            "взаиморасчеты с контрагентами",
            "задолженность контрагентов",
        ),
        "code_terms": ("расчет", "взаиморасчет", "контрагент", "задолж"),
        "code_module_types": (
            "Documents",
            "CommonModules",
            "AccumulationRegisters",
            "InformationRegisters",
        ),
        "code_queries": (
            "расчеты с контрагентами проведение документа",
            "взаиморасчеты с контрагентами движения",
        ),
    },
)

CORE_NAVIGATION_METADATA_TYPES = {
    "Catalog",
    "Document",
    "InformationRegister",
    "AccumulationRegister",
}

LOW_PRIORITY_CODE_MODULE_TYPES = {
    "Reports",
}

VALIDATION_INTENT_PREFIXES = (
    "провер",
    "контрол",
    "ошиб",
    "нельзя",
    "запрет",
    "отказ",
    "проведен",
    "провест",
    "остат",
    "отриц",
    "недостат",
    "хват",
)

BUSINESS_RULE_METHOD_PHRASES = (
    "обработка проведения",
    "перед проведением",
    "проверка заполнения",
    "проверить заполнение",
    "проверить",
    "контроль",
    "отказ",
)

UI_HANDLER_METHOD_PHRASES = (
    "при изменении",
    "при активизации строки",
    "при начале редактирования",
    "начале редактирования",
    "при открытии",
    "при создании на сервере",
    "обработка выбора",
    "начало выбора",
    "очистить",
)

UNF_SEMANTIC_LOOKUP_PATTERNS = (
    {
        "object_type": "Document",
        "triggers": (
            "поступлен",
            "товар",
        ),
        "queries": (
            "приходная накладная",
            "приход товара",
            "закупка товаров",
        ),
    },
    {
        "object_type": "Document",
        "triggers": (
            "реализац",
            "товар",
        ),
        "queries": (
            "расходная накладная",
            "отгрузка товаров",
            "продажа товаров",
        ),
    },
    {
        "object_type": "Document",
        "triggers": (
            "заказ",
            "клиент",
        ),
        "queries": (
            "заказ покупателя",
        ),
    },
    {
        "object_type": "Document",
        "triggers": (
            "заказ",
            "поставщик",
        ),
        "queries": (
            "заказ поставщику",
            "закупочный заказ",
        ),
    },
    {
        "object_type": "Document",
        "triggers": (
            "счет",
            "клиент",
        ),
        "queries": (
            "счет на оплату",
            "счет покупателю",
        ),
    },
    {
        "object_type": "Document",
        "triggers": (
            "счет",
            "поставщик",
        ),
        "queries": (
            "счет на оплату поставщику",
            "счет поставщику",
        ),
    },
    {
        "object_type": "AccumulationRegister",
        "triggers": (
            "расчет",
            "покупател",
        ),
        "queries": (
            "расчеты с покупателями",
            "взаиморасчеты с покупателями",
        ),
    },
    {
        "object_type": "AccumulationRegister",
        "triggers": (
            "взаиморасчет",
            "контрагент",
        ),
        "queries": (
            "взаиморасчеты с контрагентами",
            "расчеты с контрагентами",
        ),
    },
    {
        "object_type": "AccumulationRegister",
        "triggers": (
            "денежн",
            "средств",
        ),
        "queries": (
            "движение денежных средств",
            "денежные средства",
        ),
    },
    {
        "object_type": "InformationRegister",
        "triggers": (
            "цен",
            "номенклатур",
        ),
        "queries": (
            "цены номенклатуры",
            "виды цен номенклатуры",
        ),
    },
)

GENERIC_SEMANTIC_LOOKUP_PATTERNS = (
    {
        "object_type": "Document",
        "triggers": (
            "поступлен",
            "товар",
        ),
        "queries": (
            "поступление товаров",
            "приходная накладная",
            "закупка товаров",
        ),
    },
    {
        "object_type": "Document",
        "triggers": (
            "реализац",
            "товар",
        ),
        "queries": (
            "реализация товаров",
            "расходная накладная",
            "продажа товаров",
        ),
    },
    {
        "object_type": "Document",
        "triggers": (
            "заказ",
            "клиент",
        ),
        "queries": (
            "заказ клиента",
            "заказ покупателя",
        ),
    },
    {
        "object_type": "Document",
        "triggers": (
            "заказ",
            "поставщик",
        ),
        "queries": (
            "заказ поставщику",
            "заказ поставщика",
        ),
    },
    {
        "object_type": "AccumulationRegister",
        "triggers": (
            "расчет",
            "покупател",
        ),
        "queries": (
            "расчеты с покупателями",
            "взаиморасчеты с покупателями",
            "задолженность покупателей",
        ),
    },
    {
        "object_type": "AccumulationRegister",
        "triggers": (
            "расчет",
            "контрагент",
        ),
        "queries": (
            "расчеты с контрагентами",
            "взаиморасчеты с контрагентами",
            "задолженность контрагентов",
        ),
    },
    {
        "object_type": "AccumulationRegister",
        "triggers": (
            "взаиморасчет",
            "контрагент",
        ),
        "queries": (
            "взаиморасчеты с контрагентами",
            "расчеты с контрагентами",
        ),
    },
    {
        "object_type": "AccumulationRegister",
        "triggers": (
            "остат",
        ),
        "queries": (
            "остатки товаров",
            "остатки номенклатуры",
            "товары на складах",
        ),
    },
    {
        "object_type": "AccumulationRegister",
        "triggers": (
            "движен",
        ),
        "queries": (
            "движения",
            "обороты",
        ),
    },
    {
        "object_type": "InformationRegister",
        "triggers": (
            "цен",
        ),
        "queries": (
            "цены номенклатуры",
            "виды цен",
            "цены",
        ),
    },
)

REFERENCE_OBJECT_TYPES = {
    "CatalogRef": "Catalog",
    "DocumentRef": "Document",
    "EnumRef": "Enum",
    "InformationRegisterRef": "InformationRegister",
    "AccumulationRegisterRef": "AccumulationRegister",
    "ChartOfCharacteristicTypesRef": "ChartOfCharacteristicTypes",
    "ChartOfAccountsRef": "ChartOfAccounts",
    "ChartOfCalculationTypesRef": "ChartOfCalculationTypes",
    "BusinessProcessRef": "BusinessProcess",
    "TaskRef": "Task",
    "CharacteristicRef": "Characteristic",
}

REFERENCE_RE = re.compile(r"cfg:(\w+)\.([A-Za-zА-Яа-яЁё0-9_]+)")

RUSSIAN_SUFFIXES = (
    "иями", "ями", "ами", "его", "ого", "ему", "ому", "ыми", "ими", "иях", "ах", "ях",
    "ия", "ья", "ий", "ый", "ой", "ее", "ие", "ые", "ое", "ей", "ам", "ям", "ом", "ем",
    "ов", "ев", "ую", "юю", "ая", "яя", "а", "я", "ы", "и", "у", "ю", "е", "о",
)


def normalize_text(text: str) -> str:
    prepared = CAMEL_SPLIT_RE.sub(" ", text.replace("_", " "))
    return " ".join(token.lower().replace("ё", "е") for token in TOKEN_RE.findall(prepared))


def tokenize(text: str) -> list[str]:
    return [token for token in normalize_text(text).split() if token]


def stem_token(token: str) -> str:
    if re.fullmatch(r"[а-яё]+", token) and len(token) > 4:
        for suffix in RUSSIAN_SUFFIXES:
            if token.endswith(suffix) and len(token) - len(suffix) >= 4:
                return token[:-len(suffix)]
    return token


def match_tokens(text: str) -> list[str]:
    return [stem_token(token) for token in tokenize(text)]


def extract_keywords(text: str, stopwords: set[str]) -> list[str]:
    keywords: list[str] = []
    seen = set()
    for token in match_tokens(text):
        if token in stopwords:
            continue
        if len(token) == 1 and not token.isdigit():
            continue
        if token not in seen:
            keywords.append(token)
            seen.add(token)
    return keywords


def detect_metadata_object_type(query: str) -> str | None:
    normalized = normalize_text(query)
    for object_type, patterns in METADATA_TYPE_PATTERNS.items():
        if any(re.search(pattern, normalized) for pattern in patterns):
            return object_type
    return None


def has_structural_intent(query: str) -> bool:
    normalized = normalize_text(query)
    return any(
        word in normalized for word in (
            "структура",
            "реквизит",
            "реквизиты",
            "табличная часть",
            "табличные части",
            "измерения",
            "ресурсы",
        )
    )


def has_report_intent(query: str) -> bool:
    normalized = normalize_text(query)
    return any(
        word in normalized for word in (
            "отчет",
            "отчеты",
            "report",
            "печат",
            "форма",
            "формат",
        )
    )


def has_validation_intent(query: str) -> bool:
    keywords = extract_keywords(query, CODE_STOPWORDS)
    if not keywords:
        return False

    matched = {
        prefix
        for token in keywords
        for prefix in VALIDATION_INTENT_PREFIXES
        if token.startswith(prefix) or prefix.startswith(token)
    }
    if {"проведен", "остат"} & matched and matched & {"провер", "контрол", "отриц", "недостат"}:
        return True
    return len(matched) >= 2


def normalized_contains_any(text: str, phrases: Iterable[str]) -> bool:
    normalized = normalize_text(text)
    return any(phrase in normalized for phrase in phrases)


def has_object_navigation_intent(query: str, detected_type: str | None = None) -> bool:
    if detected_type:
        return True

    if has_structural_intent(query):
        return True

    keywords = extract_keywords(query, COMMON_STOPWORDS)
    return len(keywords) >= 2


def has_register_intent(query: str, detected_type: str | None = None) -> bool:
    if detected_type in REGISTER_METADATA_TYPES:
        return True

    register_prefixes = (
        "взаиморасчет",
        "расчет",
        "задолж",
        "остат",
        "оборот",
        "движен",
        "денежн",
        "оплат",
        "цен",
        "себестоим",
    )
    keywords = extract_keywords(query, COMMON_STOPWORDS)
    for token in keywords:
        if any(token.startswith(prefix) or prefix.startswith(token) for prefix in register_prefixes):
            return True
    return False


def build_lookup_queries(query: str, stopwords: set[str]) -> list[str]:
    keywords = extract_keywords(query, stopwords)
    if not keywords:
        return []

    candidates = []
    phrase = " ".join(keywords)
    compact = "".join(keywords)

    for candidate in (phrase, compact):
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    return candidates


def build_retrieval_queries(query: str, stopwords: set[str]) -> list[str]:
    candidates = [query]
    keywords = extract_keywords(query, stopwords)
    if not keywords:
        return candidates

    phrase = " ".join(keywords)
    compact = " ".join(keywords[:8])
    for candidate in (phrase, compact):
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    return candidates


def score_keyword_overlap(text: str, keywords: Iterable[str], phrase: str = "") -> float:
    if not text:
        return 0.0

    normalized_text = " ".join(match_tokens(text))
    text_tokens = set(normalized_text.split())
    keywords = list(keywords)
    if not keywords:
        return 0.0

    exact_hits = sum(1 for keyword in keywords if keyword in text_tokens)
    substring_hits = sum(1 for keyword in keywords if keyword in normalized_text)
    coverage = exact_hits / len(keywords)
    score = coverage * 0.8 + substring_hits * 0.12

    if phrase and len(phrase) > 2 and phrase in normalized_text:
        score += 0.8

    return score


def score_name_specificity(text: str, keywords: Iterable[str], phrase: str = "") -> float:
    normalized_text = " ".join(match_tokens(text))
    keywords = list(keywords)
    if not normalized_text or not keywords:
        return 0.0

    text_tokens = normalized_text.split()
    score = 0.0

    if phrase and normalized_text == phrase:
        score += 2.2
    elif phrase and (
        normalized_text.startswith(f"{phrase} ")
        or normalized_text.endswith(f" {phrase}")
    ):
        score += 1.0
    elif phrase and phrase in normalized_text:
        score += 0.4

    matched_keywords = sum(1 for keyword in keywords if keyword in text_tokens)
    score += matched_keywords * 0.12

    extra_tokens = max(0, len(text_tokens) - len(keywords))
    score -= min(0.6, extra_tokens * 0.08)

    return max(score, 0.0)


def score_metadata_business_anchors(query: str, payload: dict) -> float:
    query_tokens = extract_keywords(query, COMMON_STOPWORDS)
    query_anchors = [
        token
        for token in query_tokens
        if any(token.startswith(prefix) or prefix.startswith(token) for prefix in METADATA_BUSINESS_ANCHOR_PREFIXES)
    ]
    if not query_anchors:
        return 0.0

    object_text = " ".join(match_tokens(
        f"{payload.get('object_name', '')} {payload.get('synonym', '')}"
    ))
    if not object_text:
        return 0.0

    matched = sum(1 for anchor in query_anchors if anchor in object_text)
    missing = len(query_anchors) - matched
    score = matched * 0.7

    if matched >= 2:
        score += 1.2
    elif matched == 1 and len(query_anchors) == 1:
        score += 0.5

    if missing and matched == 0:
        score -= min(1.4, missing * 0.35)

    if any(anchor.startswith("договор") for anchor in query_anchors):
        if "договор" in object_text:
            score += 2.2
        elif "контрагент" in object_text:
            score -= 1.2

    return score


def get_active_shared_domain_rules(query: str) -> list[dict]:
    keywords = set(extract_keywords(query, COMMON_STOPWORDS))
    if not keywords:
        return []

    active_rules = []
    for rule in SHARED_DOMAIN_RULES:
        trigger_tokens = set(rule["triggers"])
        overlap = count_alias_token_overlap(keywords, trigger_tokens)
        if overlap == len(trigger_tokens):
            active_rules.append(rule)

    return active_rules


def build_shared_domain_queries(
    query: str,
    target: str,
    detected_type: str | None = None,
) -> list[str]:
    candidates = []
    for rule in get_active_shared_domain_rules(query):
        if target == "metadata":
            expected_types = set(rule.get("metadata_types", ()))
            if detected_type and expected_types and detected_type not in expected_types:
                continue
            rule_queries = rule.get("metadata_queries", ())
        else:
            rule_queries = rule.get("code_queries", ())

        for candidate in rule_queries:
            if candidate not in candidates:
                candidates.append(candidate)

    return candidates


def score_shared_metadata_domain(query: str, payload: dict) -> float:
    active_rules = get_active_shared_domain_rules(query)
    if not active_rules:
        return 0.0

    object_type = payload.get("object_type", "")
    object_text = " ".join(match_tokens(
        f"{payload.get('object_name', '')} "
        f"{payload.get('synonym', '')} "
        f"{payload.get('document', '')}"
    ))
    if not object_text:
        return 0.0

    score = 0.0
    for rule in active_rules:
        matched = sum(1 for term in rule.get("metadata_terms", ()) if term in object_text)
        if matched:
            score += matched * 0.55
            if matched >= 2:
                score += 0.9

        expected_types = set(rule.get("metadata_types", ()))
        if expected_types and object_type in expected_types:
            score += 0.8

    return score


def score_shared_code_domain(query: str, payload: dict) -> float:
    active_rules = get_active_shared_domain_rules(query)
    if not active_rules:
        return 0.0

    module_type = payload.get("module_type", "")
    code_text = normalize_text(
        f"{payload.get('module_path', '')} "
        f"{payload.get('method_name', '')} "
        f"{payload.get('document', '')}"
    )
    if not code_text:
        return 0.0

    score = 0.0
    for rule in active_rules:
        matched = sum(1 for term in rule.get("code_terms", ()) if term in code_text)
        if matched:
            score += matched * 0.5
            if matched >= 2:
                score += 0.8

        expected_module_types = set(rule.get("code_module_types", ()))
        if expected_module_types and module_type in expected_module_types:
            score += 0.6

    return score


def build_metadata_explain_context(query: str) -> dict:
    detected_type = detect_metadata_object_type(query)
    return {
        "query": query,
        "detected_type": detected_type,
        "keywords": extract_keywords(query, COMMON_STOPWORDS),
        "phrase": " ".join(extract_keywords(query, COMMON_STOPWORDS)),
        "structural_intent": has_structural_intent(query),
        "navigation_intent": has_object_navigation_intent(query, detected_type),
        "register_intent": has_register_intent(query, detected_type),
    }


def build_code_explain_context(query: str) -> dict:
    return {
        "query": query,
        "keywords": extract_keywords(query, CODE_STOPWORDS),
        "phrase": " ".join(extract_keywords(query, CODE_STOPWORDS)),
        "report_intent": has_report_intent(query),
        "validation_intent": has_validation_intent(query),
    }


def add_score_component(components: list[tuple[str, float]], label: str, value: float) -> float:
    value = float(value)
    if abs(value) >= 0.05:
        components.append((label, value))
    return value


def metadata_result_key(payload: dict) -> str:
    return str(payload.get("file_path") or f"{payload.get('object_type')}:{payload.get('object_name')}")


def code_result_key(payload: dict) -> tuple:
    return (
        payload.get("file_path"),
        payload.get("method_name"),
        payload.get("start_line"),
    )


def score_metadata_explain_candidate(
    context: dict,
    payload: dict,
    source: str,
    base_score: float = 0.0,
    semantic_candidate: bool = False,
) -> tuple[float, list[tuple[str, float]]]:
    keywords = context["keywords"]
    phrase = context["phrase"]
    detected_type = context["detected_type"]
    structural_intent = context["structural_intent"]
    navigation_intent = context["navigation_intent"]
    register_intent = context["register_intent"]

    object_name = payload.get("object_name", "")
    synonym = payload.get("synonym", "")
    document = payload.get("document", "")
    object_type = payload.get("object_type", "")

    components: list[tuple[str, float]] = []
    score = 0.0

    if source == "vector":
        score += add_score_component(components, "vector_similarity", base_score)
        score += add_score_component(
            components,
            "object_name_overlap",
            score_keyword_overlap(object_name, keywords, phrase) * 1.8,
        )
        score += add_score_component(
            components,
            "object_name_specificity",
            score_name_specificity(object_name, keywords, phrase) * 1.6,
        )
        score += add_score_component(
            components,
            "synonym_overlap",
            score_keyword_overlap(synonym, keywords, phrase) * 1.5,
        )
        score += add_score_component(
            components,
            "synonym_specificity",
            score_name_specificity(synonym, keywords, phrase) * 1.1,
        )
        score += add_score_component(
            components,
            "document_overlap",
            score_keyword_overlap(document, keywords, phrase) * 0.5,
        )
        if detected_type and object_type == detected_type:
            score += add_score_component(components, "detected_type_match", 0.6)
        elif detected_type:
            score += add_score_component(components, "detected_type_penalty", -0.2)
    elif source == "lexical":
        score += add_score_component(
            components,
            "object_name_overlap",
            score_keyword_overlap(object_name, keywords, phrase) * 2.2,
        )
        score += add_score_component(
            components,
            "object_name_specificity",
            score_name_specificity(object_name, keywords, phrase) * 2.0,
        )
        score += add_score_component(
            components,
            "synonym_overlap",
            score_keyword_overlap(synonym, keywords, phrase) * 1.6,
        )
        score += add_score_component(
            components,
            "synonym_specificity",
            score_name_specificity(synonym, keywords, phrase) * 1.2,
        )
        score += add_score_component(
            components,
            "document_overlap",
            score_keyword_overlap(document, keywords, phrase) * 0.4,
        )
        if detected_type and object_type == detected_type:
            score += add_score_component(components, "detected_type_match", 0.6)
    else:
        score += add_score_component(components, "lookup_identifier_match", base_score)
        score += add_score_component(components, "lookup_channel_bonus", 1.5)
        if semantic_candidate:
            score += add_score_component(components, "semantic_expansion_bonus", 0.6)

    score += add_score_component(
        components,
        "business_anchors",
        score_metadata_business_anchors(context["query"], payload),
    )
    score += add_score_component(
        components,
        "shared_domain",
        score_shared_metadata_domain(context["query"], payload),
    )

    if structural_intent:
        if source == "lookup":
            if object_type in CORE_NAVIGATION_METADATA_TYPES:
                score += add_score_component(components, "structural_priority", 2.0)
        else:
            if object_type in STRUCTURE_PRIORITY_TYPES:
                score += add_score_component(
                    components,
                    "structural_priority",
                    0.8 if source == "vector" else 1.0,
                )
            elif object_type in STRUCTURE_LOW_PRIORITY_TYPES:
                score += add_score_component(
                    components,
                    "structural_low_priority_penalty",
                    -0.6 if source == "vector" else -0.8,
                )

    if navigation_intent and not detected_type:
        if source == "lookup":
            if object_type in CORE_NAVIGATION_METADATA_TYPES:
                score += add_score_component(components, "navigation_core_type", 4.0)
            elif object_type in PRIMARY_METADATA_TYPES:
                score += add_score_component(components, "navigation_primary_type", 1.5)
            elif object_type in LOW_SIGNAL_METADATA_TYPES:
                score += add_score_component(components, "navigation_low_signal_penalty", -2.0)
        else:
            if object_type in PRIMARY_METADATA_TYPES:
                score += add_score_component(
                    components,
                    "navigation_primary_type",
                    0.9 if source == "vector" else 1.2,
                )
            elif object_type in LOW_SIGNAL_METADATA_TYPES:
                score += add_score_component(
                    components,
                    "navigation_low_signal_penalty",
                    -1.4 if source == "vector" else -1.8,
                )

    if register_intent:
        if object_type in REGISTER_METADATA_TYPES:
            if source == "lookup":
                score += add_score_component(components, "register_intent_boost", 3.0)
            else:
                score += add_score_component(
                    components,
                    "register_intent_boost",
                    2.0 if source == "vector" else 2.2,
                )
        elif object_type in {"Catalog", "Subsystem", "Role"}:
            score += add_score_component(
                components,
                "register_intent_penalty",
                -3.0 if source == "lookup" else (-1.2 if source == "vector" else -1.4),
            )
        elif object_type in LOW_SIGNAL_METADATA_TYPES:
            score += add_score_component(
                components,
                "register_low_signal_penalty",
                -2.5 if source == "lookup" else (-1.8 if source == "vector" else -2.2),
            )

    return score, components


def score_code_explain_candidate(
    context: dict,
    payload: dict,
    source: str,
    base_score: float = 0.0,
) -> tuple[float, list[tuple[str, float]]]:
    keywords = context["keywords"]
    phrase = context["phrase"]
    report_intent = context["report_intent"]
    validation_intent = context["validation_intent"]

    module_path = payload.get("module_path", "")
    method_name = payload.get("method_name", "")
    document = payload.get("document", "")
    module_type = payload.get("module_type", "")

    method_overlap = score_keyword_overlap(method_name, keywords, phrase)
    module_overlap = score_keyword_overlap(module_path, keywords, phrase)
    document_overlap = score_keyword_overlap(document, keywords, phrase)
    components: list[tuple[str, float]] = []
    score = 0.0

    if source == "vector":
        score += add_score_component(components, "vector_similarity", base_score)
        score += add_score_component(components, "method_overlap", method_overlap * 1.8)
        score += add_score_component(
            components,
            "method_specificity",
            score_name_specificity(method_name, keywords, phrase) * 1.3,
        )
        score += add_score_component(components, "module_overlap", module_overlap * 1.5)
        score += add_score_component(
            components,
            "module_specificity",
            score_name_specificity(module_path, keywords, phrase) * 1.1,
        )
        score += add_score_component(components, "document_overlap", document_overlap * 0.9)

        total_overlap = method_overlap + module_overlap + document_overlap
        if keywords and total_overlap < 0.45:
            score += add_score_component(components, "low_overlap_penalty", -1.8)
        elif keywords and total_overlap < 0.8:
            score += add_score_component(components, "medium_overlap_penalty", -0.8)

        if module_type in LOW_PRIORITY_CODE_MODULE_TYPES and not report_intent:
            score += add_score_component(components, "low_priority_module_penalty", -1.6)
    else:
        score += add_score_component(components, "method_overlap", method_overlap * 2.0)
        score += add_score_component(
            components,
            "method_specificity",
            score_name_specificity(method_name, keywords, phrase) * 1.4,
        )
        score += add_score_component(components, "module_overlap", module_overlap * 2.4)
        score += add_score_component(
            components,
            "module_specificity",
            score_name_specificity(module_path, keywords, phrase) * 1.4,
        )

        total_overlap = method_overlap + module_overlap
        if keywords and total_overlap < 0.25:
            score += add_score_component(components, "below_threshold_penalty", -99.0)

        if module_type in LOW_PRIORITY_CODE_MODULE_TYPES and not report_intent:
            score += add_score_component(components, "low_priority_module_penalty", -1.8)

    if validation_intent:
        score += add_score_component(
            components,
            "validation_signals",
            score_validation_code_signals(payload, context["query"]),
        )
    score += add_score_component(
        components,
        "shared_domain",
        score_shared_code_domain(context["query"], payload),
    )

    return score, components


def merge_explain_result(
    merged: dict,
    key,
    payload: dict,
    source: str,
    final_score: float,
    components: list[tuple[str, float]],
    matched_query: str = "",
    base_score: float | None = None,
) -> None:
    source_info = {
        "source": source,
        "final_score": float(final_score),
        "components": components,
        "matched_query": matched_query,
        "base_score": None if base_score is None else float(base_score),
    }

    current = merged.get(key)
    if current is None:
        merged[key] = {
            "payload": payload,
            "final_score": float(final_score),
            "best_source": source,
            "best_components": components,
            "best_query": matched_query,
            "sources": [source_info],
        }
        return

    current["sources"].append(source_info)
    if final_score > current["final_score"]:
        current["payload"] = payload
        current["final_score"] = float(final_score)
        current["best_source"] = source
        current["best_components"] = components
        current["best_query"] = matched_query


def format_component_summary(components: list[tuple[str, float]], limit: int = 8) -> str:
    trimmed = components[:limit]
    return ", ".join(f"{label}={value:+.2f}" for label, value in trimmed) if trimmed else "none"


def collect_metadata_explanations(query: str, limit: int) -> tuple[dict, list[dict]]:
    context = build_metadata_explain_context(query)
    detected_type = context["detected_type"]
    retrieval_queries = build_retrieval_queries(query, COMMON_STOPWORDS)
    lookup_queries = build_lookup_queries(query, COMMON_STOPWORDS)
    semantic_queries = build_semantic_lookup_queries(query, detected_type)
    merged: dict[str, dict] = {}

    if has_qdrant_collection():
        must_conditions = [
            models.FieldCondition(
                key="chunk_type",
                match=models.MatchValue(value="metadata")
            )
        ]
        if detected_type:
            must_conditions.append(
                models.FieldCondition(
                    key="object_type",
                    match=models.MatchValue(value=detected_type)
                )
            )
        meta_filter = models.Filter(must=must_conditions)
        fetch_limit = max(limit * 20, 80)
        best_vector_hits: dict[str, tuple[float, dict, str]] = {}
        for retrieval_query in retrieval_queries:
            try:
                vector = get_query_embedding(retrieval_query)
                response = qclient.query_points(
                    collection_name=COLLECTION_NAME,
                    query=vector,
                    query_filter=meta_filter,
                    limit=fetch_limit
                )
            except Exception:
                continue

            for hit in response.points:
                payload = hit.payload or {}
                key = metadata_result_key(payload)
                current = best_vector_hits.get(key)
                vector_score = float(hit.score)
                if current is None or vector_score > current[0]:
                    best_vector_hits[key] = (vector_score, payload, retrieval_query)

        for key, (vector_score, payload, retrieval_query) in best_vector_hits.items():
            final_score, components = score_metadata_explain_candidate(
                context,
                payload,
                "vector",
                base_score=vector_score,
            )
            merge_explain_result(
                merged,
                key,
                payload,
                "vector",
                final_score,
                components,
                matched_query=retrieval_query,
                base_score=vector_score,
            )

    for lexical_score, payload in lexical_metadata_results(query, detected_type)[: max(limit * 5, 20)]:
        final_score, components = score_metadata_explain_candidate(context, payload, "lexical")
        if final_score <= 0:
            continue
        merge_explain_result(
            merged,
            metadata_result_key(payload),
            payload,
            "lexical",
            final_score,
            components,
            base_score=lexical_score,
        )

    for candidate in lookup_queries + semantic_queries:
        semantic_candidate = candidate in semantic_queries
        for lookup_score, payload in rank_metadata_lookup(candidate, detected_type)[: max(limit * 5, 20)]:
            final_score, components = score_metadata_explain_candidate(
                context,
                payload,
                "lookup",
                base_score=lookup_score,
                semantic_candidate=semantic_candidate,
            )
            merge_explain_result(
                merged,
                metadata_result_key(payload),
                payload,
                "lookup",
                final_score,
                components,
                matched_query=candidate,
                base_score=lookup_score,
            )

    results = sorted(merged.values(), key=lambda item: item["final_score"], reverse=True)
    for item in results:
        item["sources"].sort(key=lambda source_info: source_info["final_score"], reverse=True)
    return {
        "detected_type": detected_type,
        "retrieval_queries": retrieval_queries,
        "lookup_queries": lookup_queries,
        "semantic_queries": semantic_queries,
        "structural_intent": context["structural_intent"],
        "navigation_intent": context["navigation_intent"],
        "register_intent": context["register_intent"],
    }, results[:limit]


def collect_code_explanations(query: str, limit: int) -> tuple[dict, list[dict]]:
    context = build_code_explain_context(query)
    retrieval_queries = build_code_search_queries(query)
    merged: dict[tuple, dict] = {}

    if has_qdrant_collection():
        fetch_limit = max(limit * 30, 150)
        code_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="chunk_type",
                    match=models.MatchValue(value="code")
                )
            ]
        )
        best_vector_hits: dict[tuple, tuple[float, dict, str]] = {}
        for retrieval_query in retrieval_queries:
            try:
                vector = get_query_embedding(retrieval_query)
                response = qclient.query_points(
                    collection_name=COLLECTION_NAME,
                    query=vector,
                    query_filter=code_filter,
                    limit=fetch_limit
                )
            except Exception:
                continue

            for hit in response.points:
                payload = hit.payload or {}
                key = code_result_key(payload)
                current = best_vector_hits.get(key)
                vector_score = float(hit.score)
                if current is None or vector_score > current[0]:
                    best_vector_hits[key] = (vector_score, payload, retrieval_query)

        for key, (vector_score, payload, retrieval_query) in best_vector_hits.items():
            final_score, components = score_code_explain_candidate(
                context,
                payload,
                "vector",
                base_score=vector_score,
            )
            merge_explain_result(
                merged,
                key,
                payload,
                "vector",
                final_score,
                components,
                matched_query=retrieval_query,
                base_score=vector_score,
            )

    for lexical_score, signature in lexical_code_results(query)[: max(limit * 5, 20)]:
        final_score, components = score_code_explain_candidate(context, signature, "lexical")
        if final_score <= 0:
            continue
        payload = load_code_payload(signature) or signature
        merge_explain_result(
            merged,
            code_result_key(payload),
            payload,
            "lexical",
            final_score,
            components,
            base_score=lexical_score,
        )

    results = sorted(merged.values(), key=lambda item: item["final_score"], reverse=True)
    for item in results:
        item["sources"].sort(key=lambda source_info: source_info["final_score"], reverse=True)
    return {
        "retrieval_queries": retrieval_queries,
        "report_intent": context["report_intent"],
        "validation_intent": context["validation_intent"],
    }, results[:limit]


def rerank_metadata_results(query: str, results, detected_type: str | None):
    keywords = extract_keywords(query, COMMON_STOPWORDS)
    phrase = " ".join(keywords)
    structural_intent = has_structural_intent(query)
    navigation_intent = has_object_navigation_intent(query, detected_type)
    register_intent = has_register_intent(query, detected_type)
    ranked = []

    for hit in results:
        payload = hit.payload or {}
        object_name = payload.get("object_name", "")
        synonym = payload.get("synonym", "")
        document = payload.get("document", "")
        object_type = payload.get("object_type", "")

        score = float(hit.score)
        score += score_keyword_overlap(object_name, keywords, phrase) * 1.8
        score += score_name_specificity(object_name, keywords, phrase) * 1.6
        score += score_keyword_overlap(synonym, keywords, phrase) * 1.5
        score += score_name_specificity(synonym, keywords, phrase) * 1.1
        score += score_keyword_overlap(document, keywords, phrase) * 0.5
        score += score_metadata_business_anchors(query, payload)
        score += score_shared_metadata_domain(query, payload)

        if detected_type and object_type == detected_type:
            score += 0.6
        elif detected_type:
            score -= 0.2

        if structural_intent:
            if object_type in STRUCTURE_PRIORITY_TYPES:
                score += 0.8
            elif object_type in STRUCTURE_LOW_PRIORITY_TYPES:
                score -= 0.6

        if navigation_intent and not detected_type:
            if object_type in PRIMARY_METADATA_TYPES:
                score += 0.9
            elif object_type in LOW_SIGNAL_METADATA_TYPES:
                score -= 1.4

        if register_intent:
            if object_type in REGISTER_METADATA_TYPES:
                score += 2.0
            elif object_type in {"Catalog", "Subsystem", "Role"}:
                score -= 1.2
            elif object_type in LOW_SIGNAL_METADATA_TYPES:
                score -= 1.8

        ranked.append((score, hit))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


_metadata_payload_cache: dict[str, list[dict]] = {}
_form_payload_cache: list[dict] | None = None
_code_signature_cache: list[dict] | None = None
_module_payload_cache: list[dict] | None = None
_qdrant_collection_available: bool | None = None


def reset_runtime_state() -> None:
    global graph_repository
    global _metadata_payload_cache, _form_payload_cache, _code_signature_cache, _module_payload_cache, _qdrant_collection_available

    _metadata_payload_cache = {}
    _form_payload_cache = None
    _code_signature_cache = None
    _module_payload_cache = None
    _qdrant_collection_available = None

    try:
        graph_repository.close()
    except Exception:
        pass

    graph_repository = build_graph_repository(
        backend=GRAPH_BACKEND,
        graph_file=GRAPH_CACHE_FILE,
        memgraph_uri=MEMGRAPH_URI,
        memgraph_username=MEMGRAPH_USER,
        memgraph_password=MEMGRAPH_PASSWORD,
        config_id=CONFIG_ID,
        memgraph_database=MEMGRAPH_DATABASE,
    )


def format_reindex_log_tail(output: str, limit: int = 40) -> str:
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    if not lines:
        return "none"
    tail = lines[-limit:]
    return "\n".join(tail)


def resolve_export_target_path(target_path: str) -> tuple[Path | None, str | None]:
    raw_target = (target_path or "").strip()
    if not raw_target:
        return None, "Путь не задан."

    export_root = Path(EXPORT_PATH).resolve()
    candidate = Path(raw_target)
    if not candidate.is_absolute():
        candidate = export_root / candidate

    try:
        resolved = candidate.resolve()
    except Exception as error:
        return None, f"Не удалось разобрать путь `{target_path}`: {error}"

    try:
        resolved.relative_to(export_root)
    except ValueError:
        return None, f"Путь `{resolved}` находится вне EXPORT_PATH `{export_root}`."

    if not resolved.exists():
        return None, f"Путь `{resolved}` не найден."

    return resolved, None


@mcp.tool()
def bsl_ls_status() -> str:
    """Показывает готовность optional-интеграции с BSL Language Server."""
    status = collect_bsl_ls_status()
    version = " ".join(str(status["version"]).split())
    runtime_issue = " ".join(str(status["runtime_issue"]).split())
    return "\n".join([
        "## BSL Language Server Status",
        "",
        f"- Available: `{status['available']}`",
        f"- Configured binary: `{status['configured']}`",
        f"- Resolved binary: `{status['binary']}`",
        f"- Binary mode: `{status['mode']}`",
        f"- Java: `{status['java']}`",
        f"- Configuration: `{status['config']}`",
        f"- Timeout seconds: `{status['timeout_seconds']}`",
        f"- Version: `{version}`",
        f"- Runtime issue: `{runtime_issue}`",
        "- Fallback: `built-in structural bootstrap`",
    ])


@mcp.tool()
def validate_bsl(file_path: str, use_bsl_ls: bool = True) -> str:
    """
    Выполняет базовую структурную проверку одного BSL-файла внутри активного EXPORT_PATH.
    Это bootstrap validation и не заменяет BSL Language Server или проверку платформой 1С.
    """
    resolved_path, error = resolve_export_target_path(file_path)
    if error:
        return error
    assert resolved_path is not None
    if not resolved_path.is_file() or resolved_path.suffix.lower() != ".bsl":
        return f"Ожидается существующий `.bsl` файл, получен `{resolved_path}`."

    try:
        source = resolved_path.read_text(encoding="utf-8-sig", errors="ignore")
    except Exception as read_error:
        return f"Не удалось прочитать `{resolved_path}`: {read_error}"

    result = analyze_bsl_structure(source)
    bsl_ls_result = analyze_bsl_files([resolved_path], Path(EXPORT_PATH)) if use_bsl_ls else {
        "status": "not_requested",
        "message": "BSL Language Server не запрашивался.",
        "diagnostics": [],
        "error_count": 0,
        "warning_count": 0,
        "information_count": 0,
        "hint_count": 0,
    }
    total_errors = result["error_count"] + bsl_ls_result["error_count"]
    total_warnings = result["warning_count"] + bsl_ls_result["warning_count"]
    status = "failed" if total_errors else "passed_with_warnings" if total_warnings else "passed"
    if use_bsl_ls and bsl_ls_result["status"] != "completed" and status == "passed":
        status = "passed_bootstrap_only"
    lines = [
        "## BSL Validation",
        "",
        f"- File: `{resolved_path}`",
        f"- Config: `{CONFIG_ID}`",
        f"- Status: `{status}`",
        f"- Methods: `{result['method_count']}`",
        f"- Errors: `{total_errors}`",
        f"- Warnings: `{total_warnings}`",
        "- Validator: `built-in structural bootstrap + optional BSL Language Server`",
        "- Limitation: this does not replace compilation by the 1C platform.",
        "",
        "## Diagnostics",
        "",
    ]
    if not result["issues"]:
        lines.append("- No structural issues detected.")
    else:
        for issue in result["issues"][:100]:
            lines.append(
                f"- `{issue['severity']}` line `{issue['line']}`: {issue['message']}"
            )
        if len(result["issues"]) > 100:
            lines.append(f"- ... {len(result['issues']) - 100} more diagnostics omitted.")
    lines.extend([
        "",
        "## BSL Language Server",
        "",
        f"- Requested: `{use_bsl_ls}`",
        f"- Status: `{bsl_ls_result['status']}`",
        f"- Errors: `{bsl_ls_result['error_count']}`",
        f"- Warnings: `{bsl_ls_result['warning_count']}`",
        f"- Information: `{bsl_ls_result['information_count']}`",
        f"- Message: {bsl_ls_result['message']}",
    ])
    for diagnostic in bsl_ls_result["diagnostics"][:100]:
        lines.append(
            f"- `{diagnostic['severity']}` `{diagnostic['code']}` "
            f"line `{diagnostic['line']}:{diagnostic['column']}`: {diagnostic['message']}"
        )
    if len(bsl_ls_result["diagnostics"]) > 100:
        lines.append(f"- ... {len(bsl_ls_result['diagnostics']) - 100} more BSL LS diagnostics omitted.")
    return "\n".join(lines)


def is_inside_nested_export(path: Path, export_root: Path) -> bool:
    current = path.parent
    while current != export_root and export_root in current.parents:
        if (current / "Configuration.xml").exists():
            return True
        current = current.parent
    return False


def collect_bsl_files_from_path(target_path: str, max_files: int) -> tuple[list[Path], str | None]:
    resolved_path, error = resolve_export_target_path(target_path)
    if error:
        return [], error
    assert resolved_path is not None

    if resolved_path.is_file():
        if resolved_path.suffix.lower() != ".bsl":
            return [], f"Ожидается `.bsl` файл или каталог, получен `{resolved_path}`."
        return [resolved_path], None

    files: list[Path] = []
    for root, dirs, names in os.walk(resolved_path):
        current_root = Path(root)
        dirs[:] = [directory for directory in dirs if directory != ".git"]
        if current_root != resolved_path and "Configuration.xml" in names:
            dirs[:] = []
            continue
        for name in names:
            if name.lower().endswith(".bsl"):
                files.append(current_root / name)
                if len(files) >= max_files:
                    return sorted(files), None
    return sorted(files), None


def collect_git_changed_bsl_files(max_files: int) -> tuple[list[Path], str | None]:
    export_root = Path(EXPORT_PATH).resolve()
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                "core.quotepath=false",
                "-C",
                str(export_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return [], f"Не удалось получить Git status для `{export_root}`: {error}"

    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip() or f"exit code {result.returncode}"
        return [], f"Git status завершился с ошибкой: {details}"

    files: list[Path] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        relative_value = line[3:].strip()
        if " -> " in relative_value:
            relative_value = relative_value.split(" -> ", 1)[1].strip()
        candidate = (export_root / relative_value).resolve()
        try:
            candidate.relative_to(export_root)
        except ValueError:
            continue
        if candidate.suffix.lower() != ".bsl" or not candidate.is_file():
            continue
        if is_inside_nested_export(candidate, export_root):
            continue
        files.append(candidate)
        if len(files) >= max_files:
            break
    return sorted(set(files)), None


def validate_bsl_paths(files: list[Path], use_bsl_ls: bool = False) -> dict:
    validation_results = []
    total_errors = 0
    total_warnings = 0
    for file in files:
        try:
            source_text = file.read_text(encoding="utf-8-sig", errors="ignore")
            result = analyze_bsl_structure(source_text)
        except Exception as read_error:
            result = {
                "method_count": 0,
                "error_count": 1,
                "warning_count": 0,
                "issues": [{"severity": "error", "line": 0, "message": str(read_error)}],
            }
        validation_results.append((file, result))
        total_errors += result["error_count"]
        total_warnings += result["warning_count"]

    bsl_ls_result = analyze_bsl_files(files, Path(EXPORT_PATH)) if use_bsl_ls else {
        "status": "not_requested",
        "message": "BSL Language Server не запрашивался.",
        "diagnostics": [],
        "error_count": 0,
        "warning_count": 0,
        "information_count": 0,
        "hint_count": 0,
    }
    results_by_file = {
        normalized_file_key(file): result
        for file, result in validation_results
    }
    if bsl_ls_result["status"] == "completed":
        for diagnostic in bsl_ls_result["diagnostics"]:
            result = results_by_file.get(normalized_file_key(diagnostic["file"]))
            if result is None or diagnostic["severity"] not in {"error", "warning"}:
                continue
            result["issues"].append({
                "severity": diagnostic["severity"],
                "line": diagnostic["line"],
                "message": f"[{diagnostic['code']}] {diagnostic['message']}",
            })
            if diagnostic["severity"] == "error":
                result["error_count"] += 1
                total_errors += 1
            else:
                result["warning_count"] += 1
                total_warnings += 1

    status = "failed" if total_errors else "passed_with_warnings" if total_warnings else "passed"
    if use_bsl_ls and bsl_ls_result["status"] != "completed" and status == "passed":
        status = "passed_bootstrap_only"
    return {
        "status": status,
        "files": validation_results,
        "error_count": total_errors,
        "warning_count": total_warnings,
        "bsl_ls": bsl_ls_result,
    }


@mcp.tool()
def validate_changed_files(path: str = "", max_files: int = 200, use_bsl_ls: bool = False) -> str:
    """
    Пакетно проверяет BSL-файлы. Без path использует измененные/untracked файлы Git
    активного export; с path проверяет конкретный файл или каталог внутри EXPORT_PATH.
    """
    safe_max_files = clamp_workflow_limit(max_files, default=200, maximum=2000)
    if path.strip():
        files, error = collect_bsl_files_from_path(path, safe_max_files)
        source = "explicit_path"
    else:
        files, error = collect_git_changed_bsl_files(safe_max_files)
        source = "git_status"
    if error:
        return error

    lines = [
        "## Changed Files Validation",
        "",
        f"- Config: `{CONFIG_ID}`",
        f"- Source: `{source}`",
        f"- Requested path: `{path or 'git changed files'}`",
        f"- Max files: `{safe_max_files}`",
        f"- Files selected: `{len(files)}`",
        f"- BSL Language Server requested: `{use_bsl_ls}`",
        "- Validator: `built-in structural bootstrap + optional BSL Language Server`",
        "- Limitation: this does not replace compilation by the 1C platform.",
    ]
    if not files:
        lines.extend([
            "",
            "## Summary",
            "",
            "- Status: `no_files`",
            "- Files validated: `0`",
            "- Errors: `0`",
            "- Warnings: `0`",
        ])
        return "\n".join(lines)

    validation = validate_bsl_paths(files, use_bsl_ls=use_bsl_ls)
    validation_results = validation["files"]
    total_errors = validation["error_count"]
    total_warnings = validation["warning_count"]
    status = validation["status"]
    lines.extend([
        "",
        "## Summary",
        "",
        f"- Status: `{status}`",
        f"- Files validated: `{len(validation_results)}`",
        f"- Errors: `{total_errors}`",
        f"- Warnings: `{total_warnings}`",
        f"- BSL Language Server status: `{validation['bsl_ls']['status']}`",
        f"- BSL Language Server message: {validation['bsl_ls']['message']}",
        "",
        "## Files",
        "",
    ])
    export_root = Path(EXPORT_PATH).resolve()
    for file, result in validation_results:
        relative_path = file.relative_to(export_root)
        file_status = "failed" if result["error_count"] else "warning" if result["warning_count"] else "passed"
        lines.append(
            f"- `{relative_path}`: status=`{file_status}`, methods=`{result['method_count']}`, "
            f"errors=`{result['error_count']}`, warnings=`{result['warning_count']}`"
        )
        for issue in result["issues"][:20]:
            lines.append(
                f"  - `{issue['severity']}` line `{issue['line']}`: {issue['message']}"
            )
        if len(result["issues"]) > 20:
            lines.append(f"  - ... {len(result['issues']) - 20} more diagnostics omitted.")
    return "\n".join(lines)


def normalized_file_key(value: str | Path) -> str:
    return os.path.normcase(os.path.abspath(str(value)))


def collect_changed_method_impact(files: list[Path], method_limit: int) -> dict:
    context = {
        "graph_available": graph_repository.exists(),
        "method_count": 0,
        "methods_analyzed": 0,
        "caller_count": 0,
        "callee_count": 0,
        "entrypoint_count": 0,
        "method_details": [],
    }
    if not context["graph_available"]:
        return context

    target_keys = {normalized_file_key(file) for file in files}
    methods = [
        node
        for node in graph_repository.iter_nodes("method")
        if normalized_file_key(node.get("file_path", "")) in target_keys
    ]
    methods.sort(
        key=lambda node: (
            node.get("file_path", ""),
            int(node.get("start_line") or 0),
            node.get("method_name", ""),
        )
    )
    context["method_count"] = len(methods)

    unique_callers: set[str] = set()
    unique_callees: set[str] = set()
    unique_entrypoints: set[str] = set()
    for method in methods[:method_limit]:
        method_id = method.get("id", "")
        callers = [
            node
            for _, node in graph_repository.iter_edges(method_id, "incoming", "calls")
            if node
        ]
        callees = [
            node
            for _, node in graph_repository.iter_edges(method_id, "outgoing", "calls")
            if node
        ]
        entrypoints = [
            node
            for _, node in graph_repository.iter_edges(method_id, "incoming", "implements_handler")
            if node
        ]
        unique_callers.update(str(node.get("id") or method_label(node)) for node in callers)
        unique_callees.update(str(node.get("id") or method_label(node)) for node in callees)
        unique_entrypoints.update(str(node.get("id") or graph_node_label(node)) for node in entrypoints)
        context["method_details"].append({
            "label": method_label(method),
            "file_path": method.get("file_path", ""),
            "start_line": method.get("start_line", "?"),
            "end_line": method.get("end_line", "?"),
            "callers": len(callers),
            "callees": len(callees),
            "entrypoints": len(entrypoints),
        })

    context["methods_analyzed"] = len(context["method_details"])
    context["caller_count"] = len(unique_callers)
    context["callee_count"] = len(unique_callees)
    context["entrypoint_count"] = len(unique_entrypoints)
    return context


def evaluate_changed_file_freshness(files: list[Path]) -> dict:
    export_root = Path(EXPORT_PATH).resolve()
    cache = load_index_cache()
    stale_cache_files = []
    for file in files:
        relative_path = str(file.relative_to(export_root))
        try:
            mtime = file.stat().st_mtime
        except OSError:
            stale_cache_files.append(relative_path)
            continue
        if cache.get(relative_path) != mtime:
            stale_cache_files.append(relative_path)

    generated_at = graph_repository.get_generated_at() if graph_repository.exists() else "unknown"
    files_newer_than_graph = []
    try:
        generated_timestamp = datetime.fromisoformat(generated_at).timestamp()
    except (TypeError, ValueError):
        generated_timestamp = None
    if generated_timestamp is not None:
        for file in files:
            try:
                if file.stat().st_mtime > generated_timestamp:
                    files_newer_than_graph.append(str(file.relative_to(export_root)))
            except OSError:
                continue

    return {
        "cache_exists": CACHE_FILE.exists(),
        "stale_cache_files": stale_cache_files,
        "graph_generated_at": generated_at,
        "files_newer_than_graph": files_newer_than_graph,
    }


@mcp.tool()
def post_change_report(
    path: str = "",
    max_files: int = 50,
    method_limit: int = 30,
    use_bsl_ls: bool = False,
) -> str:
    """
    Формирует post-change отчет для измененных BSL: validation, method impact
    по текущему graph projection и freshness относительно index cache/graph.
    """
    safe_max_files = clamp_workflow_limit(max_files, default=50, maximum=500)
    safe_method_limit = clamp_workflow_limit(method_limit, default=30, maximum=100)
    if path.strip():
        files, error = collect_bsl_files_from_path(path, safe_max_files)
        source = "explicit_path"
    else:
        files, error = collect_git_changed_bsl_files(safe_max_files)
        source = "git_status"
    if error:
        return error

    lines = [
        "## Post-Change Report",
        "",
        f"- Config: `{CONFIG_ID}`",
        f"- Source: `{source}`",
        f"- Requested path: `{path or 'git changed files'}`",
        f"- Files selected: `{len(files)}`",
        f"- Graph source: `{graph_repository.get_source_label()}`",
        f"- BSL Language Server requested: `{use_bsl_ls}`",
    ]
    if not files:
        lines.extend([
            "",
            "## Overall Status",
            "",
            "- Status: `no_files`",
            "- No changed BSL files were selected.",
        ])
        return "\n".join(lines)

    validation = validate_bsl_paths(files, use_bsl_ls=use_bsl_ls)
    impact = collect_changed_method_impact(files, safe_method_limit)
    freshness = evaluate_changed_file_freshness(files)

    if validation["error_count"]:
        overall_status = "failed_validation"
    elif use_bsl_ls and validation["bsl_ls"]["status"] != "completed":
        overall_status = "incomplete_bsl_ls_validation"
    elif not impact["graph_available"]:
        overall_status = "incomplete_graph"
    elif freshness["stale_cache_files"] or freshness["files_newer_than_graph"]:
        overall_status = "needs_reindex"
    elif validation["warning_count"]:
        overall_status = "passed_with_warnings"
    else:
        overall_status = "ready_for_review"

    lines.extend([
        "",
        "## Overall Status",
        "",
        f"- Status: `{overall_status}`",
        "",
        "## Validation",
        "",
        f"- Files validated: `{len(validation['files'])}`",
        f"- Errors: `{validation['error_count']}`",
        f"- Warnings: `{validation['warning_count']}`",
        f"- BSL Language Server status: `{validation['bsl_ls']['status']}`",
        f"- BSL Language Server message: {validation['bsl_ls']['message']}",
    ])
    for file, result in validation["files"]:
        relative_path = file.relative_to(Path(EXPORT_PATH).resolve())
        if not result["issues"]:
            continue
        lines.append(f"- `{relative_path}` diagnostics:")
        for issue in result["issues"][:10]:
            lines.append(f"  - `{issue['severity']}` line `{issue['line']}`: {issue['message']}")

    lines.extend([
        "",
        "## Graph Impact",
        "",
        f"- Graph available: `{impact['graph_available']}`",
        f"- Methods found in selected files: `{impact['method_count']}`",
        f"- Methods analyzed: `{impact['methods_analyzed']}`",
        f"- Unique callers found: `{impact['caller_count']}`",
        f"- Unique callees found: `{impact['callee_count']}`",
        f"- UI/command entrypoints found: `{impact['entrypoint_count']}`",
    ])
    for method in impact["method_details"]:
        lines.append(
            f"- `{method['label']}` lines `{method['start_line']}-{method['end_line']}`: "
            f"callers=`{method['callers']}`, callees=`{method['callees']}`, entrypoints=`{method['entrypoints']}`"
        )
    if impact["method_count"] > impact["methods_analyzed"]:
        lines.append(
            f"- Impact traversal limited: `{impact['method_count'] - impact['methods_analyzed']}` methods were not expanded."
        )

    lines.extend([
        "",
        "## Freshness",
        "",
        f"- Index cache exists: `{freshness['cache_exists']}`",
        f"- Files stale vs cache: `{len(freshness['stale_cache_files'])}`",
        f"- Graph generated at: `{freshness['graph_generated_at']}`",
        f"- Files newer than graph: `{len(freshness['files_newer_than_graph'])}`",
        "",
        "## Required Actions",
        "",
    ])
    if validation["error_count"]:
        lines.append("- Fix structural BSL validation errors before further review.")
    if use_bsl_ls and validation["bsl_ls"]["status"] != "completed":
        lines.append("- Configure or repair BSL Language Server, then repeat validation with `use_bsl_ls=true`.")
    if freshness["stale_cache_files"] or freshness["files_newer_than_graph"]:
        lines.append("- Run `reindex_file` or `reindex_path`, then repeat this report.")
    if impact["caller_count"]:
        lines.append("- Review affected callers before changing parameters, return values or side effects.")
    if impact["entrypoint_count"]:
        lines.append("- Re-test linked form commands and UI event handlers.")
    lines.extend([
        "- Run BSL Language Server/platform diagnostics when integration is available.",
        "- Run project-specific YAxUnit/Vanessa tests.",
        "- Verify every acceptance condition from the original task.",
    ])
    return "\n".join(lines)


def run_indexer_job(index_filter: str | None, graph_only: bool, force_reindex: bool) -> str:
    import index_config

    previous_filter = os.environ.get("INDEX_FILTER")
    previous_graph_only = os.environ.get("GRAPH_ONLY")
    previous_force_reindex = os.environ.get("FORCE_REINDEX")

    if index_filter:
        os.environ["INDEX_FILTER"] = index_filter
    else:
        os.environ.pop("INDEX_FILTER", None)

    if graph_only:
        os.environ["GRAPH_ONLY"] = "1"
    else:
        os.environ.pop("GRAPH_ONLY", None)

    if force_reindex:
        os.environ["FORCE_REINDEX"] = "1"
    else:
        os.environ.pop("FORCE_REINDEX", None)

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    try:
        index_config = importlib.reload(index_config)
        with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
            index_config.process_and_index()
    finally:
        if previous_filter is not None:
            os.environ["INDEX_FILTER"] = previous_filter
        else:
            os.environ.pop("INDEX_FILTER", None)

        if previous_graph_only is not None:
            os.environ["GRAPH_ONLY"] = previous_graph_only
        else:
            os.environ.pop("GRAPH_ONLY", None)

        if previous_force_reindex is not None:
            os.environ["FORCE_REINDEX"] = previous_force_reindex
        else:
            os.environ.pop("FORCE_REINDEX", None)

    output = stdout_buffer.getvalue()
    errors = stderr_buffer.getvalue()
    combined = output if not errors else f"{output.rstrip()}\n{errors}".strip()
    return combined


def reindex_export_target(target_path: str, path_kind: str, rebuild_graph: bool = True) -> str:
    resolved_path, error = resolve_export_target_path(target_path)
    if error:
        return error
    assert resolved_path is not None

    export_root = Path(EXPORT_PATH).resolve()
    if path_kind == "file":
        if resolved_path.is_dir():
            return f"`{resolved_path}` является каталогом. Для него используйте `reindex_path`."
        if resolved_path.suffix.lower() not in {".bsl", ".xml"}:
            return f"Поддерживаются только `.bsl` и `.xml`, получен `{resolved_path.suffix}`."
        relative_filter = str(resolved_path.relative_to(export_root))
    else:
        if not resolved_path.is_dir():
            return f"`{resolved_path}` не является каталогом. Для файла используйте `reindex_file`."
        relative_filter = str(resolved_path.relative_to(export_root))
        if relative_filter == ".":
            relative_filter = ""

    partial_output = run_indexer_job(
        index_filter=relative_filter or None,
        graph_only=False,
        force_reindex=True,
    )

    graph_output = ""
    if rebuild_graph:
        graph_output = run_indexer_job(
            index_filter=None,
            graph_only=True,
            force_reindex=False,
        )

    reset_runtime_state()

    lines = [
        "## Reindex",
        "",
        f"- Target kind: `{path_kind}`",
        f"- Target path: `{resolved_path}`",
        f"- Relative filter: `{relative_filter or '.'}`",
        f"- Rebuild graph: `{rebuild_graph}`",
        "",
        "## Partial Reindex Log",
        "",
        f"```text\n{format_reindex_log_tail(partial_output)}\n```",
    ]

    if rebuild_graph:
        lines.extend([
            "",
            "## Graph Rebuild Log",
            "",
            f"```text\n{format_reindex_log_tail(graph_output)}\n```",
        ])

    return "\n".join(lines)


def has_qdrant_collection() -> bool:
    global _qdrant_collection_available
    if _qdrant_collection_available is not None:
        return _qdrant_collection_available

    try:
        qclient.get_collection(COLLECTION_NAME)
        _qdrant_collection_available = True
    except Exception:
        _qdrant_collection_available = False

    return _qdrant_collection_available


def load_metadata_payloads_from_graph(detected_type: str | None) -> list[dict]:
    if not graph_repository.exists():
        return []

    payloads: list[dict] = []
    for node in graph_repository.iter_nodes("metadata"):
        if detected_type and node.get("object_type") != detected_type:
            continue
        payloads.append(node)
    return payloads


def load_form_payloads_from_graph() -> list[dict]:
    if not graph_repository.exists():
        return []
    return [node for node in graph_repository.iter_nodes("form")]


def load_code_signatures_from_graph() -> list[dict]:
    if not graph_repository.exists():
        return []

    signatures: list[dict] = []
    for node in graph_repository.iter_nodes("method"):
        signatures.append({
            "module_path": node.get("module_path", ""),
            "module_type": node.get("module_type", ""),
            "method_name": node.get("method_name", ""),
            "file_path": node.get("file_path", ""),
            "start_line": node.get("start_line"),
            "end_line": node.get("end_line"),
            "extension_annotation": node.get("extension_annotation", ""),
            "extension_target_method": node.get("extension_target_method", ""),
            "document": node.get("document", ""),
        })
    return signatures


def infer_module_name(module_path: str) -> str:
    parts = [part for part in module_path.split(".") if part]
    if len(parts) >= 2:
        return parts[1]
    if parts:
        return parts[-1]
    return ""


def load_module_payloads_from_graph() -> list[dict]:
    if not graph_repository.exists():
        return []
    return [node for node in graph_repository.iter_nodes("module")]


def load_module_summary_payloads() -> list[dict]:
    if not has_qdrant_collection():
        return []

    offset = None
    payloads: list[dict] = []
    summary_filter = models.Filter(
        must=[
            models.FieldCondition(
                key="chunk_type",
                match=models.MatchValue(value="code_module_summary"),
            )
        ]
    )
    while True:
        points, offset = qclient.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=summary_filter,
            limit=256,
            with_payload=True,
            with_vectors=False,
            offset=offset,
        )
        for point in points:
            if point.payload:
                payloads.append(point.payload)
        if offset is None:
            break
    return payloads


def load_module_payloads() -> list[dict]:
    global _module_payload_cache
    if _module_payload_cache is not None:
        return _module_payload_cache

    summary_payloads = load_module_summary_payloads()
    summaries_by_path = {
        payload.get("module_path", ""): payload
        for payload in summary_payloads
        if payload.get("module_path")
    }
    graph_payloads = load_module_payloads_from_graph()
    if graph_payloads:
        merged_payloads = []
        for graph_payload in graph_payloads:
            summary_payload = summaries_by_path.get(graph_payload.get("module_path", ""), {})
            merged_payloads.append({**graph_payload, **summary_payload})
        _module_payload_cache = merged_payloads
        return _module_payload_cache

    if summary_payloads:
        _module_payload_cache = summary_payloads
        return _module_payload_cache

    module_map: dict[tuple[str, str, str], dict] = {}
    for signature in load_code_signatures():
        module_path = signature.get("module_path", "")
        module_type = signature.get("module_type", "")
        file_path = signature.get("file_path", "")
        key = (module_path, module_type, file_path)
        payload = module_map.get(key)
        if payload is None:
            payload = {
                "module_path": module_path,
                "module_type": module_type,
                "module_name": infer_module_name(module_path),
                "file_path": file_path,
                "kind": "module",
            }
            module_map[key] = payload

    _module_payload_cache = list(module_map.values())
    return _module_payload_cache


def load_metadata_payloads(detected_type: str | None) -> list[dict]:
    cache_key = detected_type or "__all__"
    if cache_key in _metadata_payload_cache:
        return _metadata_payload_cache[cache_key]

    if not has_qdrant_collection():
        payloads = load_metadata_payloads_from_graph(detected_type)
        _metadata_payload_cache[cache_key] = payloads
        return payloads

    must_conditions = [
        models.FieldCondition(
            key="chunk_type",
            match=models.MatchValue(value="metadata")
        )
    ]
    if detected_type:
        must_conditions.append(
            models.FieldCondition(
                key="object_type",
                match=models.MatchValue(value=detected_type)
            )
        )

    offset = None
    payloads: list[dict] = []
    while True:
        points, offset = qclient.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=models.Filter(must=must_conditions),
            limit=256,
            with_payload=True,
            with_vectors=False,
            offset=offset,
        )
        if not points:
            break

        for point in points:
            if point.payload:
                payloads.append(point.payload)

        if offset is None:
            break

    _metadata_payload_cache[cache_key] = payloads
    return payloads


def load_form_payloads() -> list[dict]:
    global _form_payload_cache
    if _form_payload_cache is not None:
        return _form_payload_cache

    if not has_qdrant_collection():
        _form_payload_cache = load_form_payloads_from_graph()
        return _form_payload_cache

    offset = None
    payloads: list[dict] = []
    form_filter = models.Filter(
        must=[
            models.FieldCondition(
                key="chunk_type",
                match=models.MatchValue(value="metadata_form")
            )
        ]
    )

    while True:
        points, offset = qclient.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=form_filter,
            limit=256,
            with_payload=True,
            with_vectors=False,
            offset=offset,
        )
        if not points:
            break

        for point in points:
            if point.payload:
                payloads.append(point.payload)

        if offset is None:
            break

    if not payloads:
        payloads = load_form_payloads_from_graph()

    _form_payload_cache = payloads
    return payloads


def load_code_signatures() -> list[dict]:
    global _code_signature_cache
    if _code_signature_cache is not None:
        return _code_signature_cache

    if not has_qdrant_collection():
        _code_signature_cache = load_code_signatures_from_graph()
        return _code_signature_cache

    offset = None
    signatures: list[dict] = []
    code_filter = models.Filter(
        must=[
            models.FieldCondition(
                key="chunk_type",
                match=models.MatchValue(value="code")
            )
        ]
    )

    while True:
        points, offset = qclient.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=code_filter,
            limit=256,
            with_payload=True,
            with_vectors=False,
            offset=offset,
        )
        if not points:
            break

        for point in points:
            payload = point.payload or {}
            signatures.append({
                "module_path": payload.get("module_path", ""),
                "module_type": payload.get("module_type", ""),
                "method_name": payload.get("method_name", ""),
                "file_path": payload.get("file_path", ""),
                "start_line": payload.get("start_line"),
                "end_line": payload.get("end_line"),
                "extension_annotation": payload.get("extension_annotation", ""),
                "extension_target_method": payload.get("extension_target_method", ""),
            })

        if offset is None:
            break

    _code_signature_cache = signatures
    return signatures


def lexical_metadata_results(query: str, detected_type: str | None):
    keywords = extract_keywords(query, COMMON_STOPWORDS)
    phrase = " ".join(keywords)
    structural_intent = has_structural_intent(query)
    navigation_intent = has_object_navigation_intent(query, detected_type)
    register_intent = has_register_intent(query, detected_type)
    ranked = []

    for payload in load_metadata_payloads(detected_type):
        object_name = payload.get("object_name", "")
        synonym = payload.get("synonym", "")
        document = payload.get("document", "")
        object_type = payload.get("object_type", "")

        score = 0.0
        score += score_keyword_overlap(object_name, keywords, phrase) * 2.2
        score += score_name_specificity(object_name, keywords, phrase) * 2.0
        score += score_keyword_overlap(synonym, keywords, phrase) * 1.6
        score += score_name_specificity(synonym, keywords, phrase) * 1.2
        score += score_keyword_overlap(document, keywords, phrase) * 0.4
        score += score_metadata_business_anchors(query, payload)
        score += score_shared_metadata_domain(query, payload)

        if detected_type and object_type == detected_type:
            score += 0.6

        if structural_intent:
            if object_type in STRUCTURE_PRIORITY_TYPES:
                score += 1.0
            elif object_type in STRUCTURE_LOW_PRIORITY_TYPES:
                score -= 0.8

        if navigation_intent and not detected_type:
            if object_type in PRIMARY_METADATA_TYPES:
                score += 1.2
            elif object_type in LOW_SIGNAL_METADATA_TYPES:
                score -= 1.8

        if register_intent:
            if object_type in REGISTER_METADATA_TYPES:
                score += 2.2
            elif object_type in {"Catalog", "Subsystem", "Role"}:
                score -= 1.4
            elif object_type in LOW_SIGNAL_METADATA_TYPES:
                score -= 2.2

        if score > 0:
            ranked.append((score, payload))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def normalize_alias_key(text: str) -> str:
    return " ".join(extract_keywords(text, COMMON_STOPWORDS))


def alias_tokens_match(left: str, right: str) -> bool:
    if left == right:
        return True

    if min(len(left), len(right)) < 5:
        return False

    return left.startswith(right) or right.startswith(left)


def count_alias_token_overlap(query_tokens: set[str], alias_tokens: set[str]) -> int:
    matched_query_tokens: set[str] = set()
    overlap = 0

    for alias_token in alias_tokens:
        for query_token in query_tokens:
            if query_token in matched_query_tokens:
                continue
            if alias_tokens_match(query_token, alias_token):
                matched_query_tokens.add(query_token)
                overlap += 1
                break

    return overlap


def build_semantic_lookup_queries(query: str, detected_type: str | None) -> list[str]:
    keywords = set(extract_keywords(query, COMMON_STOPWORDS))
    if not keywords:
        return []

    candidates = []
    active_patterns = list(GENERIC_SEMANTIC_LOOKUP_PATTERNS)
    if CONFIG_PROFILE.lower() == "unf":
        active_patterns.extend(UNF_SEMANTIC_LOOKUP_PATTERNS)
    for pattern in active_patterns:
        object_type = pattern["object_type"]
        if detected_type and object_type != detected_type:
            continue

        trigger_tokens = set(pattern["triggers"])
        overlap = count_alias_token_overlap(keywords, trigger_tokens)
        if overlap != len(trigger_tokens):
            continue

        for candidate in pattern["queries"]:
            if candidate not in candidates:
                candidates.append(candidate)

    for candidate in build_shared_domain_queries(query, "metadata", detected_type):
        if candidate not in candidates:
            candidates.append(candidate)

    return candidates


def lookup_metadata_results(query: str, detected_type: str | None):
    navigation_intent = has_object_navigation_intent(query, detected_type)
    structural_intent = has_structural_intent(query)
    register_intent = has_register_intent(query, detected_type)
    ranked_map: dict[tuple[str, str], tuple[float, dict]] = {}
    base_candidates = build_lookup_queries(query, COMMON_STOPWORDS)
    semantic_candidates = build_semantic_lookup_queries(query, detected_type)

    for candidate in base_candidates + semantic_candidates:
        for score, payload in rank_metadata_lookup(candidate, detected_type):
            object_type = payload.get("object_type", "")
            key = (payload.get("object_type", ""), payload.get("object_name", ""))
            current = ranked_map.get(key)
            boosted_score = score + 1.5
            boosted_score += score_metadata_business_anchors(query, payload)

            if candidate in semantic_candidates:
                boosted_score += 0.6

            if navigation_intent:
                if object_type in CORE_NAVIGATION_METADATA_TYPES:
                    boosted_score += 4.0
                elif object_type in PRIMARY_METADATA_TYPES:
                    boosted_score += 1.5
                elif object_type in LOW_SIGNAL_METADATA_TYPES:
                    boosted_score -= 2.0

            if structural_intent and object_type in CORE_NAVIGATION_METADATA_TYPES:
                boosted_score += 2.0

            if register_intent:
                if object_type in REGISTER_METADATA_TYPES:
                    boosted_score += 3.0
                elif object_type in {"Catalog", "Subsystem", "Role"}:
                    boosted_score -= 3.0
                elif object_type in LOW_SIGNAL_METADATA_TYPES:
                    boosted_score -= 2.5

            if current is None or boosted_score > current[0]:
                ranked_map[key] = (boosted_score, payload)

    ranked = list(ranked_map.values())
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def load_code_payload(signature: dict) -> dict | None:
    if not has_qdrant_collection():
        start_line = signature.get("start_line")
        if start_line is None:
            return signature

        node_id = make_method_node_id(
            signature.get("module_path", ""),
            signature.get("method_name", ""),
            int(start_line),
        )
        return graph_repository.get_node(node_id) or signature

    must_conditions = [
        models.FieldCondition(
            key="chunk_type",
            match=models.MatchValue(value="code")
        ),
        models.FieldCondition(
            key="file_path",
            match=models.MatchValue(value=signature.get("file_path"))
        ),
        models.FieldCondition(
            key="method_name",
            match=models.MatchValue(value=signature.get("method_name"))
        ),
    ]

    points, _ = qclient.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=models.Filter(must=must_conditions),
        limit=1,
        with_payload=True,
        with_vectors=False,
    )
    if not points:
        return None
    return points[0].payload or None


def score_validation_code_signals(payload: dict, query: str = "") -> float:
    module_path = payload.get("module_path", "")
    method_name = payload.get("method_name", "")
    document = payload.get("document", "")
    module_type = payload.get("module_type", "")

    score = 0.0
    method_text = normalize_text(method_name)
    module_text = normalize_text(module_path)
    document_text = normalize_text(document)
    query_text = normalize_text(query)
    all_text = f"{method_text} {module_text} {document_text}"

    if normalized_contains_any(method_name, BUSINESS_RULE_METHOD_PHRASES):
        score += 2.6
    if normalized_contains_any(document, BUSINESS_RULE_METHOD_PHRASES):
        score += 1.0

    if "обработка проведения" in method_text:
        score += 2.4
    if "перед проведением" in method_text or "проведение" in method_text:
        score += 1.2
    if "провер" in all_text:
        score += 1.0
    if "контрол" in all_text:
        score += 1.0
    if "отказ" in all_text:
        score += 1.1
    if "ошиб" in all_text or "исключение" in all_text:
        score += 0.8
    if "остат" in all_text:
        score += 0.8
    if "отриц" in all_text or "недостат" in all_text:
        score += 1.3
    if "остат" in query_text:
        if "остат" in all_text:
            score += 0.8
        else:
            score -= 0.9
    if "отриц" in query_text and ("отриц" in all_text or "недостат" in all_text):
        score += 0.7

    if module_type in {"Documents", "CommonModules", "AccumulationRegisters"}:
        score += 0.7
    elif module_text.startswith("documents "):
        score += 0.6
    elif module_text.startswith("data processors "):
        score -= 0.3

    if normalized_contains_any(method_name, UI_HANDLER_METHOD_PHRASES):
        score -= 3.0
    if "при изменении" in method_text or "при активизации строки" in method_text:
        score -= 1.5

    return score


def build_code_search_queries(query: str) -> list[str]:
    queries = build_retrieval_queries(query, CODE_STOPWORDS)
    for expansion in build_shared_domain_queries(query, "code"):
        if expansion not in queries:
            queries.append(expansion)

    if not has_validation_intent(query):
        return queries

    normalized = normalize_text(query)
    expansions = [
        "проверка бизнес правила проведение документа",
        "обработка проведения проверка отказ",
    ]
    if "остат" in normalized:
        expansions.extend([
            "контроль остатков при проведении документа",
            "проверка отрицательных остатков проведение",
            "недостаточно товаров проведение документа отказ",
        ])
    if "товар" in normalized and ("хвата" in normalized or "нельзя" in normalized or "провести" in normalized):
        expansions.extend([
            "контроль остатков товаров при проведении документа",
            "недостаточно товара проведение документа отказ",
            "проверка доступного остатка товара проведение",
        ])

    for expansion in expansions:
        if expansion not in queries:
            queries.append(expansion)
    return queries


def lexical_code_results(query: str):
    keywords = extract_keywords(query, CODE_STOPWORDS)
    phrase = " ".join(keywords)
    report_intent = has_report_intent(query)
    validation_intent = has_validation_intent(query)
    ranked = []

    for signature in load_code_signatures():
        module_path = signature.get("module_path", "")
        method_name = signature.get("method_name", "")
        module_type = signature.get("module_type", "")

        method_overlap = score_keyword_overlap(method_name, keywords, phrase)
        module_overlap = score_keyword_overlap(module_path, keywords, phrase)

        score = 0.0
        score += method_overlap * 2.0
        score += score_name_specificity(method_name, keywords, phrase) * 1.4
        score += module_overlap * 2.4
        score += score_name_specificity(module_path, keywords, phrase) * 1.4

        total_overlap = method_overlap + module_overlap
        if keywords and total_overlap < 0.25:
            continue

        if module_type in LOW_PRIORITY_CODE_MODULE_TYPES and not report_intent:
            score -= 1.8

        if validation_intent:
            score += score_validation_code_signals(signature, query)
        score += score_shared_code_domain(query, signature)

        if score > 0:
            ranked.append((score, signature))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def rerank_code_results(query: str, results):
    keywords = extract_keywords(query, CODE_STOPWORDS)
    phrase = " ".join(keywords)
    report_intent = has_report_intent(query)
    validation_intent = has_validation_intent(query)
    ranked = []

    for hit in results:
        payload = hit.payload or {}
        module_path = payload.get("module_path", "")
        method_name = payload.get("method_name", "")
        document = payload.get("document", "")
        module_type = payload.get("module_type", "")

        score = float(hit.score)
        method_overlap = score_keyword_overlap(method_name, keywords, phrase)
        module_overlap = score_keyword_overlap(module_path, keywords, phrase)
        document_overlap = score_keyword_overlap(document, keywords, phrase)

        score += method_overlap * 1.8
        score += score_name_specificity(method_name, keywords, phrase) * 1.3
        score += module_overlap * 1.5
        score += score_name_specificity(module_path, keywords, phrase) * 1.1
        score += document_overlap * 0.9

        total_overlap = method_overlap + module_overlap + document_overlap
        if keywords and total_overlap < 0.45:
            score -= 1.8
        elif keywords and total_overlap < 0.8:
            score -= 0.8

        if module_type in LOW_PRIORITY_CODE_MODULE_TYPES and not report_intent:
            score -= 1.6

        if validation_intent:
            score += score_validation_code_signals(payload, query)
        score += score_shared_code_domain(query, payload)

        ranked.append((score, hit))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def resolve_metadata_object_type(value: str | None) -> str | None:
    if not value:
        return None

    raw_value = value.strip()
    if not raw_value:
        return None

    if raw_value in METADATA_TYPE_PATTERNS:
        return raw_value

    return detect_metadata_object_type(raw_value) or raw_value


def score_identifier_match(text: str, query: str) -> float:
    if not text or not query:
        return 0.0

    query_tokens = match_tokens(query)
    text_tokens = match_tokens(text)
    if not query_tokens or not text_tokens:
        return 0.0

    query_phrase = " ".join(query_tokens)
    text_phrase = " ".join(text_tokens)
    query_compact = "".join(query_tokens)
    text_compact = "".join(text_tokens)
    score = 0.0

    if text_compact == query_compact:
        score += 5.0
    elif text_phrase == query_phrase:
        score += 4.0
    elif text_compact.startswith(query_compact) or query_compact.startswith(text_compact):
        score += 2.3
    elif query_phrase in text_phrase or text_phrase in query_phrase:
        score += 1.4

    score += score_keyword_overlap(text, query_tokens, query_phrase) * 1.8
    score += score_name_specificity(text, query_tokens, query_phrase) * 1.2
    return score


def iter_qdrant_points(query_filter: models.Filter, page_size: int = 256):
    offset = None
    while True:
        points, offset = qclient.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=query_filter,
            limit=page_size,
            with_payload=True,
            with_vectors=False,
            offset=offset,
        )
        if not points:
            break

        for point in points:
            yield point

        if offset is None:
            break


def format_metadata_lookup_result(payload: dict, score: float, index: int) -> str:
    return (
        f"### Результат {index} (Точность совпадения: {score:.4f})\n"
        f"**Объект:** {payload.get('object_type')}.{payload.get('object_name')} (Синоним: {payload.get('synonym')})\n"
        f"**Файл:** `{payload.get('file_path')}`\n"
        f"**Структура:**\n```text\n{payload.get('document', '')}\n```\n"
        f"{'-'*40}"
    )


def format_owner_label(owner_type: str, owner_name: str, owner_object_type: str = "") -> str:
    normalized_owner_type = owner_object_type or owner_type
    return f"{normalized_owner_type}.{owner_name}" if owner_name else (normalized_owner_type or "unknown")


def format_form_lookup_result(payload: dict, score: float, index: int) -> str:
    owner_name = payload.get("owner_name") or ""
    owner_type = payload.get("owner_type") or ""
    owner_object_type = payload.get("owner_object_type") or ""
    owner_label = format_owner_label(owner_type, owner_name, owner_object_type)
    return (
        f"### Результат {index} (Точность совпадения: {score:.4f})\n"
        f"**Форма:** `{payload.get('form_name')}`\n"
        f"**Владелец:** `{owner_label}`\n"
        f"**Тип XML:** `{payload.get('root_type')}`\n"
        f"**Файл:** `{payload.get('file_path')}`\n"
        f"**Описание:**\n```text\n{payload.get('document', '')}\n```\n"
        f"{'-'*40}"
    )


def format_code_lookup_result(payload: dict, score: float, index: int) -> str:
    return (
        f"### Результат {index} (Точность совпадения: {score:.4f})\n"
        f"**Модуль:** `{payload.get('module_path')}`\n"
        f"**Метод:** `{payload.get('method_name')}` (Строки: {payload.get('start_line')}-{payload.get('end_line')})\n"
        f"**Файл:** `{payload.get('file_path')}`\n"
        f"**Код:**\n```bsl\n{payload.get('document', '')}\n```\n"
        f"{'-'*40}"
    )


def format_module_lookup_result(payload: dict, score: float, index: int) -> str:
    method_count = payload.get("method_count")
    method_count_line = f"**Методов:** `{method_count}`\n" if method_count is not None else ""
    return (
        f"### Результат {index} (Точность совпадения: {score:.4f})\n"
        f"**Модуль:** `{payload.get('module_path')}`\n"
        f"**Тип модуля:** `{payload.get('module_type')}`\n"
        f"**Имя:** `{payload.get('module_name')}`\n"
        f"{method_count_line}"
        f"**Файл:** `{payload.get('file_path')}`\n"
        f"{'-'*40}"
    )


def rank_module_lookup(name: str, module_type: str | None = None) -> list[tuple[float, dict]]:
    normalized_module_type = module_type.strip() if module_type else ""
    ranked = []

    for payload in load_module_payloads():
        current_module_path = payload.get("module_path", "")
        current_module_name = payload.get("module_name", "") or infer_module_name(current_module_path)
        current_module_type = payload.get("module_type", "")

        score = score_identifier_match(current_module_path, name) * 1.8
        score += score_identifier_match(current_module_name, name) * 2.0

        if normalized_module_type:
            if current_module_type == normalized_module_type:
                score += 0.7
            else:
                score -= 0.3

        if current_module_path == name:
            score += 5.0
        elif current_module_name == name:
            score += 4.2

        if score >= 1.0:
            ranked.append((score, payload))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def rank_metadata_lookup(name: str, object_type: str | None) -> list[tuple[float, dict]]:
    resolved_type = resolve_metadata_object_type(object_type)
    ranked = []

    for payload in load_metadata_payloads(resolved_type):
        object_name = payload.get("object_name", "")
        synonym = payload.get("synonym", "")

        score = score_identifier_match(object_name, name) * 1.8
        score += score_identifier_match(synonym, name) * 1.4

        if resolved_type and payload.get("object_type") == resolved_type:
            score += 0.4

        if score >= 1.0:
            ranked.append((score, payload))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def rank_metadata_graph_lookup(name: str, object_type: str | None) -> list[tuple[float, dict]]:
    if not graph_repository.exists():
        return []

    resolved_type = resolve_metadata_object_type(object_type)
    if resolved_type:
        exact_node = graph_repository.get_node(make_metadata_node_id(resolved_type, name))
        if exact_node:
            return [(999.0, exact_node)]

    ranked: list[tuple[float, dict]] = []
    for node in graph_repository.iter_nodes("metadata"):
        object_name = node.get("object_name", "")
        synonym = node.get("synonym", "")

        if resolved_type and node.get("object_type") != resolved_type:
            continue

        score = score_identifier_match(object_name, name) * 1.8
        score += score_identifier_match(synonym, name) * 1.4

        if resolved_type and node.get("object_type") == resolved_type:
            score += 0.4

        if object_name == name:
            score += 4.5

        if score >= 1.0:
            ranked.append((score, node))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def rank_metadata_lookup_for_graph_tool(name: str, object_type: str | None) -> list[tuple[float, dict]]:
    ranked_graph = rank_metadata_graph_lookup(name, object_type)
    if graph_repository.exists():
        return ranked_graph
    return rank_metadata_lookup(name, object_type)


def rank_form_lookup(
    name: str,
    owner_name: str | None = None,
    owner_type: str | None = None,
) -> list[tuple[float, dict]]:
    normalized_owner_name = owner_name.strip() if owner_name else ""
    normalized_owner_type = resolve_metadata_object_type(owner_type) or (owner_type.strip() if owner_type else "")
    ranked = []

    for payload in load_form_payloads():
        current_form_name = payload.get("form_name", "")
        current_owner_name = payload.get("owner_name", "")
        current_owner_type = payload.get("owner_type", "")
        current_owner_object_type = payload.get("owner_object_type", "")

        score = score_identifier_match(current_form_name, name) * 2.2
        if normalized_owner_name:
            score += score_identifier_match(current_owner_name, normalized_owner_name) * 1.5
        if normalized_owner_type:
            score += max(
                score_identifier_match(current_owner_type, normalized_owner_type),
                score_identifier_match(current_owner_object_type, normalized_owner_type),
            ) * 1.2
            if current_owner_type == normalized_owner_type or current_owner_object_type == normalized_owner_type:
                score += 0.4

        if current_form_name == name:
            score += 4.5

        if score >= 1.2:
            ranked.append((score, payload))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def find_forms_by_owner(owner_name: str, owner_type: str | None) -> list[tuple[float, dict]]:
    normalized_owner_name = owner_name.strip()
    normalized_owner_type = resolve_metadata_object_type(owner_type) or (owner_type.strip() if owner_type else "")
    ranked = []

    for payload in load_form_payloads():
        current_owner_name = payload.get("owner_name", "")
        current_owner_type = payload.get("owner_type", "")
        current_owner_object_type = payload.get("owner_object_type", "")

        score = score_identifier_match(current_owner_name, normalized_owner_name) * 2.0
        if current_owner_name == normalized_owner_name:
            score += 4.0

        if normalized_owner_type:
            score += max(
                score_identifier_match(current_owner_type, normalized_owner_type),
                score_identifier_match(current_owner_object_type, normalized_owner_type),
            ) * 1.4
            if current_owner_type == normalized_owner_type or current_owner_object_type == normalized_owner_type:
                score += 0.6

        if score >= 2.0:
            ranked.append((score, payload))

    ranked.sort(key=lambda item: (item[0], item[1].get("form_name", "")), reverse=True)
    return ranked


def build_code_filter(method_name: str | None = None, module_type: str | None = None) -> models.Filter:
    must_conditions = [
        models.FieldCondition(
            key="chunk_type",
            match=models.MatchValue(value="code")
        )
    ]

    if method_name:
        must_conditions.append(
            models.FieldCondition(
                key="method_name",
                match=models.MatchValue(value=method_name)
            )
        )

    if module_type:
        must_conditions.append(
            models.FieldCondition(
                key="module_type",
                match=models.MatchValue(value=module_type)
            )
        )

    return models.Filter(must=must_conditions)


def rank_code_lookup(
    method_name: str,
    module_path: str | None,
    module_type: str | None,
    exact_method_name: bool,
) -> list[tuple[float, dict]]:
    ranked = []
    for payload in load_code_signatures():
        current_method_name = payload.get("method_name", "")
        current_module_path = payload.get("module_path", "")
        current_module_type = payload.get("module_type", "")

        score = score_identifier_match(current_method_name, method_name) * 2.2
        if module_path:
            score += score_identifier_match(current_module_path, module_path) * 1.5

        if module_type and current_module_type == module_type:
            score += 0.5

        if exact_method_name and current_method_name == method_name:
            score += 5.0

        if score >= 1.4:
            ranked.append((score, load_code_payload(payload) or payload))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def find_method_candidates(
    method_name: str,
    module_path: str | None,
    module_type: str | None,
    limit: int,
) -> list[tuple[float, dict]]:
    if not has_qdrant_collection():
        return []

    lookup_query = method_name if not module_path else f"{module_path} {method_name}"
    try:
        vector = get_query_embedding(lookup_query)
        fetch_limit = max(limit * 20, 100)
        code_filter = build_code_filter(module_type=module_type)

        response = qclient.query_points(
            collection_name=COLLECTION_NAME,
            query=vector,
            query_filter=code_filter,
            limit=fetch_limit
        )
    except Exception:
        return []

    ranked = []
    for hit in response.points:
        payload = hit.payload or {}
        current_method_name = payload.get("method_name", "")
        current_module_path = payload.get("module_path", "")
        current_module_type = payload.get("module_type", "")

        score = float(hit.score)
        score += score_identifier_match(current_method_name, method_name) * 2.5
        if module_path:
            score += score_identifier_match(current_module_path, module_path) * 1.6
        if module_type and current_module_type == module_type:
            score += 0.5
        if current_method_name == method_name:
            score += 5.0

        ranked.append((score, payload))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def format_timestamp(timestamp: float | None) -> str:
    if timestamp is None:
        return "unknown"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def load_index_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def make_metadata_node_id(object_type: str, object_name: str) -> str:
    return f"metadata:{object_type}.{object_name}"


def make_form_node_id(owner_object_type: str, owner_name: str, form_name: str) -> str:
    return f"form:{owner_object_type}.{owner_name}.{form_name}"


def make_module_node_id(module_path: str) -> str:
    return f"module:{module_path}"


def make_method_node_id(module_path: str, method_name: str, start_line: int) -> str:
    return f"method:{module_path}.{method_name}:{start_line}"


def resolve_method_payload(name: str, module_path: str = "", module_type: str = "") -> dict | None:
    normalized_module_path = module_path.strip() or None
    normalized_module_type = module_type.strip() or None

    ranked_results = rank_code_lookup(
        method_name=name,
        module_path=normalized_module_path,
        module_type=normalized_module_type,
        exact_method_name=True,
    )
    if ranked_results:
        return ranked_results[0][1]

    ranked_results = rank_code_lookup(
        method_name=name,
        module_path=normalized_module_path,
        module_type=normalized_module_type,
        exact_method_name=False,
    )
    if ranked_results:
        return ranked_results[0][1]

    if normalized_module_path or normalized_module_type:
        semantic_results = find_method_candidates(
            method_name=name,
            module_path=normalized_module_path,
            module_type=normalized_module_type,
            limit=5,
        )
        if semantic_results:
            return semantic_results[0][1]

    return None


def resolve_module_payload(name: str, module_type: str = "") -> dict | None:
    normalized_module_type = module_type.strip() or None
    ranked_results = rank_module_lookup(name, normalized_module_type)
    if ranked_results:
        return ranked_results[0][1]
    return None


def resolve_method_payload_for_graph_tool(
    name: str,
    module_path: str = "",
    module_type: str = "",
) -> dict | None:
    if graph_repository.exists():
        properties = {"method_name": name}
        if module_path.strip():
            properties["module_path"] = module_path.strip()
        if module_type.strip():
            properties["module_type"] = module_type.strip()

        graph_matches = list(graph_repository.find_nodes("method", properties, limit=5))
        if graph_matches:
            return graph_matches[0]
        return None

    return resolve_method_payload(name, module_path, module_type)


def collect_module_methods(module_payload: dict, limit: int = 200) -> list[dict]:
    module_path = module_payload.get("module_path", "")
    module_type = module_payload.get("module_type", "")

    methods: list[dict] = []
    if graph_repository.exists():
        module_id = make_module_node_id(module_path)
        for edge, target in graph_repository.iter_edges(module_id, "outgoing", "declares_method"):
            if not target:
                continue
            methods.append(target)
    else:
        for signature in load_code_signatures():
            if signature.get("module_path") != module_path:
                continue
            if module_type and signature.get("module_type") != module_type:
                continue
            payload = load_code_payload(signature) or signature
            methods.append(payload)

    methods.sort(
        key=lambda item: (
            int(item.get("start_line") or 0),
            str(item.get("method_name") or ""),
        )
    )
    return methods[: max(1, limit)]


def format_method_graph_line(target: dict, edge: dict | None = None) -> str:
    module_path = target.get("module_path", "unknown")
    method_name = target.get("method_name", "unknown")
    start_line = target.get("start_line", "?")
    end_line = target.get("end_line", "?")
    file_path = target.get("file_path", "unknown")
    details: list[str] = []

    if edge:
        if edge.get("resolution"):
            details.append(f"resolution `{edge.get('resolution')}`")
        if edge.get("source"):
            details.append(f"source `{edge.get('source')}`")
        if edge.get("via_module"):
            details.append(f"via `{edge.get('via_module')}`")
        if edge.get("namespace"):
            details.append(f"namespace `{edge.get('namespace')}`")

    suffix = f" [{', '.join(details)}]" if details else ""
    return (
        f"- `{module_path}` -> `{method_name}` "
        f"(строки {start_line}-{end_line}) -> `{file_path}`{suffix}"
    )


def collect_export_scan(cache: dict) -> dict:
    export_dir = Path(EXPORT_PATH)
    if not export_dir.exists():
        return {
            "exists": False,
            "root": str(export_dir),
            "indexed_bsl_files": 0,
            "indexed_xml_files": 0,
            "indexed_form_xml_files": 0,
            "changed_files": 0,
        }

    indexed_bsl_files = 0
    indexed_xml_files = 0
    indexed_form_xml_files = 0
    changed_files = 0

    for root, dirs, files in os.walk(export_dir):
        current_root = Path(root)
        dirs[:] = [directory for directory in dirs if directory != ".git"]
        if current_root != export_dir and "Configuration.xml" in files:
            dirs[:] = []
            continue
        for file in files:
            filepath = Path(root) / file
            if not (file.endswith(".bsl") or file.endswith(".xml")):
                continue

            if file in {"ConfigDumpInfo.xml", "Configuration.xml"}:
                continue

            if file.endswith(".xml") and "Templates" in filepath.parts:
                continue

            if file.endswith(".bsl"):
                indexed_bsl_files += 1
            elif "Forms" in filepath.parts:
                indexed_form_xml_files += 1
            else:
                indexed_xml_files += 1

            rel_path = str(filepath.relative_to(export_dir))
            try:
                mtime = filepath.stat().st_mtime
            except OSError:
                continue

            if cache.get(rel_path) != mtime:
                changed_files += 1

    return {
        "exists": True,
        "root": str(export_dir),
        "indexed_bsl_files": indexed_bsl_files,
        "indexed_xml_files": indexed_xml_files,
        "indexed_form_xml_files": indexed_form_xml_files,
        "changed_files": changed_files,
    }


def count_collection_points(chunk_type: str | None = None) -> int | None:
    try:
        count_filter = None
        if chunk_type:
            count_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key="chunk_type",
                        match=models.MatchValue(value=chunk_type)
                    )
                ]
            )

        response = qclient.count(
            collection_name=COLLECTION_NAME,
            count_filter=count_filter,
            exact=True,
        )
        return int(response.count)
    except Exception:
        return None


def extract_line_label(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("- "):
        return stripped[2:].split(" (", 1)[0].strip()
    if stripped.startswith("* "):
        return stripped[2:].split(" (", 1)[0].strip()
    if stripped.startswith("- [") and "] " in stripped:
        return stripped.split("] ", 1)[1].split(" (", 1)[0].strip()
    if stripped.startswith("Табличная часть:"):
        tail = stripped.split(":", 1)[1].strip()
        return tail.split(" (", 1)[0].strip()
    return stripped


def extract_metadata_dependencies(document: str) -> list[dict]:
    dependencies = []
    seen = set()
    current_section = "metadata"
    current_container = ""

    for raw_line in document.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue

        if stripped == "Реквизиты:":
            current_section = "attributes"
            current_container = ""
            continue
        if stripped.startswith("Табличная часть:"):
            current_section = "tabular_section"
            current_container = extract_line_label(stripped)
            continue
        if stripped == "Измерения:":
            current_section = "dimensions"
            current_container = ""
            continue
        if stripped == "Ресурсы:":
            current_section = "resources"
            current_container = ""
            continue

        source_name = extract_line_label(stripped)
        for ref_kind, ref_name in REFERENCE_RE.findall(stripped):
            target_type = REFERENCE_OBJECT_TYPES.get(ref_kind, ref_kind.removesuffix("Ref"))
            key = (current_section, current_container, source_name, target_type, ref_name)
            if key in seen:
                continue
            seen.add(key)
            dependencies.append({
                "section": current_section,
                "container": current_container,
                "source": source_name,
                "target_type": target_type,
                "target_name": ref_name,
                "raw_type": ref_kind,
                "line": stripped,
            })

    return dependencies


def clamp_workflow_limit(limit: int, default: int = 8, maximum: int = 50) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


def metadata_label(payload: dict) -> str:
    return f"{payload.get('object_type', 'unknown')}.{payload.get('object_name', 'unknown')}"


def method_label(payload: dict) -> str:
    module_path = payload.get("module_path", "unknown")
    method_name = payload.get("method_name", "unknown")
    return f"{module_path}.{method_name}"


def graph_node_label(payload: dict) -> str:
    kind = payload.get("kind", "node")
    if kind == "handler":
        return f"handler:{payload.get('handler_name', 'unknown')}"
    if kind == "command":
        return f"command:{payload.get('command_name', 'unknown')}"
    if kind == "form_element":
        return f"{payload.get('element_type', 'element')}:{payload.get('element_name', 'unknown')}"
    if kind == "form":
        return f"form:{payload.get('form_name', 'unknown')}"
    if kind == "module":
        return f"module:{payload.get('module_path', 'unknown')}"
    if kind == "metadata":
        return metadata_label(payload)
    if kind == "method":
        return method_label(payload)
    return str(payload.get("id") or kind)


def trim_block(text: str, max_lines: int = 60, max_chars: int = 6000) -> str:
    if not text:
        return ""

    lines = text.splitlines()
    trimmed = "\n".join(lines[:max_lines])
    if len(trimmed) > max_chars:
        trimmed = trimmed[:max_chars].rstrip()

    if len(lines) > max_lines or len(text) > len(trimmed):
        trimmed += "\n..."
    return trimmed


def collect_metadata_graph_context(payload: dict, limit: int) -> dict[str, list[str]]:
    context = {
        "dependencies": [],
        "forms": [],
        "modules": [],
        "metadata_usages": [],
        "code_usages": [],
    }
    if not graph_repository.exists():
        return context

    node_id = make_metadata_node_id(payload.get("object_type", ""), payload.get("object_name", ""))
    if not graph_repository.get_node(node_id):
        return context

    for edge, target in graph_repository.iter_edges(node_id, "outgoing"):
        if not target:
            continue
        edge_type = edge.get("type")
        if edge_type == "references_metadata":
            prefix = edge.get("section", "metadata")
            if edge.get("container"):
                prefix = f"{prefix}:{edge['container']}"
            context["dependencies"].append(
                f"- `{prefix}` -> `{edge.get('source', 'unknown')}` -> `{metadata_label(target)}`"
            )
        elif edge_type == "contains_form":
            owner_label = format_owner_label(
                target.get("owner_type", ""),
                target.get("owner_name", ""),
                target.get("owner_object_type", ""),
            )
            context["forms"].append(
                f"- `{target.get('form_name', 'unknown')}` ({target.get('root_type', 'unknown')}) "
                f"[elements={target.get('form_element_count', 0)}, commands={target.get('form_command_count', 0)}, "
                f"events={target.get('form_event_count', 0)}] "
                f"-> owner `{owner_label}` -> `{target.get('file_path', 'unknown')}`"
            )
        elif edge_type == "contains_module":
            context["modules"].append(
                f"- `{target.get('module_path', 'unknown')}` -> `{target.get('file_path', 'unknown')}`"
            )

    for edge, target in graph_repository.iter_edges(node_id, "incoming"):
        if not target:
            continue
        edge_type = edge.get("type")
        if edge_type == "references_metadata":
            prefix = edge.get("section", "metadata")
            if edge.get("container"):
                prefix = f"{prefix}:{edge['container']}"
            context["metadata_usages"].append(
                f"- `{metadata_label(target)}` -> `{prefix}` -> `{edge.get('source', 'unknown')}`"
            )
        elif edge_type == "uses_metadata":
            context["code_usages"].append(format_method_graph_line(target, edge))

    for key, values in context.items():
        context[key] = values[:limit]
    return context


def collect_method_graph_context(payload: dict, limit: int) -> dict[str, list[str]]:
    context = {"entrypoints": [], "callers": [], "callees": []}
    if not graph_repository.exists():
        return context

    start_line = int(payload.get("start_line") or 0)
    node_id = make_method_node_id(payload.get("module_path", ""), payload.get("method_name", ""), start_line)
    if not graph_repository.get_node(node_id):
        return context

    for edge, target in graph_repository.iter_edges(node_id, "incoming", "calls"):
        if target:
            context["callers"].append(format_method_graph_line(target, edge))

    for edge, target in graph_repository.iter_edges(node_id, "incoming", "implements_handler"):
        if target:
            details = []
            if edge.get("source"):
                details.append(f"source `{edge.get('source')}`")
            if edge.get("event"):
                details.append(f"event `{edge.get('event')}`")
            handler_id = str(target.get("id") or "")
            if handler_id:
                for source_edge, source_node in graph_repository.iter_edges(handler_id, "incoming", "handles_event"):
                    if source_node:
                        details.append(f"from `{graph_node_label(source_node)}`")
                        break
            suffix = f" [{', '.join(details)}]" if details else ""
            context["entrypoints"].append(f"- `{graph_node_label(target)}` -> `{method_label(payload)}`{suffix}")

    for edge, target in graph_repository.iter_edges(node_id, "outgoing", "calls"):
        if target:
            context["callees"].append(format_method_graph_line(target, edge))

    for key, values in context.items():
        context[key] = values[:limit]
    return context


def resolve_form_payload_for_report(name: str, owner_name: str = "", owner_type: str = "") -> dict | None:
    ranked_results = rank_form_lookup(
        name=name,
        owner_name=owner_name.strip() or None,
        owner_type=owner_type.strip() or None,
    )
    if ranked_results:
        return ranked_results[0][1]
    return None


def format_form_method_link(handler_node: dict, edge: dict) -> str:
    details = []
    if edge.get("event"):
        details.append(f"event `{edge.get('event')}`")
    if edge.get("source"):
        details.append(f"source `{edge.get('source')}`")
    suffix = f" [{', '.join(details)}]" if details else ""
    return f"`{graph_node_label(handler_node)}`{suffix}"


def collect_form_element_tree(root_elements: list[dict]) -> list[dict]:
    elements: list[dict] = []
    stack = list(root_elements)
    seen: set[str] = set()

    while stack:
        element = stack.pop(0)
        element_id = str(element.get("id") or "")
        if element_id and element_id in seen:
            continue
        if element_id:
            seen.add(element_id)
        elements.append(element)

        children = [
            child
            for _, child in graph_repository.iter_edges(element_id, "outgoing", "contains_child_element")
            if child
        ]
        children.sort(key=lambda item: (int(item.get("depth") or 0), item.get("element_name", "")))
        stack.extend(children)

    return elements


def form_element_flags(element: dict) -> str:
    flags = []
    if element.get("visible") is False:
        flags.append("hidden")
    if element.get("enabled") is False:
        flags.append("disabled")
    if element.get("read_only"):
        flags.append("read_only")
    return f" flags=`{', '.join(flags)}`" if flags else ""


def collect_form_structure_context(form_payload: dict, limit: int) -> dict[str, list[str]]:
    context = {
        "modules": [],
        "commands": [],
        "elements": [],
        "events": [],
        "handler_methods": [],
        "quality_hints": [],
    }
    if not graph_repository.exists():
        return context

    form_id = form_payload.get("id") or make_form_node_id(
        form_payload.get("owner_object_type", ""),
        form_payload.get("owner_name", ""),
        form_payload.get("form_name", ""),
    )
    form_node = graph_repository.get_node(form_id)
    if not form_node:
        return context

    commands: list[dict] = []
    top_level_elements: list[dict] = []
    orphan_command_names: set[str] = set()
    unresolved_handler_names: set[str] = set()

    for edge, target in graph_repository.iter_edges(form_id, "outgoing"):
        if not target:
            continue
        edge_type = edge.get("type")
        if edge_type == "has_form_module":
            context["modules"].append(
                f"- `{target.get('module_path', 'unknown')}` -> `{target.get('file_path', 'unknown')}`"
            )
        elif edge_type == "contains_command":
            commands.append(target)
        elif edge_type == "contains_element":
            top_level_elements.append(target)
        elif edge_type == "handles_event":
            event = edge.get("event") or target.get("event_name") or "unknown"
            source = edge.get("source") or target.get("source_name") or "form"
            context["events"].append(
                f"- form event `{event}` from `{source}` -> `{target.get('handler_name', 'unknown')}`"
            )
            if not any(graph_repository.iter_edges(target.get("id", ""), "outgoing", "implements_handler")):
                unresolved_handler_names.add(target.get("handler_name", "unknown"))

    for command in sorted(commands, key=lambda item: item.get("command_name", "")):
        command_name = command.get("command_name", "unknown")
        title = command.get("title") or ""
        action = command.get("action") or ""
        handler_labels = []
        for _, handler in graph_repository.iter_edges(command.get("id", ""), "outgoing", "handled_by"):
            if not handler:
                continue
            handler_labels.append(handler.get("handler_name", "unknown"))
            resolved_method = False
            for method_edge, method_node in graph_repository.iter_edges(handler.get("id", ""), "outgoing", "implements_handler"):
                if method_node:
                    resolved_method = True
                    context["handler_methods"].append(
                        f"- command `{command_name}` -> {format_form_method_link(handler, method_edge)} -> `{method_label(method_node)}`"
                    )
            if not resolved_method:
                unresolved_handler_names.add(handler.get("handler_name", "unknown"))
        handlers = ", ".join(f"`{item}`" for item in handler_labels) if handler_labels else "none"
        if not handler_labels and action:
            orphan_command_names.add(command_name)
        context["commands"].append(
            f"- `{command_name}` title=`{title}` action=`{action or 'none'}` handlers={handlers}"
        )

    all_elements = collect_form_element_tree(top_level_elements)

    for element in sorted(
        top_level_elements,
        key=lambda item: (int(item.get("depth") or 0), item.get("element_name", "")),
    ):
        element_id = element.get("id", "")
        child_count = sum(1 for _ in graph_repository.iter_edges(element_id, "outgoing", "contains_child_element"))
        command_labels = []
        event_labels = []
        for _, command in graph_repository.iter_edges(element_id, "outgoing", "invokes_command"):
            if command:
                command_labels.append(command.get("command_name", "unknown"))
        for event_edge, handler in graph_repository.iter_edges(element_id, "outgoing", "handles_event"):
            if not handler:
                continue
            event_name = event_edge.get("event") or handler.get("event_name") or "unknown"
            handler_name = handler.get("handler_name", "unknown")
            event_labels.append(f"{event_name}->{handler_name}")
            for method_edge, method_node in graph_repository.iter_edges(handler.get("id", ""), "outgoing", "implements_handler"):
                if method_node:
                    context["handler_methods"].append(
                        f"- element `{element.get('element_name', 'unknown')}` -> {format_form_method_link(handler, method_edge)} -> `{method_label(method_node)}`"
                    )

        commands_text = ", ".join(f"`{item}`" for item in command_labels) if command_labels else "none"
        events_text = ", ".join(f"`{item}`" for item in event_labels) if event_labels else "none"
        context["elements"].append(
            f"- `{element.get('element_name', 'unknown')}` type=`{element.get('element_type', '')}` "
            f"title=`{element.get('title', '')}` data_path=`{element.get('data_path', '')}` "
            f"children=`{child_count}` commands={commands_text} events={events_text}{form_element_flags(element)}"
        )

    duplicate_names: dict[str, int] = {}
    broken_command_elements: set[str] = set()
    passive_command_elements: set[str] = set()
    hidden_top_level = 0

    for element in all_elements:
        element_name = str(element.get("element_name") or "")
        if element_name:
            duplicate_names[element_name] = duplicate_names.get(element_name, 0) + 1

        element_id = str(element.get("id") or "")
        invokes_command = any(graph_repository.iter_edges(element_id, "outgoing", "invokes_command"))
        handles_event = any(graph_repository.iter_edges(element_id, "outgoing", "handles_event"))
        child_count = sum(1 for _ in graph_repository.iter_edges(element_id, "outgoing", "contains_child_element"))
        element_type = str(element.get("element_type") or "")
        command_ref = str(element.get("command_ref") or "")
        command_name = str(element.get("command_name") or "")

        if command_ref and not invokes_command:
            broken_command_elements.add(element_name or element_id or "unknown")
        if (
            ("button" in element_type.lower() or "command" in element_type.lower())
            and not invokes_command
            and not handles_event
            and child_count == 0
            and not command_name
        ):
            passive_command_elements.add(element_name or element_id or "unknown")
        if int(element.get("depth") or 0) <= 1 and element.get("visible") is False:
            hidden_top_level += 1

    if not context["commands"]:
        context["quality_hints"].append("- No form commands found in graph projection.")
    if not context["elements"]:
        context["quality_hints"].append("- No top-level form elements found in graph projection.")
    if int(form_payload.get("form_element_count") or 0) >= 300:
        context["quality_hints"].append(
            f"- Large form: `{form_payload.get('form_element_count')}` elements. Consider reviewing navigation groups and command placement."
        )
    if int(form_payload.get("form_command_count") or 0) >= 100:
        context["quality_hints"].append(
            f"- Command-heavy form: `{form_payload.get('form_command_count')}` commands. Check for overloaded command bars."
        )
    if int(form_payload.get("form_event_count") or 0) >= 100:
        context["quality_hints"].append(
            f"- Event-heavy form: `{form_payload.get('form_event_count')}` form-level events. Review lifecycle handler complexity."
        )
    duplicate_items = [name for name, count in duplicate_names.items() if count > 1]
    for name in sorted(duplicate_items)[:10]:
        context["quality_hints"].append(f"- Duplicate element name `{name}` appears `{duplicate_names[name]}` times.")
    for element_name in sorted(broken_command_elements)[:10]:
        context["quality_hints"].append(f"- Element `{element_name}` has command reference but no resolved command node.")
    for element_name in sorted(passive_command_elements)[:10]:
        context["quality_hints"].append(f"- Command-like element `{element_name}` has no command or event handler link.")
    if hidden_top_level:
        context["quality_hints"].append(f"- Hidden top-level elements: `{hidden_top_level}`.")
    for command_name in sorted(orphan_command_names):
        context["quality_hints"].append(f"- Command `{command_name}` has action text but no resolved handler node.")
    for handler_name in sorted(unresolved_handler_names)[:20]:
        context["quality_hints"].append(f"- Handler `{handler_name}` is referenced but no method implementation was resolved.")

    for key, values in context.items():
        context[key] = values[:limit]
    return context


def append_section(lines: list[str], title: str, items: list[str], empty_text: str = "- none") -> None:
    lines.extend(["", f"## {title}", ""])
    if items:
        lines.extend(items)
    else:
        lines.append(empty_text)


def format_metadata_candidate(result: dict, index: int, include_document: bool = False) -> list[str]:
    payload = result["payload"]
    lines = [
        f"### Metadata {index}",
        f"- Object: `{metadata_label(payload)}`",
        f"- Synonym: `{payload.get('synonym') or ''}`",
        f"- File: `{payload.get('file_path')}`",
        f"- Score: `{result.get('final_score', 0):.4f}`",
        f"- Source: `{result.get('best_source', 'unknown')}`",
    ]
    if include_document:
        document = trim_block(payload.get("document", ""), max_lines=50)
        if document:
            lines.extend(["", "```text", document, "```"])
    return lines


def format_code_candidate(result: dict, index: int, include_snippet: bool = False) -> list[str]:
    payload = result["payload"]
    lines = [
        f"### Code {index}",
        f"- Method: `{method_label(payload)}`",
        f"- Lines: `{payload.get('start_line', '?')}-{payload.get('end_line', '?')}`",
        f"- File: `{payload.get('file_path')}`",
        f"- Score: `{result.get('final_score', 0):.4f}`",
        f"- Source: `{result.get('best_source', 'unknown')}`",
    ]
    if include_snippet:
        document = trim_block(payload.get("document", ""), max_lines=80)
        if document:
            lines.extend(["", "```bsl", document, "```"])
    return lines


def unique_metadata_explain_results(results: list[dict], limit: int) -> list[dict]:
    unique: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for result in results:
        payload = result.get("payload") or {}
        key = (payload.get("object_type", ""), payload.get("object_name", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(result)
        if len(unique) >= limit:
            break
    return unique


def unique_code_explain_results(results: list[dict], limit: int) -> list[dict]:
    unique: list[dict] = []
    seen: set[tuple] = set()
    for result in results:
        payload = result.get("payload") or {}
        key = code_result_key(payload)
        if key in seen:
            continue
        seen.add(key)
        unique.append(result)
        if len(unique) >= limit:
            break
    return unique


def find_exact_code_payload(method_name: str, module_path: str = "", module_type: str = "") -> dict | None:
    if not has_qdrant_collection():
        return None

    must_conditions = [
        models.FieldCondition(
            key="chunk_type",
            match=models.MatchValue(value="code"),
        ),
        models.FieldCondition(
            key="method_name",
            match=models.MatchValue(value=method_name),
        ),
    ]
    if module_path.strip():
        must_conditions.append(
            models.FieldCondition(
                key="module_path",
                match=models.MatchValue(value=module_path.strip()),
            )
        )
    if module_type.strip():
        must_conditions.append(
            models.FieldCondition(
                key="module_type",
                match=models.MatchValue(value=module_type.strip()),
            )
        )

    try:
        points, _ = qclient.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=models.Filter(must=must_conditions),
            limit=20,
            with_payload=True,
            with_vectors=False,
        )
    except Exception:
        return None

    for point in points:
        payload = point.payload or {}
        if module_path.strip() and payload.get("module_path") != module_path.strip():
            continue
        if module_type.strip() and payload.get("module_type") != module_type.strip():
            continue
        return payload
    return None


def resolve_workflow_method_payload(name: str, module_path: str = "", module_type: str = "") -> dict | None:
    method_payload = resolve_method_payload_for_graph_tool(name, module_path, module_type)
    if method_payload:
        return method_payload
    exact_payload = find_exact_code_payload(name, module_path, module_type)
    if exact_payload:
        return exact_payload
    return resolve_method_payload(name, module_path, module_type)


def ensure_query_runtime():
    global encoder, openai_client, embedding_runtime_ready
    if embedding_runtime_ready:
        return

    if USE_OPENAI_EMBEDDINGS:
        if not OPENAI_API_KEY:
            raise RuntimeError(
                "EMBEDDING_PROVIDER=openai требует OPENAI_API_KEY. "
                "Для локальной модели установите EMBEDDING_PROVIDER=local."
            )
        from openai import OpenAI

        openai_client = OpenAI(api_key=OPENAI_API_KEY)
        print("MCP-сервер: Запуск с поддержкой эмбеддингов OpenAI.")
        embedding_runtime_ready = True
        return

    from fastembed import TextEmbedding

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

    print(f"MCP-сервер: Загрузка локальной модели FastEmbed: {EMBEDDING_MODEL}...")
    encoder = TextEmbedding(model_name=EMBEDDING_MODEL, **model_kwargs)
    print("MCP-сервер: Запуск с локальными эмбеддингами FastEmbed.")
    embedding_runtime_ready = True


def get_query_embedding(text: str):
    """
    Генерирует вектор для поискового запроса через OpenAI или FastEmbed
    """
    ensure_query_runtime()
    if USE_OPENAI_EMBEDDINGS:
        response = openai_client.embeddings.create(
            input=[text],
            model=OPENAI_EMBEDDING_MODEL
        )
        return response.data[0].embedding
    else:
        return list(encoder.embed([text]))[0].tolist()

@mcp.tool()
def search_code(query: str, limit: int = 5) -> str:
    """
    Семантический поиск по исходному коду BSL (процедурам и функциям 1С).
    Пример запроса: "как записать метку RFID" или "заполнение контрагента".
    """
    try:
        code_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="chunk_type",
                    match=models.MatchValue(value="code")
                )
            ]
        )

        fetch_limit = max(limit * 30, 150)
        vector_points = []
        for lookup_query in build_code_search_queries(query):
            vector = get_query_embedding(lookup_query)
            response = qclient.query_points(
                collection_name=COLLECTION_NAME,
                query=vector,
                query_filter=code_filter,
                limit=fetch_limit
            )
            vector_points.extend(response.points)

        ranked_results = rerank_code_results(query, vector_points)
        lexical_results = lexical_code_results(query)

        merged_map: dict[tuple, tuple[float, float | None, dict]] = {}

        for rank_score, hit in ranked_results:
            payload = hit.payload or {}
            key = (payload.get("file_path"), payload.get("method_name"), payload.get("start_line"))
            current = merged_map.get(key)
            candidate = (rank_score, float(hit.score), payload)
            if current is None or rank_score > current[0]:
                merged_map[key] = candidate

        for rank_score, signature in lexical_results[: max(limit * 5, 20)]:
            payload = load_code_payload(signature)
            if not payload:
                continue
            key = (payload.get("file_path"), payload.get("method_name"), payload.get("start_line"))
            current = merged_map.get(key)
            candidate = (rank_score, None, payload)
            if current is None or rank_score > current[0]:
                merged_map[key] = candidate

        merged_results = sorted(merged_map.values(), key=lambda item: item[0], reverse=True)

        if not merged_results:
            return "Ничего не найдено по вашему запросу в коде."

        formatted_results = []
        for i, (rank_score, vector_score, payload) in enumerate(merged_results[:limit]):
            doc_text = payload.get("document", "")
            score_line = f"Релевантность: {rank_score:.4f}"
            if vector_score is not None:
                score_line += f", Векторное сходство: {vector_score:.4f}"
            formatted_results.append(
                f"### Результат {i+1} ({score_line})\n"
                f"**Модуль:** `{payload.get('module_path')}`\n"
                f"**Метод:** `{payload.get('method_name')}` (Строки: {payload.get('start_line')}-{payload.get('end_line')})\n"
                f"**Файл:** `{payload.get('file_path')}`\n"
                f"**Код:**\n```bsl\n{doc_text}\n```\n"
                f"{'-'*40}"
            )

        return "\n\n".join(formatted_results)

    except Exception as e:
        return f"Ошибка при поиске в коде: {str(e)}"

@mcp.tool()
def search_metadata(query: str, limit: int = 5) -> str:
    """
    Семантический поиск по описаниям метаданных 1С (справочникам, документам, регистрам).
    Помогает узнать структуру реквизитов и табличных частей объектов.
    Пример запроса: "структура справочника номенклатура" или "реквизиты заказа покупателя".
    """
    try:
        detected_type = detect_metadata_object_type(query)

        # Фильтр для поиска только по метаданным XML
        must_conditions = [
            models.FieldCondition(
                key="chunk_type",
                match=models.MatchValue(value="metadata")
            )
        ]
        if detected_type:
            must_conditions.append(
                models.FieldCondition(
                    key="object_type",
                    match=models.MatchValue(value=detected_type)
                )
            )

        meta_filter = models.Filter(
            must=must_conditions
        )

        fetch_limit = max(limit * 20, 80)

        # Выполняем поиск через современный API query_points.
        # Для длинных разговорных запросов дополнительно ищем по смысловому ядру.
        vector_points = []
        for lookup_query in build_retrieval_queries(query, COMMON_STOPWORDS):
            vector = get_query_embedding(lookup_query)
            response = qclient.query_points(
                collection_name=COLLECTION_NAME,
                query=vector,
                query_filter=meta_filter,
                limit=fetch_limit
            )
            vector_points.extend(response.points)

        ranked_results = rerank_metadata_results(query, vector_points, detected_type)
        lexical_results = lexical_metadata_results(query, detected_type)
        lookup_results = lookup_metadata_results(query, detected_type)

        merged_map: dict[str, tuple[float, float | None, dict]] = {}

        for rank_score, hit in ranked_results:
            payload = hit.payload or {}
            file_path = payload.get("file_path")
            current = merged_map.get(file_path)
            candidate = (rank_score, float(hit.score), payload)
            if current is None or rank_score > current[0]:
                merged_map[file_path] = candidate

        for rank_score, payload in lexical_results:
            file_path = payload.get("file_path")
            current = merged_map.get(file_path)
            candidate = (rank_score, None, payload)
            if current is None or rank_score > current[0]:
                merged_map[file_path] = candidate

        for rank_score, payload in lookup_results:
            file_path = payload.get("file_path")
            current = merged_map.get(file_path)
            candidate = (rank_score, None, payload)
            if current is None or rank_score > current[0]:
                merged_map[file_path] = candidate

        merged_results = sorted(merged_map.values(), key=lambda item: item[0], reverse=True)

        if not merged_results:
            return "Ничего не найдено по вашему запросу в метаданных."

        formatted_results = []
        for i, (rank_score, vector_score, payload) in enumerate(merged_results[:limit]):
            doc_text = payload.get("document", "")
            score_line = f"Релевантность: {rank_score:.4f}"
            if vector_score is not None:
                score_line += f", Векторное сходство: {vector_score:.4f}"
            formatted_results.append(
                f"### Результат {i+1} ({score_line})\n"
                f"**Объект:** {payload.get('object_type')}.{payload.get('object_name')} (Синоним: {payload.get('synonym')})\n"
                f"**Файл:** `{payload.get('file_path')}`\n"
                f"**Структура реквизитов:**\n```text\n{doc_text}\n```\n"
                f"{'-'*40}"
            )

        return "\n\n".join(formatted_results)

    except Exception as e:
        return f"Ошибка при поиске в метаданных: {str(e)}"

@mcp.tool()
def explain_search_result(query: str, target: str = "auto", limit: int = 3) -> str:
    """
    Диагностика поиска: показывает retrieval-запросы и score breakdown
    для metadata/code результатов.
    """
    try:
        normalized_target = (target or "auto").strip().lower()
        if normalized_target not in {"auto", "metadata", "code"}:
            return "Параметр target должен быть `auto`, `metadata` или `code`."

        lines = [
            "## Explain Search Result",
            "",
            f"- Query: `{query}`",
            f"- Target: `{normalized_target}`",
            f"- Limit: `{limit}`",
        ]

        if normalized_target in {"auto", "metadata"}:
            meta_info, meta_results = collect_metadata_explanations(query, max(1, limit))
            lines.extend([
                "",
                "## Metadata Diagnostics",
                "",
                f"- Detected type: `{meta_info['detected_type'] or 'none'}`",
                f"- Structural intent: `{meta_info['structural_intent']}`",
                f"- Navigation intent: `{meta_info['navigation_intent']}`",
                f"- Register intent: `{meta_info['register_intent']}`",
                f"- Retrieval queries: `{meta_info['retrieval_queries']}`",
                f"- Lookup queries: `{meta_info['lookup_queries']}`",
                f"- Semantic queries: `{meta_info['semantic_queries']}`",
            ])

            if not meta_results:
                lines.extend(["", "- Results: none"])
            else:
                for index, result in enumerate(meta_results, start=1):
                    payload = result["payload"]
                    source_scores = ", ".join(
                        f"{item['source']}={item['final_score']:.3f}" for item in result["sources"]
                    )
                    lines.extend([
                        "",
                        f"### Result {index}",
                        f"- Object: `{payload.get('object_type')}.{payload.get('object_name')}`",
                        f"- File: `{payload.get('file_path')}`",
                        f"- Final score: `{result['final_score']:.4f}`",
                        f"- Best source: `{result['best_source']}`",
                        f"- Best matched query: `{result['best_query'] or 'n/a'}`",
                        f"- Source scores: `{source_scores}`",
                        f"- Score components: `{format_component_summary(result['best_components'])}`",
                    ])

        if normalized_target in {"auto", "code"}:
            code_info, code_results = collect_code_explanations(query, max(1, limit))
            lines.extend([
                "",
                "## Code Diagnostics",
                "",
                f"- Report intent: `{code_info['report_intent']}`",
                f"- Validation intent: `{code_info['validation_intent']}`",
                f"- Retrieval queries: `{code_info['retrieval_queries']}`",
            ])

            if not code_results:
                lines.extend(["", "- Results: none"])
            else:
                for index, result in enumerate(code_results, start=1):
                    payload = result["payload"]
                    source_scores = ", ".join(
                        f"{item['source']}={item['final_score']:.3f}" for item in result["sources"]
                    )
                    lines.extend([
                        "",
                        f"### Result {index}",
                        f"- Module: `{payload.get('module_path')}`",
                        f"- Method: `{payload.get('method_name')}`",
                        f"- File: `{payload.get('file_path')}`",
                        f"- Final score: `{result['final_score']:.4f}`",
                        f"- Best source: `{result['best_source']}`",
                        f"- Best matched query: `{result['best_query'] or 'n/a'}`",
                        f"- Source scores: `{source_scores}`",
                        f"- Score components: `{format_component_summary(result['best_components'])}`",
                    ])

        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка при объяснении результатов поиска: {str(e)}"


@mcp.tool()
def find_metadata_object(name: str, object_type: str = "", limit: int = 5) -> str:
    """
    Точный или почти точный поиск объекта метаданных по имени.
    Лучше подходит для поиска конкретного объекта, чем общий семантический запрос.
    """
    try:
        ranked_results = rank_metadata_lookup(name, object_type)
        if not ranked_results:
            if object_type.strip():
                return f"Объект метаданных `{object_type}.{name}` не найден."
            return f"Объект метаданных `{name}` не найден."

        formatted_results = [
            format_metadata_lookup_result(payload, score, index + 1)
            for index, (score, payload) in enumerate(ranked_results[:limit])
        ]
        return "\n\n".join(formatted_results)
    except Exception as e:
        return f"Ошибка при точном поиске объекта метаданных: {str(e)}"


@mcp.tool()
def find_form(name: str, owner_name: str = "", owner_type: str = "", limit: int = 5) -> str:
    """
    Точный или почти точный поиск формы 1С по имени.
    Можно сузить поиск по владельцу формы и типу владельца.
    """
    try:
        ranked_results = rank_form_lookup(
            name=name,
            owner_name=owner_name or None,
            owner_type=owner_type or None,
        )
        if not ranked_results:
            return f"Форма `{name}` не найдена."

        formatted_results = [
            format_form_lookup_result(payload, score, index + 1)
            for index, (score, payload) in enumerate(ranked_results[:limit])
        ]
        return "\n\n".join(formatted_results)
    except Exception as e:
        return f"Ошибка при точном поиске формы: {str(e)}"


@mcp.tool()
def form_structure_report(name: str, owner_name: str = "", owner_type: str = "", limit: int = 30) -> str:
    """
    Показывает структуру формы из graph projection: модуль формы, команды,
    верхнеуровневые элементы, события и привязанные методы-обработчики.
    """
    try:
        safe_limit = clamp_workflow_limit(limit, default=30, maximum=120)
        form_payload = resolve_form_payload_for_report(name, owner_name, owner_type)
        if not form_payload:
            return f"Форма `{name}` не найдена."

        owner_label = format_owner_label(
            form_payload.get("owner_type", ""),
            form_payload.get("owner_name", ""),
            form_payload.get("owner_object_type", ""),
        )
        form_context = collect_form_structure_context(form_payload, safe_limit)

        lines = [
            "## Form Structure Report",
            "",
            f"- Config: `{CONFIG_ID}`",
            f"- Form: `{form_payload.get('form_name', name)}`",
            f"- Owner: `{owner_label}`",
            f"- XML type: `{form_payload.get('root_type', 'unknown')}`",
            f"- File: `{form_payload.get('file_path', 'unknown')}`",
            f"- Graph source: `{graph_repository.get_source_label()}`",
            f"- Elements total: `{form_payload.get('form_element_count', 'unknown')}`",
            f"- Commands total: `{form_payload.get('form_command_count', 'unknown')}`",
            f"- Form events total: `{form_payload.get('form_event_count', 'unknown')}`",
        ]

        if not graph_repository.exists():
            lines.extend([
                "",
                "## Graph Status",
                "",
                "- GraphRepository недоступен. Детальный отчет по командам, элементам и обработчикам невозможен.",
            ])
            document = trim_block(form_payload.get("document", ""), max_lines=80)
            if document:
                lines.extend(["", "## Indexed Form Card", "", "```text", document, "```"])
            return "\n".join(lines)

        append_section(lines, "Form Module", form_context["modules"])
        append_section(lines, "Commands", form_context["commands"])
        append_section(lines, "Top-Level Elements", form_context["elements"])
        append_section(lines, "Form Events", form_context["events"])
        append_section(lines, "Resolved Handler Methods", form_context["handler_methods"])
        append_section(lines, "Quality Hints", form_context["quality_hints"], empty_text="- no obvious form-structure issues detected")

        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка при построении отчета по форме: {str(e)}"


@mcp.tool()
def search_modules(query: str, limit: int = 5) -> str:
    """
    Ищет BSL-модули по назначению, имени и списку объявленных методов.
    Использует module-summary chunks, если они проиндексированы в Qdrant.
    """
    try:
        safe_limit = clamp_workflow_limit(limit, default=5, maximum=20)
        keywords = extract_keywords(query, CODE_STOPWORDS)
        phrase = " ".join(keywords)
        ranked_by_path: dict[str, tuple[float, float | None, dict]] = {}

        for score, payload in rank_module_lookup(query)[: max(20, safe_limit * 5)]:
            module_path = payload.get("module_path", "")
            if module_path:
                ranked_by_path[module_path] = (score, None, payload)

        if has_qdrant_collection():
            vector = get_query_embedding(query)
            summary_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key="chunk_type",
                        match=models.MatchValue(value="code_module_summary"),
                    )
                ]
            )
            response = qclient.query_points(
                collection_name=COLLECTION_NAME,
                query=vector,
                query_filter=summary_filter,
                limit=max(50, safe_limit * 10),
            )
            for hit in response.points:
                payload = hit.payload or {}
                module_path = payload.get("module_path", "")
                if not module_path:
                    continue
                score = float(hit.score)
                score += score_identifier_match(module_path, query) * 1.4
                score += score_identifier_match(payload.get("module_name", ""), query) * 1.6
                score += score_keyword_overlap(payload.get("document", ""), keywords, phrase) * 0.8
                current = ranked_by_path.get(module_path)
                if current is None or score > current[0]:
                    ranked_by_path[module_path] = (score, float(hit.score), payload)

        ranked = sorted(ranked_by_path.values(), key=lambda item: item[0], reverse=True)
        if not ranked:
            return f"Модули по запросу `{query}` не найдены."

        lines = [
            "## Module Search",
            "",
            f"- Query: `{query}`",
            f"- Config: `{CONFIG_ID}`",
            f"- Results: `{min(len(ranked), safe_limit)}`",
        ]
        for index, (score, vector_score, payload) in enumerate(ranked[:safe_limit], start=1):
            score_suffix = f", vector={vector_score:.4f}" if vector_score is not None else ""
            method_names = payload.get("method_names") or []
            methods_preview = ", ".join(str(name) for name in method_names[:12]) or "not indexed"
            lines.extend([
                "",
                f"### Module {index}",
                f"- Module: `{payload.get('module_path', 'unknown')}`",
                f"- Type: `{payload.get('module_type', 'unknown')}`",
                f"- Methods: `{payload.get('method_count', 'unknown')}`",
                f"- Score: `{score:.4f}{score_suffix}`",
                f"- Method preview: `{methods_preview}`",
                f"- File: `{payload.get('file_path', 'unknown')}`",
            ])
        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка при поиске модулей: {str(e)}"


def enrich_command_from_graph(command: dict) -> dict:
    command_id = command.get("id", "")
    if not command_id or not graph_repository.exists():
        return command

    for _, form in graph_repository.iter_edges(command_id, "incoming", "contains_command"):
        if not form:
            continue
        return {
            **command,
            "form_name": form.get("form_name", command.get("form_name", "")),
            "owner_type": form.get("owner_type", ""),
            "owner_object_type": form.get("owner_object_type", ""),
            "owner_name": form.get("owner_name", ""),
            "file_path": form.get("file_path", command.get("file_path", "")),
        }
    return command


def score_command_candidate(query: str, payload: dict, vector_score: float = 0.0) -> float:
    keywords = extract_keywords(query, COMMON_STOPWORDS)
    phrase = " ".join(keywords)
    score = vector_score
    score += score_identifier_match(payload.get("command_name", ""), query) * 1.8
    score += score_identifier_match(payload.get("action", ""), query) * 1.4
    score += score_keyword_overlap(payload.get("title", ""), keywords, phrase) * 1.2
    score += score_keyword_overlap(payload.get("tooltip", ""), keywords, phrase) * 0.8
    score += score_keyword_overlap(payload.get("form_name", ""), keywords, phrase) * 0.9
    score += score_keyword_overlap(payload.get("document", ""), keywords, phrase) * 0.5
    return score


@mcp.tool()
def search_commands(query: str, limit: int = 5) -> str:
    """
    Ищет команды управляемых форм по имени, заголовку, подсказке,
    обработчику и форме-владельцу.
    """
    try:
        safe_limit = clamp_workflow_limit(limit, default=5, maximum=30)
        ranked_by_key: dict[tuple[str, str, str], tuple[float, float | None, dict]] = {}
        qdrant_available = has_qdrant_collection()

        if not qdrant_available and graph_repository.exists():
            for command in graph_repository.iter_nodes("command"):
                score = score_command_candidate(query, command)
                if score <= 0:
                    continue
                key = (
                    command.get("id", ""),
                    "",
                    "",
                )
                ranked_by_key[key] = (score, None, command)

        if qdrant_available:
            vector = get_query_embedding(query)
            command_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key="chunk_type",
                        match=models.MatchValue(value="metadata_command"),
                    )
                ]
            )
            response = qclient.query_points(
                collection_name=COLLECTION_NAME,
                query=vector,
                query_filter=command_filter,
                limit=max(50, safe_limit * 10),
            )
            for hit in response.points:
                payload = hit.payload or {}
                score = score_command_candidate(query, payload, float(hit.score))
                key = (
                    payload.get("owner_name", ""),
                    payload.get("form_name", ""),
                    payload.get("command_name", ""),
                )
                current = ranked_by_key.get(key)
                if current is None or score > current[0]:
                    ranked_by_key[key] = (score, float(hit.score), payload)

        ranked = sorted(ranked_by_key.values(), key=lambda item: item[0], reverse=True)
        if not ranked:
            return f"Команды по запросу `{query}` не найдены."

        lines = [
            "## Command Search",
            "",
            f"- Query: `{query}`",
            f"- Config: `{CONFIG_ID}`",
            f"- Results: `{min(len(ranked), safe_limit)}`",
        ]
        for index, (score, vector_score, payload) in enumerate(ranked[:safe_limit], start=1):
            payload = enrich_command_from_graph(payload)
            owner_label = format_owner_label(
                payload.get("owner_type", ""),
                payload.get("owner_name", ""),
                payload.get("owner_object_type", ""),
            )
            vector_line = f"; vector={vector_score:.4f}" if vector_score is not None else ""
            lines.extend([
                "",
                f"### Command {index}",
                f"- Command: `{payload.get('command_name', 'unknown')}`",
                f"- Title: `{payload.get('title', '')}`",
                f"- Action: `{payload.get('action', '')}`",
                f"- Form: `{owner_label}.{payload.get('form_name', 'unknown')}`",
                f"- Score: `{score:.4f}{vector_line}`",
                f"- File: `{payload.get('file_path', 'unknown')}`",
            ])
        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка при поиске команд формы: {str(e)}"


@lru_cache(maxsize=8)
def load_event_subscription_payloads(export_path: str) -> tuple[dict, ...]:
    subscriptions_dir = Path(export_path) / "EventSubscriptions"
    if not subscriptions_dir.is_dir():
        return ()

    payloads = []
    for filepath in sorted(subscriptions_dir.glob("*.xml")):
        subscription = parse_event_subscription_xml(filepath)
        if not subscription:
            continue
        payloads.append({
            "document": subscription["card_text"],
            "subscription_name": subscription["name"],
            "synonym": subscription["synonym"],
            "event": subscription["event"],
            "handler": subscription["handler"],
            "handler_module": subscription["handler_module"],
            "handler_method": subscription["handler_method"],
            "sources": subscription["sources"],
            "file_path": str(filepath),
            "chunk_type": "metadata_event_subscription",
        })
    return tuple(payloads)


def score_event_subscription_candidate(query: str, payload: dict, vector_score: float = 0.0) -> float:
    keywords = extract_keywords(query, COMMON_STOPWORDS)
    phrase = " ".join(keywords)
    score = vector_score
    score += score_identifier_match(payload.get("subscription_name", ""), query) * 1.8
    score += score_identifier_match(payload.get("handler", ""), query) * 1.5
    score += score_identifier_match(payload.get("event", ""), query) * 1.2
    score += score_keyword_overlap(payload.get("synonym", ""), keywords, phrase) * 1.2
    score += score_keyword_overlap(" ".join(payload.get("sources") or []), keywords, phrase)
    score += score_keyword_overlap(payload.get("document", ""), keywords, phrase) * 0.5
    return score


@mcp.tool()
def search_event_subscriptions(query: str, limit: int = 5) -> str:
    """Ищет подписки на события по имени, источнику, событию и обработчику."""
    try:
        safe_limit = clamp_workflow_limit(limit, default=5, maximum=30)
        ranked_by_name: dict[str, tuple[float, float | None, dict]] = {}

        for payload in load_event_subscription_payloads(EXPORT_PATH):
            score = score_event_subscription_candidate(query, payload)
            if score <= 0:
                continue
            ranked_by_name[payload.get("subscription_name", "")] = (score, None, payload)

        if has_qdrant_collection() and count_collection_points("metadata_event_subscription"):
            response = qclient.query_points(
                collection_name=COLLECTION_NAME,
                query=get_query_embedding(query),
                query_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="chunk_type",
                            match=models.MatchValue(value="metadata_event_subscription"),
                        )
                    ]
                ),
                limit=max(50, safe_limit * 10),
            )
            for hit in response.points:
                payload = hit.payload or {}
                score = score_event_subscription_candidate(query, payload, float(hit.score))
                key = payload.get("subscription_name", "")
                current = ranked_by_name.get(key)
                if current is None or score > current[0]:
                    ranked_by_name[key] = (score, float(hit.score), payload)

        ranked = sorted(ranked_by_name.values(), key=lambda item: item[0], reverse=True)
        if not ranked:
            return f"Подписки на события по запросу `{query}` не найдены."

        lines = [
            "## Event Subscription Search",
            "",
            f"- Query: `{query}`",
            f"- Config: `{CONFIG_ID}`",
            f"- Results: `{min(len(ranked), safe_limit)}`",
        ]
        for index, (score, vector_score, payload) in enumerate(ranked[:safe_limit], start=1):
            vector_line = f"; vector={vector_score:.4f}" if vector_score is not None else ""
            lines.extend([
                "",
                f"### Subscription {index}",
                f"- Name: `{payload.get('subscription_name', 'unknown')}`",
                f"- Synonym: `{payload.get('synonym', '')}`",
                f"- Event: `{payload.get('event', '')}`",
                f"- Sources: `{', '.join(payload.get('sources') or [])}`",
                f"- Handler: `{payload.get('handler', '')}`",
                f"- Score: `{score:.4f}{vector_line}`",
                f"- File: `{payload.get('file_path', 'unknown')}`",
            ])
        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка при поиске подписок на события: {str(e)}"


@mcp.tool()
def find_module(name: str, module_type: str = "", limit: int = 5) -> str:
    """
    Точный или почти точный поиск модуля по имени или полному module_path.
    Подходит для навигации по объектным, менеджерским и общим модулям.
    """
    try:
        ranked_results = rank_module_lookup(name, module_type or None)
        if not ranked_results:
            if module_type.strip():
                return f"Модуль `{name}` с типом `{module_type}` не найден."
            return f"Модуль `{name}` не найден."

        formatted_results = [
            format_module_lookup_result(payload, score, index + 1)
            for index, (score, payload) in enumerate(ranked_results[:limit])
        ]
        return "\n\n".join(formatted_results)
    except Exception as e:
        return f"Ошибка при точном поиске модуля: {str(e)}"


@mcp.tool()
def find_common_module(name: str, limit: int = 5) -> str:
    """
    Точный или почти точный поиск общего модуля.
    Это удобная специализация `find_module` для `CommonModules`.
    """
    if not name.strip():
        return "Укажите имя общего модуля."

    return find_module(name=name, module_type="CommonModules", limit=limit)


@mcp.tool()
def list_module_methods(name: str, module_type: str = "", limit: int = 50) -> str:
    """
    Показывает методы указанного модуля в порядке строк.
    Можно передавать короткое имя модуля или полный `module_path`.
    """
    try:
        module_payload = resolve_module_payload(name, module_type)
        if not module_payload:
            if module_type.strip():
                return f"Модуль `{name}` с типом `{module_type}` не найден."
            return f"Модуль `{name}` не найден."

        methods = collect_module_methods(module_payload, limit=max(limit, 1))
        lines = [
            "## Module Methods",
            "",
            f"- Module: `{module_payload.get('module_path', '')}`",
            f"- Module type: `{module_payload.get('module_type', '')}`",
            f"- File: `{module_payload.get('file_path', '')}`",
            f"- Methods found: `{len(methods)}`",
        ]

        if not methods:
            lines.extend([
                "",
                "## Methods",
                "",
                "- В модуле не найдено методов.",
            ])
            return "\n".join(lines)

        lines.extend([
            "",
            "## Methods",
            "",
        ])
        for method in methods[: max(1, limit)]:
            lines.append(
                f"- `{method.get('method_name', 'unknown')}` "
                f"(строки {method.get('start_line', '?')}-{method.get('end_line', '?')})"
            )

        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка при получении методов модуля: {str(e)}"


@mcp.tool()
def list_object_forms(name: str, object_type: str = "") -> str:
    """
    Показывает формы, связанные с объектом метаданных 1С.
    Сначала использует GraphRepository, а если граф недоступен, переходит к поиску по владельцу формы.
    """
    try:
        ranked_results = rank_metadata_lookup_for_graph_tool(name, object_type)
        if not ranked_results:
            if object_type.strip():
                return f"Объект метаданных `{object_type}.{name}` не найден."
            return f"Объект метаданных `{name}` не найден."

        _, payload = ranked_results[0]
        resolved_type = payload.get("object_type", "")
        resolved_name = payload.get("object_name", "")
        object_label = f"{resolved_type}.{resolved_name}"

        forms: list[dict] = []
        seen_form_ids: set[str] = set()
        if graph_repository.exists():
            node_id = make_metadata_node_id(resolved_type, resolved_name)
            for _, target in graph_repository.iter_edges(node_id, "outgoing", "contains_form"):
                if not target:
                    continue
                target_id = target.get("id", "")
                if target_id in seen_form_ids:
                    continue
                seen_form_ids.add(target_id)
                forms.append(target)

        if not forms and not graph_repository.exists():
            for _, form_payload in find_forms_by_owner(resolved_name, resolved_type):
                form_id = (
                    f"{form_payload.get('owner_object_type') or form_payload.get('owner_type')}"
                    f".{form_payload.get('owner_name')}.{form_payload.get('form_name')}"
                )
                if form_id in seen_form_ids:
                    continue
                seen_form_ids.add(form_id)
                forms.append(form_payload)

        if not forms:
            return "\n".join([
                "## Object Forms",
                "",
                f"- Object: `{object_label}`",
                f"- File: `{payload.get('file_path')}`",
                "- Forms: `0`",
            ])

        forms.sort(key=lambda item: (item.get("form_name", ""), item.get("file_path", "")))
        lines = [
            "## Object Forms",
            "",
            f"- Object: `{object_label}`",
            f"- File: `{payload.get('file_path')}`",
            f"- Forms: `{len(forms)}`",
            "",
            "## Forms",
            "",
        ]
        for form_payload in forms:
            owner_label = format_owner_label(
                form_payload.get("owner_type", ""),
                form_payload.get("owner_name", ""),
                form_payload.get("owner_object_type", ""),
            )
            lines.append(
                f"- `{form_payload.get('form_name')}` ({form_payload.get('root_type', 'unknown')}) "
                f"-> owner `{owner_label}` -> `{form_payload.get('file_path', 'unknown')}`"
            )

        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка при получении списка форм объекта: {str(e)}"


@mcp.tool()
def find_method(name: str, module_path: str = "", module_type: str = "", limit: int = 5) -> str:
    """
    Точный или почти точный поиск метода в модулях 1С.
    При необходимости можно сузить поиск по module_path или module_type.
    """
    try:
        normalized_module_path = module_path.strip() or None
        normalized_module_type = module_type.strip() or None

        ranked_results = find_method_candidates(
            method_name=name,
            module_path=normalized_module_path,
            module_type=normalized_module_type,
            limit=limit,
        )

        if ranked_results and ranked_results[0][0] >= 4.0:
            formatted_results = [
                format_code_lookup_result(payload, score, index + 1)
                for index, (score, payload) in enumerate(ranked_results[:limit])
            ]
            return "\n\n".join(formatted_results)

        if not normalized_module_path and not normalized_module_type:
            if not ranked_results:
                return f"Метод `{name}` не найден."

            formatted_results = [
                format_code_lookup_result(payload, score, index + 1)
                for index, (score, payload) in enumerate(ranked_results[:limit])
            ]
            return "\n\n".join(formatted_results)

        fallback_results = rank_code_lookup(
            method_name=name,
            module_path=normalized_module_path,
            module_type=normalized_module_type,
            exact_method_name=False,
        )
        if not fallback_results:
            return f"Метод `{name}` не найден."

        formatted_results = [
            format_code_lookup_result(payload, score, index + 1)
            for index, (score, payload) in enumerate(fallback_results[:limit])
        ]
        return "\n\n".join(formatted_results)
    except Exception as e:
        return f"Ошибка при точном поиске метода: {str(e)}"


@mcp.tool()
def find_usages(name: str, object_type: str = "", limit: int = 20) -> str:
    """
    Показывает, где используется объект метаданных или метод.
    Для объекта метаданных ищет входящие ссылки из метаданных и BSL-кода.
    Для метода показывает вызывающие методы.
    """
    try:
        if not graph_repository.exists():
            return "GraphRepository недоступен. Сначала постройте graph projection."

        ranked_metadata = rank_metadata_lookup_for_graph_tool(name, object_type)
        if ranked_metadata:
            _, payload = ranked_metadata[0]
            resolved_type = payload.get("object_type", "")
            resolved_name = payload.get("object_name", "")
            node_id = make_metadata_node_id(resolved_type, resolved_name)
            graph_node = graph_repository.get_node(node_id)
            if graph_node:
                metadata_lines: list[str] = []
                code_lines: list[str] = []

                for edge, target in graph_repository.iter_edges(node_id, "incoming"):
                    if edge.get("type") == "references_metadata" and target:
                        source_label = f"{target.get('object_type', 'unknown')}.{target.get('object_name', 'unknown')}"
                        prefix = edge.get("section", "metadata")
                        if edge.get("container"):
                            prefix = f"{prefix}:{edge['container']}"
                        metadata_lines.append(
                            f"- `{source_label}` -> `{prefix}` -> `{edge.get('source', 'unknown')}`"
                        )
                    elif edge.get("type") == "uses_metadata" and target:
                        code_lines.append(format_method_graph_line(target, edge))

                lines = [
                    "## Usages",
                    "",
                    f"- Object: `{resolved_type}.{resolved_name}`",
                    f"- File: `{payload.get('file_path')}`",
                    f"- Metadata usages: `{len(metadata_lines)}`",
                    f"- Code usages: `{len(code_lines)}`",
                ]

                if metadata_lines:
                    lines.extend([
                        "",
                        "## Metadata Usages",
                        "",
                        *metadata_lines[:limit],
                    ])

                if code_lines:
                    lines.extend([
                        "",
                        "## Code Usages",
                        "",
                        *code_lines[:limit],
                    ])

                return "\n".join(lines)

        method_payload = resolve_method_payload_for_graph_tool(name)
        if not method_payload:
            if object_type.strip():
                return f"Объект метаданных `{object_type}.{name}` не найден."
            return f"Использования для `{name}` не найдены."

        node_id = make_method_node_id(
            method_payload.get("module_path", ""),
            method_payload.get("method_name", ""),
            int(method_payload.get("start_line") or 0),
        )
        graph_node = graph_repository.get_node(node_id)
        if not graph_node:
            return f"Метод `{name}` найден в индексе, но отсутствует в graph projection."

        caller_lines: list[str] = []
        for edge, target in graph_repository.iter_edges(node_id, "incoming", "calls"):
            if not target:
                continue
            caller_lines.append(format_method_graph_line(target, edge))

        lines = [
            "## Usages",
            "",
            f"- Method: `{method_payload.get('module_path')}.{method_payload.get('method_name')}`",
            f"- File: `{method_payload.get('file_path')}`",
            f"- Callers: `{len(caller_lines)}`",
        ]
        if caller_lines:
            lines.extend([
                "",
                "## Callers",
                "",
                *caller_lines[:limit],
            ])
        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка при поиске использований: {str(e)}"


@mcp.tool()
def get_callers(name: str, module_path: str = "", module_type: str = "", limit: int = 20) -> str:
    """
    Показывает методы, которые вызывают указанный метод.
    """
    try:
        if not graph_repository.exists():
            return "GraphRepository недоступен. Сначала постройте graph projection."

        method_payload = resolve_method_payload_for_graph_tool(name, module_path, module_type)
        if not method_payload:
            return f"Метод `{name}` не найден."

        node_id = make_method_node_id(
            method_payload.get("module_path", ""),
            method_payload.get("method_name", ""),
            int(method_payload.get("start_line") or 0),
        )
        graph_node = graph_repository.get_node(node_id)
        if not graph_node:
            return f"Метод `{name}` найден в индексе, но отсутствует в graph projection."

        caller_lines: list[str] = []
        for edge, target in graph_repository.iter_edges(node_id, "incoming", "calls"):
            if not target:
                continue
            caller_lines.append(format_method_graph_line(target, edge))

        lines = [
            "## Callers",
            "",
            f"- Method: `{method_payload.get('module_path')}.{method_payload.get('method_name')}`",
            f"- File: `{method_payload.get('file_path')}`",
            f"- Callers: `{len(caller_lines)}`",
        ]

        if caller_lines:
            lines.extend([
                "",
                "## Methods",
                "",
                *caller_lines[:limit],
            ])

        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка при получении callers: {str(e)}"


@mcp.tool()
def get_callees(name: str, module_path: str = "", module_type: str = "", limit: int = 20) -> str:
    """
    Показывает методы, которые вызывает указанный метод.
    """
    try:
        if not graph_repository.exists():
            return "GraphRepository недоступен. Сначала постройте graph projection."

        method_payload = resolve_method_payload_for_graph_tool(name, module_path, module_type)
        if not method_payload:
            return f"Метод `{name}` не найден."

        node_id = make_method_node_id(
            method_payload.get("module_path", ""),
            method_payload.get("method_name", ""),
            int(method_payload.get("start_line") or 0),
        )
        graph_node = graph_repository.get_node(node_id)
        if not graph_node:
            return f"Метод `{name}` найден в индексе, но отсутствует в graph projection."

        callee_lines: list[str] = []
        for edge, target in graph_repository.iter_edges(node_id, "outgoing", "calls"):
            if not target:
                continue
            callee_lines.append(format_method_graph_line(target, edge))

        lines = [
            "## Callees",
            "",
            f"- Method: `{method_payload.get('module_path')}.{method_payload.get('method_name')}`",
            f"- File: `{method_payload.get('file_path')}`",
            f"- Callees: `{len(callee_lines)}`",
        ]

        if callee_lines:
            lines.extend([
                "",
                "## Methods",
                "",
                *callee_lines[:limit],
            ])

        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка при получении callees: {str(e)}"


@mcp.tool()
def change_impact_report(
    name: str,
    object_type: str = "",
    module_path: str = "",
    module_type: str = "",
    limit: int = 20,
) -> str:
    """
    Builds a compact impact report for a metadata object or method:
    dependencies, usages, related modules/forms, callers and callees.
    """
    try:
        safe_limit = clamp_workflow_limit(limit, maximum=80)
        lines = [
            "## Change Impact Report",
            "",
            f"- Config: `{CONFIG_ID}`",
            f"- Target: `{name}`",
            f"- Graph source: `{graph_repository.get_source_label()}`",
        ]

        method_hint = bool(module_path.strip() or module_type.strip()) and not object_type.strip()
        method_payload = None
        if not object_type.strip():
            method_payload = find_exact_code_payload(name, module_path, module_type)
            if method_payload is None and method_hint:
                method_payload = resolve_workflow_method_payload(name, module_path, module_type)
        prefer_method = bool(method_payload)

        ranked_metadata = [] if prefer_method else rank_metadata_lookup_for_graph_tool(name, object_type)
        if ranked_metadata:
            _, payload = ranked_metadata[0]
            graph_context = collect_metadata_graph_context(payload, safe_limit)
            lines.extend([
                "",
                "## Target",
                "",
                f"- Kind: `metadata`",
                f"- Object: `{metadata_label(payload)}`",
                f"- Synonym: `{payload.get('synonym') or ''}`",
                f"- File: `{payload.get('file_path')}`",
            ])
            append_section(lines, "Direct Dependencies", graph_context["dependencies"])
            append_section(lines, "Owned Forms", graph_context["forms"])
            append_section(lines, "Owned Modules", graph_context["modules"])
            append_section(lines, "Metadata Usages", graph_context["metadata_usages"])
            append_section(lines, "Code Usages", graph_context["code_usages"])
            lines.extend([
                "",
                "## Suggested Checks",
                "",
                "- Review direct dependencies before changing object structure.",
                "- Review code usages before renaming fields, tabular sections, or object names.",
                "- Rebuild graph after targeted reindex if the export changes.",
            ])
            return "\n".join(lines)

        if method_payload is None and not object_type.strip():
            method_payload = resolve_workflow_method_payload(name, module_path, module_type)
        if not method_payload:
            return f"Target `{name}` was not found as metadata object or method."

        graph_context = collect_method_graph_context(method_payload, safe_limit)
        lines.extend([
            "",
            "## Target",
            "",
            f"- Kind: `method`",
            f"- Method: `{method_label(method_payload)}`",
            f"- Lines: `{method_payload.get('start_line', '?')}-{method_payload.get('end_line', '?')}`",
            f"- File: `{method_payload.get('file_path')}`",
        ])
        append_section(lines, "Entrypoints", graph_context["entrypoints"])
        append_section(lines, "Callers", graph_context["callers"])
        append_section(lines, "Callees", graph_context["callees"])
        lines.extend([
            "",
            "## Suggested Checks",
            "",
            "- Review callers before changing parameters, return value, or side effects.",
            "- Review callees before moving shared logic or changing transaction-sensitive code.",
            "- Use `get_file_snippet` for a wider local code window when editing.",
        ])
        return "\n".join(lines)
    except Exception as e:
        return f"Error while building change impact report: {str(e)}"


@mcp.tool()
def build_implementation_context(
    task: str,
    limit: int = 5,
    include_snippets: bool = True,
) -> str:
    """
    Collects the most useful metadata, code and graph context for starting an implementation task.
    """
    try:
        safe_limit = clamp_workflow_limit(limit, default=5, maximum=12)
        metadata_info, metadata_results = collect_metadata_explanations(task, safe_limit * 2)
        code_info, code_results = collect_code_explanations(task, safe_limit * 2)
        metadata_results = unique_metadata_explain_results(metadata_results, safe_limit)
        code_results = unique_code_explain_results(code_results, safe_limit)

        lines = [
            "## Implementation Context",
            "",
            f"- Config: `{CONFIG_ID}`",
            f"- Task: `{task}`",
            f"- Graph source: `{graph_repository.get_source_label()}`",
            f"- Metadata retrieval queries: `{metadata_info.get('retrieval_queries', [])}`",
            f"- Code retrieval queries: `{code_info.get('retrieval_queries', [])}`",
        ]

        lines.extend(["", "## Metadata Candidates", ""])
        if metadata_results:
            for index, result in enumerate(metadata_results, start=1):
                lines.extend(format_metadata_candidate(result, index, include_document=False))
                graph_context = collect_metadata_graph_context(result["payload"], limit=5)
                quick_context = []
                if graph_context["forms"]:
                    quick_context.append(f"forms={len(graph_context['forms'])}")
                if graph_context["modules"]:
                    quick_context.append(f"modules={len(graph_context['modules'])}")
                if graph_context["code_usages"]:
                    quick_context.append(f"code_usages>={len(graph_context['code_usages'])}")
                if quick_context:
                    lines.append(f"- Graph hints: `{', '.join(quick_context)}`")
                lines.append("")
        else:
            lines.append("- none")

        lines.extend(["", "## Code Candidates", ""])
        if code_results:
            for index, result in enumerate(code_results, start=1):
                lines.extend(format_code_candidate(result, index, include_snippet=include_snippets))
                method_context = collect_method_graph_context(result["payload"], limit=5)
                quick_context = []
                if method_context["callers"]:
                    quick_context.append(f"callers>={len(method_context['callers'])}")
                if method_context["callees"]:
                    quick_context.append(f"callees>={len(method_context['callees'])}")
                if quick_context:
                    lines.append(f"- Graph hints: `{', '.join(quick_context)}`")
                lines.append("")
        else:
            lines.append("- none")

        lines.extend([
            "",
            "## Next MCP Calls",
            "",
            "- `change_impact_report` for the object or method you plan to edit.",
            "- `get_file_snippet` for the exact editing window around a selected method.",
            "- `reindex_file` after changing one exported BSL/XML file.",
        ])
        return "\n".join(lines)
    except Exception as e:
        return f"Error while building implementation context: {str(e)}"


def describe_task_config_scope() -> tuple[list[str], str]:
    registered = list_registered_configs()
    related_extensions = [
        config
        for config in registered
        if config.base_config_id == CONFIG_ID
    ]

    if CONFIG_KIND == "extension":
        strategy = (
            f"Работать в активном расширении `{CONFIG_ID}`; перед изменением проверить "
            f"соответствующую сущность основной конфигурации `{BASE_CONFIG_ID or 'unknown'}`."
        )
    elif related_extensions:
        extension_ids = ", ".join(config.config_id for config in related_extensions)
        strategy = (
            f"Предпочесть extension-first реализацию и проверить зарегистрированные расширения: "
            f"`{extension_ids}`. Изменение основной конфигурации требует отдельного обоснования."
        )
    else:
        strategy = (
            "Зарегистрированное расширение для активной конфигурации не найдено. "
            "Перед изменением основной конфигурации оценить возможность создать расширение."
        )

    scope_lines = [
        f"- Active config: `{CONFIG_ID}`",
        f"- Config kind: `{CONFIG_KIND}`",
        f"- Base config: `{BASE_CONFIG_ID or 'none'}`",
    ]
    for config in related_extensions:
        scope_lines.append(
            f"- Related extension: `{config.config_id}` -> `{config.export_path}`"
        )
    return scope_lines, strategy


@mcp.tool()
def analyze_task(task: str, limit: int = 5) -> str:
    """
    Анализирует ТЗ и формирует единый evidence-based план: кандидаты объектов и методов,
    конфигурационный контекст, точки изменения, риски и validation checklist.
    """
    try:
        if not task.strip():
            return "Укажите текст технического задания."

        safe_limit = clamp_workflow_limit(limit, default=5, maximum=10)
        metadata_info, metadata_results = collect_metadata_explanations(task, safe_limit * 2)
        code_info, code_results = collect_code_explanations(task, safe_limit * 2)
        metadata_results = unique_metadata_explain_results(metadata_results, safe_limit)
        code_results = unique_code_explain_results(code_results, safe_limit)
        scope_lines, change_strategy = describe_task_config_scope()

        lines = [
            "## Task Analysis",
            "",
            f"- Task: `{task}`",
            f"- Config: `{CONFIG_ID}`",
            f"- Qdrant available: `{has_qdrant_collection()}`",
            f"- Graph available: `{graph_repository.exists()}`",
            f"- Graph source: `{graph_repository.get_source_label()}`",
            "",
            "## Detected Intent",
            "",
            f"- Metadata type: `{metadata_info.get('detected_type') or 'not detected'}`",
            f"- Structural intent: `{metadata_info.get('structural_intent')}`",
            f"- Register intent: `{metadata_info.get('register_intent')}`",
            f"- Validation intent: `{code_info.get('validation_intent')}`",
            f"- Report/form intent: `{code_info.get('report_intent')}`",
            "",
            "## Configuration Scope",
            "",
            *scope_lines,
            f"- Recommended strategy: {change_strategy}",
            "",
            "## Evidence: Metadata Candidates",
            "",
        ]

        metadata_contexts: list[tuple[dict, dict[str, list[str]]]] = []
        if metadata_results:
            for index, result in enumerate(metadata_results, start=1):
                payload = result["payload"]
                graph_context = collect_metadata_graph_context(payload, limit=5)
                metadata_contexts.append((payload, graph_context))
                lines.extend([
                    f"### Metadata {index}",
                    f"- Object: `{metadata_label(payload)}`",
                    f"- Score: `{result.get('final_score', 0):.4f}`",
                    f"- Evidence source: `{result.get('best_source', 'unknown')}`",
                    f"- File: `{payload.get('file_path', 'unknown')}`",
                    f"- Graph coverage: dependencies=`{len(graph_context['dependencies'])}`, "
                    f"forms=`{len(graph_context['forms'])}`, modules=`{len(graph_context['modules'])}`, "
                    f"code usages>=`{len(graph_context['code_usages'])}`",
                    "",
                ])
        else:
            lines.extend([
                "- No metadata candidates found. Treat object selection as unresolved.",
                "",
            ])

        lines.extend(["## Evidence: Code Candidates", ""])
        method_contexts: list[tuple[dict, dict[str, list[str]]]] = []
        if code_results:
            for index, result in enumerate(code_results, start=1):
                payload = result["payload"]
                graph_context = collect_method_graph_context(payload, limit=5)
                method_contexts.append((payload, graph_context))
                lines.extend([
                    f"### Method {index}",
                    f"- Method: `{method_label(payload)}`",
                    f"- Score: `{result.get('final_score', 0):.4f}`",
                    f"- Evidence source: `{result.get('best_source', 'unknown')}`",
                    f"- Lines: `{payload.get('start_line', '?')}-{payload.get('end_line', '?')}`",
                    f"- File: `{payload.get('file_path', 'unknown')}`",
                    f"- Graph coverage: entrypoints=`{len(graph_context['entrypoints'])}`, "
                    f"callers>=`{len(graph_context['callers'])}`, callees>=`{len(graph_context['callees'])}`",
                    "",
                ])
        else:
            lines.extend([
                "- No code candidates found. Implementation point remains unresolved.",
                "",
            ])

        lines.extend(["## Recommended Change Points", ""])
        if metadata_contexts:
            payload, graph_context = metadata_contexts[0]
            lines.append(
                f"- Primary domain candidate: `{metadata_label(payload)}` -> `{payload.get('file_path', 'unknown')}`."
            )
            if graph_context["forms"]:
                lines.append("- Inspect the object's forms before changing UI behavior or command handling.")
            if graph_context["modules"]:
                lines.append("- Inspect owned object/manager modules before introducing new common-module logic.")
        if method_contexts:
            payload, _ = method_contexts[0]
            lines.append(
                f"- Primary code candidate: `{method_label(payload)}` -> `{payload.get('file_path', 'unknown')}` "
                f"(lines {payload.get('start_line', '?')}-{payload.get('end_line', '?')})."
            )
        if not metadata_contexts and not method_contexts:
            lines.append("- No evidence-backed change point is available; refine the task or rebuild the semantic index.")
        lines.append("- These are candidates, not an automatic edit decision; confirm behavior in source before changing code.")

        lines.extend(["", "## Risks And Unknowns", ""])
        risk_count = 0
        if not has_qdrant_collection():
            lines.append("- Semantic Qdrant index is unavailable; current candidates rely on lexical/graph evidence and may miss similar implementations.")
            risk_count += 1
        if not graph_repository.exists():
            lines.append("- Graph projection is unavailable; impact, callers and usages are incomplete.")
            risk_count += 1
        if CONFIG_KIND != "extension" and any(config.base_config_id == CONFIG_ID for config in list_registered_configs()):
            lines.append("- The active base configuration has registered extensions; cross-config overrides are not yet resolved automatically.")
            risk_count += 1
        if metadata_contexts and not metadata_contexts[0][1]["code_usages"]:
            lines.append("- No code usages were confirmed for the top metadata candidate; verify dynamic references and query text manually.")
            risk_count += 1
        if method_contexts and not method_contexts[0][1]["callers"]:
            lines.append("- No callers were confirmed for the top method; dynamic or unresolved calls may still exist.")
            risk_count += 1
        if risk_count == 0:
            lines.append("- No immediate infrastructure gaps detected; domain assumptions still require developer review.")

        lines.extend([
            "",
            "## Implementation Plan",
            "",
            "1. Confirm the top metadata object and read its structure, forms and owned modules.",
            "2. Read the complete source window around the selected method and inspect callers/callees.",
            "3. Check the related extension before editing the base configuration.",
            "4. Reuse an existing implementation pattern from the selected candidates.",
            "5. Keep the change localized and add or update automated tests where available.",
            "6. Reindex changed files and run post-change impact analysis.",
            "",
            "## Validation Checklist",
            "",
            "- Validate BSL syntax and client/server annotations.",
            "- Check method signatures, callers and exported API compatibility.",
            "- Check metadata, form-command and role implications.",
            "- Run relevant YAxUnit/Vanessa or project-specific tests when available.",
            "- Re-run `change_impact_report` after reindexing changed files.",
            "- Compare the implementation against every acceptance condition in the task.",
            "",
            "## Next MCP Calls",
            "",
            "- `get_file_snippet` for the selected code window.",
            "- `change_impact_report` for the selected object or method.",
            "- `form_structure_report` when the task affects UI or commands.",
            "- `switch_configuration` to inspect the related base or extension.",
        ])
        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка при анализе технического задания: {str(e)}"


@mcp.tool()
def trace_business_flow(query: str, limit: int = 8) -> str:
    """
    Produces a best-effort business-flow trace from semantic metadata/code matches plus graph links.
    """
    try:
        safe_limit = clamp_workflow_limit(limit, default=8, maximum=20)
        metadata_info, metadata_results = collect_metadata_explanations(query, min(10, safe_limit * 2))
        code_info, code_results = collect_code_explanations(query, safe_limit * 2)
        metadata_results = unique_metadata_explain_results(metadata_results, min(5, safe_limit))
        code_results = unique_code_explain_results(code_results, safe_limit)

        lines = [
            "## Business Flow Trace",
            "",
            f"- Config: `{CONFIG_ID}`",
            f"- Query: `{query}`",
            f"- Graph source: `{graph_repository.get_source_label()}`",
            f"- Metadata intent: structural=`{metadata_info.get('structural_intent')}`, register=`{metadata_info.get('register_intent')}`",
            f"- Code intent: validation=`{code_info.get('validation_intent')}`, report=`{code_info.get('report_intent')}`",
        ]

        lines.extend(["", "## Candidate Domain Objects", ""])
        if metadata_results:
            for index, result in enumerate(metadata_results[:5], start=1):
                payload = result["payload"]
                graph_context = collect_metadata_graph_context(payload, limit=5)
                lines.append(
                    f"- `{index}. {metadata_label(payload)}` -> `{payload.get('file_path')}` "
                    f"(forms={len(graph_context['forms'])}, modules={len(graph_context['modules'])}, "
                    f"code_usages>={len(graph_context['code_usages'])})"
                )
        else:
            lines.append("- none")

        lines.extend(["", "## Candidate Code Path", ""])
        if code_results:
            for index, result in enumerate(code_results[:safe_limit], start=1):
                payload = result["payload"]
                method_context = collect_method_graph_context(payload, limit=4)
                lines.append(
                    f"- `{index}. {method_label(payload)}` "
                    f"(lines {payload.get('start_line', '?')}-{payload.get('end_line', '?')}) "
                    f"-> `{payload.get('file_path')}`"
                )
                if method_context["callers"]:
                    lines.append(f"  callers: {len(method_context['callers'])}")
                    lines.extend(f"  {item}" for item in method_context["callers"][:2])
                if method_context["callees"]:
                    lines.append(f"  callees: {len(method_context['callees'])}")
                    lines.extend(f"  {item}" for item in method_context["callees"][:2])
        else:
            lines.append("- none")

        lines.extend([
            "",
            "## Reading Order",
            "",
            "- Start with the highest-scored metadata object to understand data shape.",
            "- Then inspect the highest-scored code methods and their callers.",
            "- Use `change_impact_report` on the chosen edit target before making changes.",
        ])
        return "\n".join(lines)
    except Exception as e:
        return f"Error while tracing business flow: {str(e)}"


@mcp.tool()
def reindex_file(file_path: str, rebuild_graph: bool = True) -> str:
    """
    Принудительно переиндексирует один BSL/XML файл внутри EXPORT_PATH.
    По умолчанию после этого перестраивает полный graph projection, чтобы
    graph navigation не осталась на старом состоянии.
    """
    try:
        return reindex_export_target(file_path, path_kind="file", rebuild_graph=rebuild_graph)
    except Exception as e:
        return f"Ошибка при переиндексации файла: {str(e)}"


@mcp.tool()
def reindex_path(path: str, rebuild_graph: bool = True) -> str:
    """
    Принудительно переиндексирует каталог внутри EXPORT_PATH.
    По умолчанию после этого перестраивает полный graph projection.
    """
    try:
        return reindex_export_target(path, path_kind="path", rebuild_graph=rebuild_graph)
    except Exception as e:
        return f"Ошибка при переиндексации каталога: {str(e)}"


def collect_embedding_readiness() -> dict:
    issues: list[str] = []
    warnings: list[str] = []
    model_path_exists = False
    cache_data = load_index_cache()
    cached_schema_version = cache_data.get(INDEX_SCHEMA_CACHE_KEY) if cache_data else None
    index_schema_current = cached_schema_version == INDEX_SCHEMA_VERSION

    if EMBEDDING_PROVIDER == "openai":
        if not OPENAI_API_KEY:
            issues.append("OPENAI_API_KEY is required for EMBEDDING_PROVIDER=openai.")
    else:
        if EMBEDDING_MODEL_PATH:
            model_path_exists = Path(EMBEDDING_MODEL_PATH).resolve().exists()
            if not model_path_exists:
                issues.append(f"EMBEDDING_MODEL_PATH does not exist: {EMBEDDING_MODEL_PATH}")
        elif EMBEDDING_LOCAL_ONLY:
            issues.append("EMBEDDING_LOCAL_ONLY=true requires EMBEDDING_MODEL_PATH.")
        else:
            warnings.append("Local model may access its cache or download source on first initialization.")
        if OPENAI_API_KEY:
            warnings.append("OPENAI_API_KEY is present but ignored because EMBEDDING_PROVIDER=local.")

    return {
        "ready": not issues,
        "issues": issues,
        "warnings": warnings,
        "model_path_exists": model_path_exists,
        "cached_schema_version": cached_schema_version,
        "index_schema_current": index_schema_current,
        "full_reindex_required": CACHE_FILE.exists() and not index_schema_current,
    }


@mcp.tool()
def embedding_status() -> str:
    """
    Показывает выбранный embedding provider и готовность конфигурации без загрузки модели
    и без выполнения платных API-запросов.
    """
    readiness = collect_embedding_readiness()
    lines = [
        "## Embedding Status",
        "",
        f"- Provider: `{EMBEDDING_PROVIDER}`",
        f"- Model: `{OPENAI_EMBEDDING_MODEL if USE_OPENAI_EMBEDDINGS else EMBEDDING_MODEL}`",
        f"- Config: `{CONFIG_ID}`",
        f"- Collection: `{COLLECTION_NAME}`",
        f"- Configuration ready: `{readiness['ready']}`",
        f"- OpenAI key configured: `{bool(OPENAI_API_KEY)}`",
        f"- Local-only mode: `{EMBEDDING_LOCAL_ONLY}`",
        f"- Local model path: `{EMBEDDING_MODEL_PATH or 'not set'}`",
        f"- Local model path exists: `{readiness['model_path_exists']}`",
        f"- FastEmbed cache dir: `{FASTEMBED_CACHE_DIR or 'default'}`",
        f"- Qdrant collection available: `{has_qdrant_collection()}`",
        f"- Expected index schema: `{INDEX_SCHEMA_VERSION}`",
        f"- Cached index schema: `{readiness['cached_schema_version'] if readiness['cached_schema_version'] is not None else 'missing'}`",
        f"- Index schema current: `{readiness['index_schema_current']}`",
        f"- Full reindex required: `{readiness['full_reindex_required']}`",
    ]
    lines.extend(["", "## Issues", ""])
    lines.extend(f"- {issue}" for issue in readiness["issues"])
    if not readiness["issues"]:
        lines.append("- none")
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {warning}" for warning in readiness["warnings"])
    if not readiness["warnings"]:
        lines.append("- none")
    return "\n".join(lines)


@mcp.tool()
def index_status(include_fs_scan: bool = True) -> str:
    """
    Показывает состояние индекса: коллекцию Qdrant, кэш индексации и,
    при необходимости, расхождение экспорта на диске с кэшем.
    """
    collection_info = None
    qdrant_error = None
    try:
        collection_info = qclient.get_collection(COLLECTION_NAME)
    except Exception as e:
        qdrant_error = str(e)

    cache = load_index_cache()
    cache_exists = CACHE_FILE.exists()
    cache_mtime = CACHE_FILE.stat().st_mtime if cache_exists else None
    graph_cache_exists = graph_repository.exists()
    graph_cache_file_exists = GRAPH_CACHE_FILE.exists()
    graph_cache_mtime = GRAPH_CACHE_FILE.stat().st_mtime if graph_cache_file_exists else None
    graph_stats = graph_repository.get_stats() if graph_cache_exists else {}
    embedding_readiness = collect_embedding_readiness()

    total_points = count_collection_points() if collection_info else None
    code_points = count_collection_points("code") if collection_info else None
    metadata_points = count_collection_points("metadata") if collection_info else None
    form_points = count_collection_points("metadata_form") if collection_info else None
    module_summary_points = count_collection_points("code_module_summary") if collection_info else None
    command_points = count_collection_points("metadata_command") if collection_info else None
    event_subscription_points = count_collection_points("metadata_event_subscription") if collection_info else None

    vectors_config = collection_info.config.params.vectors if collection_info else None
    vector_size = getattr(vectors_config, "size", "unknown") if vectors_config else "unknown"
    distance = getattr(getattr(vectors_config, "distance", None), "value", "unknown") if vectors_config else "unknown"
    points_count = getattr(collection_info, "points_count", None) if collection_info else None
    indexed_vectors_count = getattr(collection_info, "indexed_vectors_count", None) if collection_info else None

    lines = [
        "## Index Status",
        "",
        f"- Collection: `{COLLECTION_NAME}`",
        f"- Config name: `{CONFIG_NAME}`",
        f"- Config id: `{CONFIG_ID}`",
        f"- Config profile: `{CONFIG_PROFILE}`",
        f"- Config kind: `{CONFIG_KIND}`",
        f"- Base config id: `{BASE_CONFIG_ID or 'none'}`",
        f"- Platform version: `{PLATFORM_VERSION or 'unknown'}`",
        f"- Config registry: `{RUNTIME_CONFIG.registry_file or 'env-only mode'}`",
        f"- Qdrant URL: `{QDRANT_URL}`",
        f"- Embedding provider: `{EMBEDDING_PROVIDER}`",
        f"- Embedding model: `{OPENAI_EMBEDDING_MODEL if USE_OPENAI_EMBEDDINGS else EMBEDDING_MODEL}`",
        f"- Embedding configuration ready: `{embedding_readiness['ready']}`",
        f"- Embedding readiness issues: `{len(embedding_readiness['issues'])}`",
        f"- Qdrant collection available: `{bool(collection_info)}`",
        f"- Vector size: `{vector_size}`",
        f"- Distance: `{distance}`",
        f"- Collection points_count: `{points_count}`",
        f"- Indexed vectors count: `{indexed_vectors_count}`",
        f"- Exact total chunks: `{total_points}`",
        f"- Exact code chunks: `{code_points}`",
        f"- Exact metadata chunks: `{metadata_points}`",
        f"- Exact form chunks: `{form_points}`",
        f"- Exact module summary chunks: `{module_summary_points}`",
        f"- Exact command chunks: `{command_points}`",
        f"- Exact event subscription chunks: `{event_subscription_points}`",
        f"- Export path: `{EXPORT_PATH}`",
        f"- Cache file: `{CACHE_FILE}`",
        f"- Cache exists: `{cache_exists}`",
        f"- Cache entries: `{len(cache)}`",
        f"- Cache updated at: `{format_timestamp(cache_mtime)}`",
        f"- Graph cache file: `{GRAPH_CACHE_FILE}`",
        f"- Graph source: `{graph_repository.get_source_label()}`",
        f"- Graph cache exists: `{graph_cache_exists}`",
        f"- Graph cache file exists: `{graph_cache_file_exists}`",
        f"- Graph cache updated at: `{format_timestamp(graph_cache_mtime)}`",
        f"- Graph nodes: `{graph_stats.get('node_count', 'unknown')}`",
        f"- Graph edges: `{graph_stats.get('edge_count', 'unknown')}`",
        f"- Graph generated at: `{graph_repository.get_generated_at() if graph_cache_exists else 'unknown'}`",
    ]

    if qdrant_error:
        lines.extend([
            "",
            "## Qdrant Status",
            "",
            f"- Collection is unavailable: `{qdrant_error}`",
        ])

    if include_fs_scan:
        export_scan = collect_export_scan(cache)
        if not export_scan["exists"]:
            lines.extend([
                "",
                "## Export Scan",
                "",
                f"- Export path not found: `{export_scan['root']}`",
            ])
        else:
            lines.extend([
                "",
                "## Export Scan",
                "",
                f"- Indexed BSL files: `{export_scan['indexed_bsl_files']}`",
                f"- Indexed XML files: `{export_scan['indexed_xml_files']}`",
                f"- Indexed form XML files: `{export_scan['indexed_form_xml_files']}`",
                f"- Indexed files changed vs cache: `{export_scan['changed_files']}`",
            ])
    else:
        lines.extend([
            "",
            "## Export Scan",
            "",
            "- Skipped. Pass `include_fs_scan=True` for disk-vs-cache comparison.",
        ])

    return "\n".join(lines)


@mcp.tool()
def list_configurations() -> str:
    """
    Показывает зарегистрированные конфигурации и расширения, доступные для текущего MCP runtime.
    """
    registered_configs = list_registered_configs()
    lines = [
        "## Configurations",
        "",
        f"- Active config: `{CONFIG_ID}`",
        f"- Active export path: `{EXPORT_PATH}`",
        f"- Active collection: `{COLLECTION_NAME}`",
    ]

    if not registered_configs:
        lines.extend([
            "",
            "Registry file не найден. MCP работает в single-config режиме через `.env`.",
        ])
        return "\n".join(lines)

    lines.extend([
        "",
        f"- Registry file: `{RUNTIME_CONFIG.registry_file}`",
        "",
        "## Registered Configs",
        "",
    ])

    for config in registered_configs:
        marker = "active" if config.config_id == CONFIG_ID else "idle"
        relation = f", base=`{config.base_config_id}`" if config.base_config_id else ""
        description = f", note={config.description}" if config.description else ""
        lines.append(
            f"- `{config.config_id}` [{marker}] kind=`{config.config_kind}`, "
            f"name=`{config.config_name}`, export=`{config.export_path}`, "
            f"collection=`{config.collection_name}`{relation}{description}"
        )

    return "\n".join(lines)


def build_repository_for_runtime_config(runtime_config: RuntimeConfig) -> GraphRepository:
    return build_graph_repository(
        backend=GRAPH_BACKEND,
        graph_file=runtime_config.graph_cache_file,
        memgraph_uri=MEMGRAPH_URI,
        memgraph_username=MEMGRAPH_USER,
        memgraph_password=MEMGRAPH_PASSWORD,
        config_id=runtime_config.config_id,
        memgraph_database=MEMGRAPH_DATABASE,
    )


def overlay_node_identity(kind: str, node: dict) -> tuple | str | None:
    if kind == "metadata":
        if not node.get("file_path"):
            return None
        return (node.get("object_type", ""), node.get("object_name", ""))
    if kind == "form":
        return (
            node.get("owner_object_type", "") or node.get("owner_type", ""),
            node.get("owner_name", ""),
            node.get("form_name", ""),
        )
    if kind == "module":
        return node.get("module_path", "") or None
    if kind == "method":
        target_method = node.get("extension_target_method", "") or node.get("method_name", "")
        return (node.get("module_path", ""), target_method)
    if kind == "command":
        return node.get("id", "") or None
    return node.get("id", "") or None


def overlay_node_label(kind: str, node: dict) -> str:
    if kind == "metadata":
        return metadata_label(node)
    if kind == "form":
        owner = format_owner_label(
            node.get("owner_type", ""),
            node.get("owner_name", ""),
            node.get("owner_object_type", ""),
        )
        return f"{owner}.{node.get('form_name', 'unknown')}"
    if kind == "module":
        return node.get("module_path", "unknown")
    if kind == "method":
        label = method_label(node)
        if node.get("extension_annotation") and node.get("extension_target_method"):
            label += (
                f" [{node.get('extension_annotation')} -> "
                f"{node.get('extension_target_method')}]"
            )
        return label
    if kind == "command":
        return f"{node.get('form_name', 'unknown')}.{node.get('command_name', 'unknown')}"
    return graph_node_label(node)


def compare_overlay_kind(
    base_repository: GraphRepository,
    extension_repository: GraphRepository,
    kind: str,
    sample_limit: int,
) -> dict:
    base_keys = set()
    base_method_name_counts: dict[str, int] = {}
    for node in base_repository.iter_nodes(kind):
        identity = overlay_node_identity(kind, node)
        if identity is None:
            continue
        base_keys.add(identity)
        if kind == "method":
            method_name = str(node.get("method_name") or "")
            if method_name:
                base_method_name_counts[method_name] = base_method_name_counts.get(method_name, 0) + 1
    extension_total = 0
    overlay_count = 0
    added_count = 0
    overlay_samples: list[str] = []
    added_samples: list[str] = []
    annotation_counts: dict[str, int] = {}
    resolved_annotations = 0
    unresolved_annotations = 0
    unresolved_reasons: dict[str, int] = {}

    for node in extension_repository.iter_nodes(kind):
        identity = overlay_node_identity(kind, node)
        if identity is None:
            continue
        extension_total += 1
        annotation = str(node.get("extension_annotation") or "")
        if annotation:
            annotation_counts[annotation] = annotation_counts.get(annotation, 0) + 1
        label = overlay_node_label(kind, node)
        if identity in base_keys:
            overlay_count += 1
            if annotation:
                resolved_annotations += 1
            if len(overlay_samples) < sample_limit:
                overlay_samples.append(label)
        else:
            added_count += 1
            if annotation:
                unresolved_annotations += 1
                target_method = str(node.get("extension_target_method") or "")
                candidate_count = base_method_name_counts.get(target_method, 0)
                if candidate_count == 0:
                    reason = "target_absent_in_base_graph"
                elif candidate_count == 1:
                    reason = "target_found_in_other_module"
                else:
                    reason = "target_name_ambiguous_outside_expected_module"
                unresolved_reasons[reason] = unresolved_reasons.get(reason, 0) + 1
            if len(added_samples) < sample_limit:
                added_samples.append(label)

    return {
        "base_total": len(base_keys),
        "extension_total": extension_total,
        "overlay_count": overlay_count,
        "added_count": added_count,
        "overlay_samples": overlay_samples,
        "added_samples": added_samples,
        "annotation_counts": annotation_counts,
        "resolved_annotations": resolved_annotations,
        "unresolved_annotations": unresolved_annotations,
        "unresolved_reasons": unresolved_reasons,
    }


@mcp.tool()
def extension_overlay_report(extension_config_id: str = "", limit: int = 10) -> str:
    """
    Сравнивает graph projections основной конфигурации и расширения.
    Показывает пересекающиеся и новые metadata objects, forms, modules, methods и commands.
    """
    registered = list_registered_configs()
    by_id = {config.config_id: config for config in registered}
    requested_id = (extension_config_id or "").strip()

    if requested_id:
        extension_config = by_id.get(requested_id)
    elif CONFIG_KIND == "extension":
        extension_config = by_id.get(CONFIG_ID)
    else:
        extension_config = next(
            (config for config in registered if config.base_config_id == CONFIG_ID),
            None,
        )

    if extension_config is None:
        available = ", ".join(
            config.config_id for config in registered if config.config_kind == "extension"
        ) or "none"
        return f"Расширение не найдено. Доступные extension config_id: `{available}`."
    if extension_config.config_kind != "extension" or not extension_config.base_config_id:
        return f"Конфигурация `{extension_config.config_id}` не зарегистрирована как расширение с base_config_id."

    base_config = by_id.get(extension_config.base_config_id)
    if base_config is None:
        return f"Основная конфигурация `{extension_config.base_config_id}` для расширения не найдена в registry."

    safe_limit = clamp_workflow_limit(limit, default=10, maximum=50)
    base_repository = build_repository_for_runtime_config(base_config)
    extension_repository = build_repository_for_runtime_config(extension_config)
    try:
        if not base_repository.exists():
            return f"Graph projection основной конфигурации `{base_config.config_id}` недоступен."
        if not extension_repository.exists():
            return f"Graph projection расширения `{extension_config.config_id}` недоступен."

        kind_titles = (
            ("metadata", "Metadata Objects"),
            ("form", "Forms"),
            ("module", "Modules"),
            ("method", "Methods"),
            ("command", "Commands"),
        )
        comparisons = {
            kind: compare_overlay_kind(base_repository, extension_repository, kind, safe_limit)
            for kind, _ in kind_titles
        }

        lines = [
            "## Extension Overlay Report",
            "",
            f"- Base config: `{base_config.config_id}` ({base_config.config_name})",
            f"- Extension: `{extension_config.config_id}` ({extension_config.config_name})",
            f"- Base graph: `{base_repository.get_source_label()}`",
            f"- Extension graph: `{extension_repository.get_source_label()}`",
            "- Interpretation: `overlay` means the same stable entity key exists in base and extension; it does not yet prove a specific 1C extension annotation.",
        ]

        for kind, title in kind_titles:
            result = comparisons[kind]
            lines.extend([
                "",
                f"## {title}",
                "",
                f"- Base entities: `{result['base_total']}`",
                f"- Extension entities: `{result['extension_total']}`",
                f"- Overlay candidates: `{result['overlay_count']}`",
                f"- Extension-only entities: `{result['added_count']}`",
            ])
            if result["overlay_samples"]:
                lines.append("- Overlay samples:")
                lines.extend(f"  - `{label}`" for label in result["overlay_samples"])
            if result["added_samples"]:
                lines.append("- Extension-only samples:")
                lines.extend(f"  - `{label}`" for label in result["added_samples"])
            if result["annotation_counts"]:
                annotation_summary = ", ".join(
                    f"{name}={count}"
                    for name, count in sorted(result["annotation_counts"].items())
                )
                lines.append(f"- Extension annotations: `{annotation_summary}`")
                lines.append(f"- Resolved annotation targets: `{result['resolved_annotations']}`")
                lines.append(f"- Unresolved annotation targets: `{result['unresolved_annotations']}`")
                if result["unresolved_reasons"]:
                    reason_summary = ", ".join(
                        f"{name}={count}"
                        for name, count in sorted(result["unresolved_reasons"].items())
                    )
                    lines.append(f"- Unresolved reasons: `{reason_summary}`")

        lines.extend([
            "",
            "## Current Limitations",
            "",
            "- Unresolved annotation targets are kept unresolved when the expected base module does not contain the target; same-name methods in unrelated modules are not linked automatically.",
            "- Attribute-level metadata differences are not calculated yet.",
            "- Same-name entities are overlay candidates and require source verification before editing.",
            "",
            "## Next Actions",
            "",
            "- Inspect overlay method candidates before modifying the base configuration.",
            "- Use `switch_configuration` and `get_file_snippet` to compare concrete implementations.",
            "- Use `change_impact_report` in both scopes for a change candidate.",
        ])
        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка при построении base/extension overlay: {str(e)}"
    finally:
        base_repository.close()
        extension_repository.close()


@mcp.tool()
def switch_configuration(config_id: str) -> str:
    """
    Переключает активную конфигурацию MCP runtime на запись из config registry.
    """
    target_id = (config_id or "").strip()
    if not target_id:
        return "Укажите `config_id` из `list_configurations`."

    registered_configs = list_registered_configs()
    if not registered_configs:
        return "Config registry не найден. Переключение недоступно в env-only режиме."

    target_config = None
    normalized_target = re.sub(r"[^a-zA-Z0-9]+", "_", target_id.lower()).strip("_")
    for config in registered_configs:
        if config.config_id == normalized_target:
            target_config = config
            break

    if target_config is None:
        available = ", ".join(f"`{config.config_id}`" for config in registered_configs)
        return f"Конфигурация `{target_id}` не найдена. Доступно: {available}."

    previous_config_id = CONFIG_ID
    apply_runtime_config(target_config)
    reset_runtime_state()

    return "\n".join([
        "## Configuration Switched",
        "",
        f"- Previous config: `{previous_config_id}`",
        f"- Active config: `{CONFIG_ID}`",
        f"- Config kind: `{CONFIG_KIND}`",
        f"- Base config id: `{BASE_CONFIG_ID or 'none'}`",
        f"- Export path: `{EXPORT_PATH}`",
        f"- Collection: `{COLLECTION_NAME}`",
        f"- Graph source: `{graph_repository.get_source_label()}`",
    ])


@mcp.tool()
def get_dependencies(name: str, object_type: str = "") -> str:
    """
    Показывает зависимости объекта метаданных на основе GraphRepository:
    ссылки на другие объекты, связанные формы и привязанные модули.
    """
    try:
        ranked_results = rank_metadata_lookup_for_graph_tool(name, object_type)
        if not ranked_results:
            if object_type.strip():
                return f"Объект метаданных `{object_type}.{name}` не найден."
            return f"Объект метаданных `{name}` не найден."

        _, payload = ranked_results[0]
        object_type_value = payload.get("object_type", "")
        object_name_value = payload.get("object_name", "")
        object_label = f"{object_type_value}.{object_name_value}"

        graph_lines: list[str] = []
        form_lines: list[str] = []
        module_lines: list[str] = []

        if graph_repository.exists():
            node_id = make_metadata_node_id(object_type_value, object_name_value)
            graph_node = graph_repository.get_node(node_id)
            if graph_node:
                for edge, target in graph_repository.iter_edges(node_id, "outgoing"):
                    if edge.get("type") == "references_metadata" and target:
                        prefix = edge.get("section", "metadata")
                        if edge.get("container"):
                            prefix = f"{prefix}:{edge['container']}"
                        source_name = edge.get("source") or "unknown"
                        target_label = f"{target.get('object_type', 'unknown')}.{target.get('object_name', 'unknown')}"
                        graph_lines.append(f"- `{prefix}` -> `{source_name}` -> `{target_label}`")
                    elif edge.get("type") == "contains_form" and target:
                        owner_label = format_owner_label(
                            target.get("owner_type", ""),
                            target.get("owner_name", ""),
                            target.get("owner_object_type", ""),
                        )
                        form_lines.append(
                            f"- `{target.get('form_name', 'unknown')}` ({target.get('root_type', 'unknown')}) "
                            f"-> owner `{owner_label}` -> `{target.get('file_path', 'unknown')}`"
                        )
                    elif edge.get("type") == "contains_module" and target:
                        module_lines.append(
                            f"- `{target.get('module_path', 'unknown')}` -> `{target.get('file_path', 'unknown')}`"
                        )

        if not graph_lines and not form_lines and not module_lines and not graph_repository.exists():
            dependencies = extract_metadata_dependencies(payload.get("document", ""))
            if not dependencies:
                return (
                    f"## Dependencies\n\n"
                    f"- Object: `{object_label}`\n"
                    f"- File: `{payload.get('file_path')}`\n"
                    f"- Dependencies: `0`\n"
                )

            graph_lines = []
            for dep in dependencies:
                prefix = dep["section"]
                if dep["container"]:
                    prefix = f"{prefix}:{dep['container']}"
                graph_lines.append(
                    f"- `{prefix}` -> `{dep['source']}` -> `{dep['target_type']}.{dep['target_name']}`"
                )

        lines = [
            "## Dependencies",
            "",
            f"- Object: `{object_label}`",
            f"- File: `{payload.get('file_path')}`",
            f"- Metadata links: `{len(graph_lines)}`",
            f"- Forms: `{len(form_lines)}`",
            f"- Modules: `{len(module_lines)}`",
        ]

        if graph_lines:
            lines.extend([
                "",
                "## Metadata Links",
                "",
                *graph_lines,
            ])

        if form_lines:
            lines.extend([
                "",
                "## Forms",
                "",
                *form_lines,
            ])

        if module_lines:
            lines.extend([
                "",
                "## Modules",
                "",
                *module_lines,
            ])

        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка при получении зависимостей: {str(e)}"


@mcp.tool()
def get_file_snippet(file_path: str, start_line: int, end_line: int) -> str:
    """
    Получает конкретный фрагмент файла с диска по номерам строк.
    Полезно, если найденная процедура ссылается на соседний код или нужно посмотреть более широкий контекст.
    """
    path = Path(file_path)
    if not path.exists():
        return f"Ошибка: Файл {file_path} не найден."
    
    try:
        with open(path, 'r', encoding='utf-8-sig', errors='ignore') as f:
            lines = f.readlines()
        
        total_lines = len(lines)
        start = max(1, start_line)
        end = min(total_lines, end_line)
        
        snippet = lines[start-1:end]
        
        # Форматируем вывод с номерами строк
        formatted_lines = [f"{start + i}: {line.rstrip()}" for i, line in enumerate(snippet)]
        
        return (
            f"**Файл:** `{file_path}` (Показаны строки {start}-{end} из {total_lines})\n"
            f"```bsl\n" + "\n".join(formatted_lines) + "\n```"
        )
    except Exception as e:
        return f"Ошибка чтения файла: {str(e)}"

if __name__ == "__main__":
    # Запуск MCP сервера (по умолчанию запускается через stdio)
    mcp.run()

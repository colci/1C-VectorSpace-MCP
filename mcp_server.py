import os
import re
import json
from pathlib import Path
from typing import Iterable
from datetime import datetime
from dotenv import load_dotenv

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
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"

# Определение суффикса для разделения коллекций
if OPENAI_API_KEY:
    MODEL_SUFFIX = "openai"
elif "e5-large" in EMBEDDING_MODEL:
    MODEL_SUFFIX = "e5_large"
elif "MiniLM" in EMBEDDING_MODEL:
    MODEL_SUFFIX = "minilm"
else:
    # Безопасный суффикс для произвольных моделей
    clean_name = re.sub(r'[^a-zA-Z0-9]', '_', EMBEDDING_MODEL.split('/')[-1].lower())
    MODEL_SUFFIX = clean_name

# Настройки Qdrant
EXPORT_PATH = os.getenv("EXPORT_PATH", r"D:\Export\UNF")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION_NAME = f"1c_unf_configuration_{MODEL_SUFFIX}"
CACHE_FILE = Path(f"indexing_cache_{MODEL_SUFFIX}.json")

# Инициализация клиента Qdrant
qclient = QdrantClient(url=QDRANT_URL)

encoder = None
if OPENAI_API_KEY:
    from openai import OpenAI
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    print("MCP-сервер: Запуск с поддержкой эмбеддингов OpenAI.")
else:
    from fastembed import TextEmbedding
    print(f"MCP-сервер: Загрузка локальной модели FastEmbed: {EMBEDDING_MODEL}...")
    encoder = TextEmbedding(model_name=EMBEDDING_MODEL)
    print("MCP-сервер: Запуск с локальными эмбеддингами FastEmbed.")

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

CORE_NAVIGATION_METADATA_TYPES = {
    "Catalog",
    "Document",
    "InformationRegister",
    "AccumulationRegister",
}

LOW_PRIORITY_CODE_MODULE_TYPES = {
    "Reports",
}

SEMANTIC_LOOKUP_PATTERNS = (
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


def has_object_navigation_intent(query: str, detected_type: str | None = None) -> bool:
    if detected_type:
        return True

    if has_structural_intent(query):
        return True

    keywords = extract_keywords(query, COMMON_STOPWORDS)
    return len(keywords) >= 2


def has_register_intent(query: str, detected_type: str | None = None) -> bool:
    if detected_type in {"InformationRegister", "AccumulationRegister"}:
        return True

    register_prefixes = (
        "взаиморасчет",
        "расчет",
        "остат",
        "оборот",
        "движен",
        "денежн",
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
            if object_type in {"AccumulationRegister", "InformationRegister"}:
                score += 2.0
            elif object_type in LOW_SIGNAL_METADATA_TYPES:
                score -= 1.8

        ranked.append((score, hit))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


_metadata_payload_cache: dict[str, list[dict]] = {}
_code_signature_cache: list[dict] | None = None


def load_metadata_payloads(detected_type: str | None) -> list[dict]:
    cache_key = detected_type or "__all__"
    if cache_key in _metadata_payload_cache:
        return _metadata_payload_cache[cache_key]

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


def load_code_signatures() -> list[dict]:
    global _code_signature_cache
    if _code_signature_cache is not None:
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
            if object_type in {"AccumulationRegister", "InformationRegister"}:
                score += 2.2
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
    for pattern in SEMANTIC_LOOKUP_PATTERNS:
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
                if object_type in {"AccumulationRegister", "InformationRegister"}:
                    boosted_score += 3.0
                elif object_type in LOW_SIGNAL_METADATA_TYPES:
                    boosted_score -= 2.5

            if current is None or boosted_score > current[0]:
                ranked_map[key] = (boosted_score, payload)

    ranked = list(ranked_map.values())
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def load_code_payload(signature: dict) -> dict | None:
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


def lexical_code_results(query: str):
    keywords = extract_keywords(query, CODE_STOPWORDS)
    phrase = " ".join(keywords)
    report_intent = has_report_intent(query)
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

        if score > 0:
            ranked.append((score, signature))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def rerank_code_results(query: str, results):
    keywords = extract_keywords(query, CODE_STOPWORDS)
    phrase = " ".join(keywords)
    report_intent = has_report_intent(query)
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


def format_code_lookup_result(payload: dict, score: float, index: int) -> str:
    return (
        f"### Результат {index} (Точность совпадения: {score:.4f})\n"
        f"**Модуль:** `{payload.get('module_path')}`\n"
        f"**Метод:** `{payload.get('method_name')}` (Строки: {payload.get('start_line')}-{payload.get('end_line')})\n"
        f"**Файл:** `{payload.get('file_path')}`\n"
        f"**Код:**\n```bsl\n{payload.get('document', '')}\n```\n"
        f"{'-'*40}"
    )


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
    scan_filter = build_code_filter(module_type=module_type)

    for point in iter_qdrant_points(scan_filter):
        payload = point.payload or {}
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
            ranked.append((score, payload))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def find_method_candidates(
    method_name: str,
    module_path: str | None,
    module_type: str | None,
    limit: int,
) -> list[tuple[float, dict]]:
    lookup_query = method_name if not module_path else f"{module_path} {method_name}"
    vector = get_query_embedding(lookup_query)
    fetch_limit = max(limit * 20, 100)
    code_filter = build_code_filter(module_type=module_type)

    response = qclient.query_points(
        collection_name=COLLECTION_NAME,
        query=vector,
        query_filter=code_filter,
        limit=fetch_limit
    )

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


def collect_export_scan(cache: dict) -> dict:
    export_dir = Path(EXPORT_PATH)
    if not export_dir.exists():
        return {
            "exists": False,
            "root": str(export_dir),
            "indexed_bsl_files": 0,
            "indexed_xml_files": 0,
            "changed_files": 0,
        }

    indexed_bsl_files = 0
    indexed_xml_files = 0
    changed_files = 0

    for root, _, files in os.walk(export_dir):
        for file in files:
            filepath = Path(root) / file
            if not (file.endswith(".bsl") or file.endswith(".xml")):
                continue

            if file in {"ConfigDumpInfo.xml", "Configuration.xml"}:
                continue

            if file.endswith(".xml") and ("Forms" in filepath.parts or "Templates" in filepath.parts):
                continue

            if file.endswith(".bsl"):
                indexed_bsl_files += 1
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

def get_query_embedding(text: str):
    """
    Генерирует вектор для поискового запроса через OpenAI или FastEmbed
    """
    if OPENAI_API_KEY:
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

        vector = get_query_embedding(query)
        fetch_limit = max(limit * 50, 200)

        response = qclient.query_points(
            collection_name=COLLECTION_NAME,
            query=vector,
            query_filter=code_filter,
            limit=fetch_limit
        )
        ranked_results = rerank_code_results(query, response.points)
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

        # Генерация вектора запроса
        vector = get_query_embedding(query)
        fetch_limit = max(limit * 20, 80)

        # Выполняем поиск через современный API query_points
        response = qclient.query_points(
            collection_name=COLLECTION_NAME,
            query=vector,
            query_filter=meta_filter,
            limit=fetch_limit
        )
        ranked_results = rerank_metadata_results(query, response.points, detected_type)
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
def index_status(include_fs_scan: bool = True) -> str:
    """
    Показывает состояние индекса: коллекцию Qdrant, кэш индексации и,
    при необходимости, расхождение экспорта на диске с кэшем.
    """
    try:
        collection_info = qclient.get_collection(COLLECTION_NAME)
    except Exception as e:
        return (
            f"Ошибка при получении статуса индекса: не удалось открыть коллекцию "
            f"`{COLLECTION_NAME}` в Qdrant ({str(e)})."
        )

    cache = load_index_cache()
    cache_exists = CACHE_FILE.exists()
    cache_mtime = CACHE_FILE.stat().st_mtime if cache_exists else None

    total_points = count_collection_points()
    code_points = count_collection_points("code")
    metadata_points = count_collection_points("metadata")

    vectors_config = collection_info.config.params.vectors
    vector_size = getattr(vectors_config, "size", "unknown")
    distance = getattr(getattr(vectors_config, "distance", None), "value", "unknown")
    points_count = getattr(collection_info, "points_count", None)
    indexed_vectors_count = getattr(collection_info, "indexed_vectors_count", None)

    lines = [
        "## Index Status",
        "",
        f"- Collection: `{COLLECTION_NAME}`",
        f"- Qdrant URL: `{QDRANT_URL}`",
        f"- Embedding model: `{EMBEDDING_MODEL if not OPENAI_API_KEY else OPENAI_EMBEDDING_MODEL}`",
        f"- Vector size: `{vector_size}`",
        f"- Distance: `{distance}`",
        f"- Collection points_count: `{points_count}`",
        f"- Indexed vectors count: `{indexed_vectors_count}`",
        f"- Exact total chunks: `{total_points}`",
        f"- Exact code chunks: `{code_points}`",
        f"- Exact metadata chunks: `{metadata_points}`",
        f"- Export path: `{EXPORT_PATH}`",
        f"- Cache file: `{CACHE_FILE}`",
        f"- Cache exists: `{cache_exists}`",
        f"- Cache entries: `{len(cache)}`",
        f"- Cache updated at: `{format_timestamp(cache_mtime)}`",
    ]

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
def get_dependencies(name: str, object_type: str = "") -> str:
    """
    Показывает зависимости объекта метаданных на основе его карточки:
    ссылки из реквизитов, табличных частей, измерений и ресурсов.
    """
    try:
        ranked_results = rank_metadata_lookup(name, object_type)
        if not ranked_results:
            if object_type.strip():
                return f"Объект метаданных `{object_type}.{name}` не найден."
            return f"Объект метаданных `{name}` не найден."

        _, payload = ranked_results[0]
        dependencies = extract_metadata_dependencies(payload.get("document", ""))
        object_label = f"{payload.get('object_type')}.{payload.get('object_name')}"

        if not dependencies:
            return (
                f"## Dependencies\n\n"
                f"- Object: `{object_label}`\n"
                f"- File: `{payload.get('file_path')}`\n"
                f"- Dependencies: `0`\n"
            )

        grouped_lines = []
        for dep in dependencies:
            prefix = dep["section"]
            if dep["container"]:
                prefix = f"{prefix}:{dep['container']}"
            grouped_lines.append(
                f"- `{prefix}` -> `{dep['source']}` -> `{dep['target_type']}.{dep['target_name']}`"
            )

        return "\n".join([
            "## Dependencies",
            "",
            f"- Object: `{object_label}`",
            f"- File: `{payload.get('file_path')}`",
            f"- Dependencies: `{len(dependencies)}`",
            "",
            "## Links",
            "",
            *grouped_lines,
        ])
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

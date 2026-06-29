import os
import re
import json
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from dotenv import load_dotenv
from tqdm import tqdm
import psutil
import time
import gc

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
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
MAX_RAM_PERCENT = float(os.getenv("MAX_RAM_PERCENT", "70"))

# Определение суффикса для разделения коллекций и кэшей
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

EXPORT_PATH = os.getenv("EXPORT_PATH", r"D:\Export\UNF")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION_NAME = f"1c_unf_configuration_{MODEL_SUFFIX}"
CACHE_FILE = Path(f"indexing_cache_{MODEL_SUFFIX}.json")

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
qclient = QdrantClient(url=QDRANT_URL)

# Выбор энкодера и размерности векторов
encoder = None
if OPENAI_API_KEY:
    from openai import OpenAI
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    vector_size = 1536
    print(f"Используются эмбеддинги OpenAI: {OPENAI_EMBEDDING_MODEL} (размерность {vector_size})")
else:
    from fastembed import TextEmbedding
    
    # Ограничение потоков процессора для FastEmbed (предотвращает зависания системы)
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
            
    print(f"Загрузка локальной модели FastEmbed: {EMBEDDING_MODEL} с лимитом потоков CPU: {fastembed_threads}...")
    encoder = TextEmbedding(model_name=EMBEDDING_MODEL, threads=fastembed_threads)
    # Вычисляем размерность вектора динамически на основе тестового эмбеддинга
    vector_size = len(next(encoder.embed(["dummy"])))
    print(f"Модель FastEmbed загружена. Размерность векторов: {vector_size}")

# Создание коллекции и индексов
def setup_collection():
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

# Генерация эмбеддингов
def get_embedding(text):
    if OPENAI_API_KEY:
        response = openai_client.embeddings.create(
            input=[text],
            model=OPENAI_EMBEDDING_MODEL
        )
        return response.data[0].embedding
    else:
        return list(encoder.embed([text]))[0].tolist()

# Кэширование состояния файлов
def load_cache():
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_cache(cache, pbar=None):
    try:
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

def parse_bsl_file(filepath: Path):
    chunks = []
    current_method = None
    method_name = ""
    method_lines = []
    start_line = 0
    comments_buffer = []

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

        match = METHOD_START_RE.match(line)
        if match and not current_method:
            method_name = match.group(1)
            current_method = "METHOD"
            start_line = line_num
            method_lines = list(comments_buffer) + [line]
            comments_buffer = []
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
                    "end_line": end_line
                })
                current_method = None
                method_lines = []
                comments_buffer = []
        
        if not stripped.startswith("//") and not current_method:
            comments_buffer = []

    return chunks

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
                res_list.append(f"  - [Ресурс] {res_name} ({res_type}): {res_synonym}")
        if res_list:
            lines.append("Ресурсы:")
            lines.extend(res_list)

    card_text = "\n".join(lines)
    return {
        "object_type": obj_type,
        "object_name": name_val,
        "synonym": synonym_val,
        "card_text": card_text
    }

def process_and_index():
    check_memory_limit(MAX_RAM_PERCENT)
    print(f"Поиск файлов в {EXPORT_PATH}...")
    export_dir = Path(EXPORT_PATH)
    
    if not export_dir.exists():
        print(f"Ошибка: Каталог выгрузки {EXPORT_PATH} не найден!")
        return

    setup_collection()
    cache = load_cache()

    # Фильтр для частичной индексации (полезно при тестировании)
    index_filter = os.getenv("INDEX_FILTER")
    if index_filter:
        print(f"Применяется фильтр индексации: '{index_filter}'")

    # Сначала сканируем всю файловую систему и собираем файлы
    all_files = []
    for root, dirs, files in os.walk(EXPORT_PATH):
        for file in files:
            filepath = Path(root) / file
            # Индексируем только BSL и XML
            if file.endswith('.bsl') or file.endswith('.xml'):
                # Пропускаем служебные XML
                if file in ["ConfigDumpInfo.xml", "Configuration.xml"]:
                    continue
                # Пропускаем вложенные XML форм/макетов (для экономии памяти и времени)
                if file.endswith('.xml') and ("Forms" in filepath.parts or "Templates" in filepath.parts):
                    continue
                
                # Фильтруем файлы по INDEX_FILTER, если он задан
                if index_filter and index_filter not in str(filepath):
                    continue
                
                all_files.append(filepath)

    print(f"Всего найдено файлов для проверки: {len(all_files)}")
    
    # Лимит чанков в одной порции для отправки в Qdrant (по умолчанию 100 для экономии RAM)
    CHUNK_BATCH_SIZE = int(os.getenv("INDEX_BATCH_SIZE", "100"))
    
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
            
            log_msg(f"Вычисление эмбеддингов для {len(docs)} чанков...")
            try:
                batch_ids = [str(uuid.uuid4()) for _ in range(len(docs))]
                
                if OPENAI_API_KEY:
                    log_msg(f"Отправка запроса к OpenAI API ({len(docs)} чанков)...")
                    response = openai_client.embeddings.create(
                        input=docs,
                        model=OPENAI_EMBEDDING_MODEL
                    )
                    batch_vectors = [item.embedding for item in response.data]
                else:
                    batch_vectors = []
                    embeddings_gen = encoder.embed(docs, batch_size=32)
                    for vector in tqdm(
                        embeddings_gen,
                        total=len(docs),
                        desc="  ├ Вычисление векторов",
                        leave=False,
                        bar_format="{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"
                    ):
                        batch_vectors.append(vector.tolist())
                    
                log_msg(f"Загрузка {len(docs)} векторов в коллекцию Qdrant '{COLLECTION_NAME}'...")
                qclient.upsert(
                    collection_name=COLLECTION_NAME,
                    points=models.Batch(
                        ids=batch_ids,
                        vectors=batch_vectors,
                        payloads=payloads
                    )
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
        if cache.get(rel_path) != mtime:
            changed_files.append((filepath, rel_path, mtime))
            
    if not changed_files:
        print("Все файлы актуальны. Изменений не обнаружено.")
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
            # Логическое имя модуля
            parts = filepath.relative_to(export_dir).parts
            module_type = parts[0]
            module_name = parts[1] if len(parts) >= 2 else filepath.name
            
            if "ObjectModule.bsl" in filepath.name:
                module_path = f"{module_type}.{module_name}.МодульОбъекта"
            elif "ManagerModule.bsl" in filepath.name:
                module_path = f"{module_type}.{module_name}.МодульМенеджера"
            else:
                module_path = f"{module_type}.{module_name}"

            methods = parse_bsl_file(filepath)
            for m in methods:
                context_text = (
                    f"Модуль: {module_path}\n"
                    f"Тип: {module_type}\n"
                    f"Метод: {m['method_name']}\n"
                    f"Код:\n{m['body']}"
                )
                file_chunks.append((context_text, {
                    "document": context_text,
                    "module_path": module_path,
                    "module_type": module_type,
                    "method_name": m['method_name'],
                    "start_line": m['start_line'],
                    "end_line": m['end_line'],
                    "file_path": str(filepath),
                    "chunk_type": "code"
                }))
        
        elif filepath.suffix == '.xml':
            meta_info = parse_metadata_xml(filepath)
            if meta_info:
                file_chunks.append((meta_info['card_text'], {
                    "document": meta_info['card_text'],
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
    print("Индексация успешно завершена!")

if __name__ == "__main__":
    process_and_index()

import os
from pathlib import Path
from dotenv import load_dotenv

# Отключаем прокси для локальных запросов к Qdrant, чтобы избежать ошибки 503 на Windows
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

from qdrant_client import QdrantClient
from qdrant_client.http import models
from mcp.server.fastmcp import FastMCP

# Загрузка переменных окружения
load_dotenv()

# Настройки
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION_NAME = "1c_unf_configuration"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_MODEL = "intfloat/multilingual-e5-large"  # Для FastEmbed

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
        # Фильтр для поиска только по программному коду
        code_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="chunk_type",
                    match=models.MatchValue(value="code")
                )
            ]
        )

        # Генерация вектора запроса
        vector = get_query_embedding(query)

        # Выполняем поиск через современный API query_points
        response = qclient.query_points(
            collection_name=COLLECTION_NAME,
            query=vector,
            query_filter=code_filter,
            limit=limit
        )
        results = response.points

        if not results:
            return "Ничего не найдено по вашему запросу в коде."

        formatted_results = []
        for i, hit in enumerate(results):
            payload = hit.payload
            doc_text = payload.get("document", "")
            formatted_results.append(
                f"### Результат {i+1} (Сходство: {hit.score:.4f})\n"
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
        # Фильтр для поиска только по метаданным XML
        meta_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="chunk_type",
                    match=models.MatchValue(value="metadata")
                )
            ]
        )

        # Генерация вектора запроса
        vector = get_query_embedding(query)

        # Выполняем поиск через современный API query_points
        response = qclient.query_points(
            collection_name=COLLECTION_NAME,
            query=vector,
            query_filter=meta_filter,
            limit=limit
        )
        results = response.points

        if not results:
            return "Ничего не найдено по вашему запросу в метаданных."

        formatted_results = []
        for i, hit in enumerate(results):
            payload = hit.payload
            doc_text = payload.get("document", "")
            formatted_results.append(
                f"### Результат {i+1} (Сходство: {hit.score:.4f})\n"
                f"**Объект:** {payload.get('object_type')}.{payload.get('object_name')} (Синоним: {payload.get('synonym')})\n"
                f"**Файл:** `{payload.get('file_path')}`\n"
                f"**Структура реквизитов:**\n```text\n{doc_text}\n```\n"
                f"{'-'*40}"
            )
        
        return "\n\n".join(formatted_results)

    except Exception as e:
        return f"Ошибка при поиске в метаданных: {str(e)}"

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

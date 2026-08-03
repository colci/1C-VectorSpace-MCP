import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


REPO_ROOT = Path(__file__).resolve().parent
INDEX_SCHEMA_VERSION = 4
INDEX_SCHEMA_CACHE_KEY = "__index_schema_version__"


def normalize_config_key(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return normalized or "default"


def resolve_embedding_provider(env: Mapping[str, str]) -> str:
    raw_provider = str(env.get("EMBEDDING_PROVIDER", "local")).strip().lower()
    aliases = {
        "local": "local",
        "fastembed": "local",
        "openai": "openai",
    }
    if raw_provider not in aliases:
        raise ValueError(
            f"Unsupported EMBEDDING_PROVIDER `{raw_provider}`. Use `local` or `openai`."
        )
    return aliases[raw_provider]


def compute_model_suffix(env: Mapping[str, str]) -> str:
    embedding_provider = resolve_embedding_provider(env)
    embedding_model = env.get("EMBEDDING_MODEL", "intfloat/multilingual-e5-large")

    if embedding_provider == "openai":
        return "openai"
    if "e5-large" in embedding_model:
        return "e5_large"
    if "MiniLM" in embedding_model:
        return "minilm"

    clean_name = re.sub(r"[^a-zA-Z0-9]", "_", embedding_model.split("/")[-1].lower())
    return clean_name or "default"


def _resolve_path(path_value: str, base_dir: Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


@dataclass(frozen=True)
class RuntimeConfig:
    export_path: str
    qdrant_url: str
    config_name: str
    config_id: str
    config_profile: str
    platform_version: str
    collection_name: str
    cache_file: Path
    graph_cache_file: Path
    config_kind: str
    base_config_id: str
    description: str
    registry_file: Path | None
    is_default: bool = False


def find_registry_file(env: Mapping[str, str] | None = None) -> Path | None:
    current_env = env or os.environ
    raw_path = current_env.get("CONFIG_REGISTRY_FILE", "").strip()
    if raw_path:
        return _resolve_path(raw_path, REPO_ROOT)

    fallback = REPO_ROOT / "config_registry.json"
    if fallback.exists():
        return fallback
    return None


def _load_registry_items(registry_file: Path | None) -> list[dict]:
    if registry_file is None or not registry_file.exists():
        return []

    try:
        data = json.loads(registry_file.read_text(encoding="utf-8"))
    except Exception:
        return []

    if isinstance(data, dict):
        items = data.get("configs")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        return []

    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    return []


def _build_runtime_config_from_item(
    item: dict,
    env: Mapping[str, str],
    registry_file: Path,
    model_suffix: str,
) -> RuntimeConfig | None:
    registry_dir = registry_file.parent
    config_name = str(item.get("config_name") or item.get("name") or "").strip()
    config_id = normalize_config_key(str(item.get("config_id") or config_name))
    export_path_value = str(item.get("export_path") or "").strip()

    if not config_id or not export_path_value:
        return None

    export_path = str(_resolve_path(export_path_value, registry_dir))
    config_profile = str(item.get("config_profile") or env.get("CONFIG_PROFILE", "generic")).strip() or "generic"
    platform_version = str(item.get("platform_version") or env.get("PLATFORM_VERSION", "")).strip()
    collection_name = str(item.get("collection_name") or f"1c_configuration_{config_id}_{model_suffix}").strip()
    cache_file = _resolve_path(
        str(item.get("index_cache_file") or f"indexing_cache_{config_id}_{model_suffix}.json"),
        registry_dir,
    )
    graph_cache_file = _resolve_path(
        str(item.get("graph_cache_file") or f"graph_cache_{config_id}.json"),
        registry_dir,
    )
    config_kind = str(item.get("config_kind") or item.get("kind") or "configuration").strip().lower() or "configuration"
    base_config_id = normalize_config_key(str(item.get("base_config_id") or item.get("extends") or "").strip()) if (item.get("base_config_id") or item.get("extends")) else ""
    description = str(item.get("description") or "").strip()
    is_default = bool(item.get("default"))

    return RuntimeConfig(
        export_path=export_path,
        qdrant_url=env.get("QDRANT_URL", "http://localhost:6333"),
        config_name=config_name or config_id,
        config_id=config_id,
        config_profile=config_profile,
        platform_version=platform_version,
        collection_name=collection_name,
        cache_file=cache_file,
        graph_cache_file=graph_cache_file,
        config_kind=config_kind,
        base_config_id=base_config_id,
        description=description,
        registry_file=registry_file,
        is_default=is_default,
    )


def list_registered_configs(env: Mapping[str, str] | None = None) -> list[RuntimeConfig]:
    current_env = env or os.environ
    registry_file = find_registry_file(current_env)
    if registry_file is None:
        return []

    model_suffix = compute_model_suffix(current_env)
    configs: list[RuntimeConfig] = []
    for item in _load_registry_items(registry_file):
        runtime_config = _build_runtime_config_from_item(item, current_env, registry_file, model_suffix)
        if runtime_config:
            configs.append(runtime_config)
    return configs


def resolve_runtime_config(
    env: Mapping[str, str] | None = None,
    requested_config_id: str | None = None,
) -> RuntimeConfig:
    current_env = env or os.environ
    registered_configs = list_registered_configs(current_env)
    selected_key = normalize_config_key(
        requested_config_id
        or current_env.get("ACTIVE_CONFIG_ID", "")
        or current_env.get("CONFIG_ID", "")
        or current_env.get("CONFIG_NAME", "")
    )

    if registered_configs:
        for config in registered_configs:
            if config.config_id == selected_key:
                return config
        for config in registered_configs:
            if config.is_default:
                return config
        return registered_configs[0]

    model_suffix = compute_model_suffix(current_env)
    config_name = current_env.get("CONFIG_NAME", "default")
    config_id = normalize_config_key(current_env.get("CONFIG_ID", config_name))

    return RuntimeConfig(
        export_path=current_env.get("EXPORT_PATH", r"D:\Export\1C"),
        qdrant_url=current_env.get("QDRANT_URL", "http://localhost:6333"),
        config_name=config_name,
        config_id=config_id,
        config_profile=current_env.get("CONFIG_PROFILE", "generic"),
        platform_version=current_env.get("PLATFORM_VERSION", ""),
        collection_name=current_env.get("COLLECTION_NAME", f"1c_configuration_{config_id}_{model_suffix}"),
        cache_file=Path(current_env.get("INDEX_CACHE_FILE", f"indexing_cache_{config_id}_{model_suffix}.json")),
        graph_cache_file=Path(current_env.get("GRAPH_CACHE_FILE", f"graph_cache_{config_id}.json")),
        config_kind=current_env.get("CONFIG_KIND", "configuration").strip().lower() or "configuration",
        base_config_id=normalize_config_key(current_env.get("BASE_CONFIG_ID", "")) if current_env.get("BASE_CONFIG_ID", "").strip() else "",
        description="",
        registry_file=find_registry_file(current_env),
        is_default=True,
    )


def sync_runtime_env(runtime_config: RuntimeConfig, env: dict[str, str] | None = None) -> None:
    target_env = env if env is not None else os.environ
    target_env["EXPORT_PATH"] = runtime_config.export_path
    target_env["QDRANT_URL"] = runtime_config.qdrant_url
    target_env["CONFIG_NAME"] = runtime_config.config_name
    target_env["CONFIG_ID"] = runtime_config.config_id
    target_env["ACTIVE_CONFIG_ID"] = runtime_config.config_id
    target_env["CONFIG_PROFILE"] = runtime_config.config_profile
    target_env["PLATFORM_VERSION"] = runtime_config.platform_version
    target_env["COLLECTION_NAME"] = runtime_config.collection_name
    target_env["INDEX_CACHE_FILE"] = str(runtime_config.cache_file)
    target_env["GRAPH_CACHE_FILE"] = str(runtime_config.graph_cache_file)
    target_env["CONFIG_KIND"] = runtime_config.config_kind
    if runtime_config.base_config_id:
        target_env["BASE_CONFIG_ID"] = runtime_config.base_config_id
    else:
        target_env.pop("BASE_CONFIG_ID", None)
    if runtime_config.registry_file:
        target_env["CONFIG_REGISTRY_FILE"] = str(runtime_config.registry_file)

import json
import tempfile
from pathlib import Path

from config_runtime import (
    compute_model_suffix,
    list_registered_configs,
    resolve_runtime_config,
    resolve_embedding_provider,
    sync_runtime_env,
)


def write_registry(path: Path) -> None:
    registry = {
        "configs": [
            {
                "config_id": "do_cf",
                "config_name": "Документооборот",
                "export_path": "exports/cf",
                "config_profile": "generic",
                "platform_version": "8.3.27",
                "config_kind": "main",
                "default": True,
            },
            {
                "config_id": "do_cfe",
                "config_name": "Расширение",
                "export_path": "exports/cfe",
                "config_profile": "generic",
                "platform_version": "8.3.21",
                "config_kind": "extension",
                "base_config_id": "do_cf",
            },
        ],
    }
    path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    assert resolve_embedding_provider({}) == "local"
    assert resolve_embedding_provider({"EMBEDDING_PROVIDER": "fastembed"}) == "local"
    assert compute_model_suffix({"EMBEDDING_PROVIDER": "local", "EMBEDDING_MODEL": "intfloat/multilingual-e5-large"}) == "e5_large"
    assert compute_model_suffix({"EMBEDDING_PROVIDER": "local", "EMBEDDING_MODEL": "sentence-transformers/all-MiniLM-L6-v2"}) == "minilm"
    assert compute_model_suffix({"EMBEDDING_PROVIDER": "openai"}) == "openai"
    try:
        resolve_embedding_provider({"EMBEDDING_PROVIDER": "unknown"})
    except ValueError as error:
        assert "Unsupported EMBEDDING_PROVIDER" in str(error)
    else:
        raise AssertionError("unknown embedding provider should fail")

    with tempfile.TemporaryDirectory(prefix="runtime-config-smoke-") as tmpdir:
        registry_file = Path(tmpdir) / "config_registry.json"
        write_registry(registry_file)

        env = {
            "CONFIG_REGISTRY_FILE": str(registry_file),
            "EMBEDDING_PROVIDER": "local",
            "EMBEDDING_MODEL": "sentence-transformers/all-MiniLM-L6-v2",
            "QDRANT_URL": "http://qdrant.example:6333",
        }

        configs = list_registered_configs(env)
        assert [config.config_id for config in configs] == ["do_cf", "do_cfe"]
        assert configs[0].is_default is True
        assert configs[1].config_kind == "extension"
        assert configs[1].base_config_id == "do_cf"
        assert configs[1].collection_name == "1c_configuration_do_cfe_minilm"
        assert configs[1].export_path == str((registry_file.parent / "exports/cfe").resolve())

        default_config = resolve_runtime_config(env)
        assert default_config.config_id == "do_cf"

        extension_config = resolve_runtime_config(env, requested_config_id="do_cfe")
        assert extension_config.config_id == "do_cfe"
        assert extension_config.base_config_id == "do_cf"

        runtime_env = {"BASE_CONFIG_ID": "stale"}
        sync_runtime_env(extension_config, runtime_env)
        assert runtime_env["ACTIVE_CONFIG_ID"] == "do_cfe"
        assert runtime_env["BASE_CONFIG_ID"] == "do_cf"
        assert runtime_env["GRAPH_CACHE_FILE"].endswith("graph_cache_do_cfe.json")

        sync_runtime_env(default_config, runtime_env)
        assert runtime_env["ACTIVE_CONFIG_ID"] == "do_cf"
        assert "BASE_CONFIG_ID" not in runtime_env

        fallback_env = {
            "CONFIG_REGISTRY_FILE": str(Path(tmpdir) / "missing.json"),
            "CONFIG_NAME": "Standalone Config",
            "CONFIG_ID": "Standalone Config",
            "EXPORT_PATH": r"D:\Export\Standalone",
            "EMBEDDING_PROVIDER": "openai",
            "QDRANT_URL": "http://localhost:6333",
        }
        fallback_config = resolve_runtime_config(fallback_env)
        assert fallback_config.config_id == "standalone_config"
        assert fallback_config.collection_name == "1c_configuration_standalone_config_openai"
        assert fallback_config.registry_file == Path(fallback_env["CONFIG_REGISTRY_FILE"])

        list_registry_file = Path(tmpdir) / "list_registry.json"
        list_registry_file.write_text(
            json.dumps([
                {
                    "name": "List Shape Config",
                    "export_path": "exports/list",
                    "collection_name": "custom_collection",
                    "index_cache_file": "cache/custom_index.json",
                    "graph_cache_file": "cache/custom_graph.json",
                },
                {"config_id": "broken_without_export_path"},
            ], ensure_ascii=False),
            encoding="utf-8",
        )
        list_env = {
            "CONFIG_REGISTRY_FILE": str(list_registry_file),
            "EMBEDDING_PROVIDER": "local",
        }
        list_configs = list_registered_configs(list_env)
        assert len(list_configs) == 1
        assert list_configs[0].config_id == "list_shape_config"
        assert list_configs[0].collection_name == "custom_collection"
        assert list_configs[0].cache_file == (list_registry_file.parent / "cache/custom_index.json").resolve()
        assert list_configs[0].graph_cache_file == (list_registry_file.parent / "cache/custom_graph.json").resolve()

        invalid_registry_file = Path(tmpdir) / "invalid_registry.json"
        invalid_registry_file.write_text("{", encoding="utf-8")
        assert list_registered_configs({
            "CONFIG_REGISTRY_FILE": str(invalid_registry_file),
            "EMBEDDING_PROVIDER": "local",
        }) == []

    print("[runtime-config-smoke] success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

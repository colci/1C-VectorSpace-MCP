import json
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from pathlib import Path


class GraphWriter(ABC):
    @property
    @abstractmethod
    def target_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def write(self, graph_projection: dict, pbar=None) -> None:
        raise NotImplementedError


class JsonGraphWriter(GraphWriter):
    def __init__(self, graph_file: Path):
        self.graph_file = Path(graph_file)

    @property
    def target_name(self) -> str:
        return f"json:{self.graph_file}"

    def write(self, graph_projection: dict, pbar=None) -> None:
        tmp_file = self.graph_file.with_suffix(f"{self.graph_file.suffix}.tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(graph_projection, f, ensure_ascii=False, indent=2)
        tmp_file.replace(self.graph_file)


class MemgraphGraphWriter(GraphWriter):
    KIND_TO_LABEL = {
        "metadata": "MetadataObject",
        "form": "Form",
        "form_element": "FormElement",
        "command": "Command",
        "handler": "Handler",
        "module": "Module",
        "method": "Method",
    }
    NODE_EXCLUDED_PROPERTIES = {
        "document",
    }

    def __init__(
        self,
        uri: str,
        username: str,
        password: str,
        database: str = "",
        batch_size: int = 1000,
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 2.0,
        progress_every: int = 25,
    ):
        self.uri = uri
        self.username = username
        self.password = password
        self.database = database.strip()
        self.batch_size = batch_size
        self.retry_attempts = max(1, retry_attempts)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self.progress_every = max(1, progress_every)
        self._driver = None

    @property
    def target_name(self) -> str:
        return f"memgraph:{self.uri}"

    def _get_driver(self):
        if self._driver is None:
            from neo4j import GraphDatabase

            driver_kwargs = {"keep_alive": True}
            if self.username:
                driver_kwargs["auth"] = (self.username, self.password)
            self._driver = GraphDatabase.driver(self.uri, **driver_kwargs)
        return self._driver

    def _close_driver(self) -> None:
        if self._driver is None:
            return
        try:
            self._driver.close()
        except Exception:
            pass
        finally:
            self._driver = None

    def _session_kwargs(self) -> dict:
        if self.database:
            return {"database": self.database}
        return {}

    def _log(self, message: str, pbar=None) -> None:
        if pbar:
            pbar.write(message)
        else:
            print(message)

    def _is_retryable_error(self, error: Exception) -> bool:
        try:
            from neo4j.exceptions import ServiceUnavailable, SessionExpired

            if isinstance(error, (ServiceUnavailable, SessionExpired)):
                return True
        except Exception:
            pass

        normalized = str(error).lower()
        return any(
            marker in normalized
            for marker in (
                "defunct connection",
                "failed to read",
                "connection reset",
                "connection aborted",
                "service unavailable",
                "timed out",
                "no data",
            )
        )

    def _run_query(self, query: str, pbar=None, log_context: str = "", **params) -> None:
        last_error = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                driver = self._get_driver()
                with driver.session(**self._session_kwargs()) as session:
                    session.run(query, **params).consume()
                return
            except Exception as error:
                last_error = error
                should_retry = attempt < self.retry_attempts and self._is_retryable_error(error)
                if not should_retry:
                    raise

                context_suffix = f" ({log_context})" if log_context else ""
                self._log(
                    f"Memgraph: повтор подключения{context_suffix}, "
                    f"попытка {attempt + 1}/{self.retry_attempts}: {error}",
                    pbar=pbar,
                )
                self._close_driver()
                if self.retry_backoff_seconds:
                    time.sleep(self.retry_backoff_seconds * attempt)

        if last_error:
            raise last_error

    def _run_schema_query(self, query: str, pbar=None, log_context: str = "") -> None:
        try:
            self._run_query(query, pbar=pbar, log_context=log_context)
        except Exception as error:
            normalized = str(error).lower()
            if "already exists" in normalized or "existing index" in normalized:
                return
            raise

    def _ensure_indexes(self, pbar=None) -> None:
        self._log("Memgraph: проверка structural indexes...", pbar=pbar)
        for query, context in (
            (
                "CREATE INDEX ON :GraphProjectionMeta(config_id)",
                "index GraphProjectionMeta(config_id)",
            ),
            (
                "CREATE INDEX ON :GraphNode(config_id)",
                "index GraphNode(config_id)",
            ),
            (
                "CREATE INDEX ON :GraphNode(id)",
                "index GraphNode(id)",
            ),
            (
                "CREATE INDEX ON :GraphNode(kind)",
                "index GraphNode(kind)",
            ),
            (
                "CREATE INDEX ON :GraphNode(object_type)",
                "index GraphNode(object_type)",
            ),
            (
                "CREATE INDEX ON :GraphNode(object_name)",
                "index GraphNode(object_name)",
            ),
            (
                "CREATE INDEX ON :GraphNode(method_name)",
                "index GraphNode(method_name)",
            ),
            (
                "CREATE INDEX ON :GraphNode(module_path)",
                "index GraphNode(module_path)",
            ),
        ):
            self._run_schema_query(query, pbar=pbar, log_context=context)

    def _log_batch_progress(
        self,
        entity_label: str,
        batch_index: int,
        total_batches: int,
        pbar=None,
    ) -> None:
        if batch_index == 1 or batch_index == total_batches or batch_index % self.progress_every == 0:
            self._log(
                f"Memgraph: запись {entity_label}, batch {batch_index}/{total_batches}...",
                pbar=pbar,
            )

    def _sanitize_node_properties(self, node: dict) -> dict:
        # Keep Memgraph as a structural graph; long text payloads stay in JSON/Qdrant.
        return {
            key: value
            for key, value in node.items()
            if key not in self.NODE_EXCLUDED_PROPERTIES
        }

    def write(self, graph_projection: dict, pbar=None) -> None:
        config = graph_projection.get("config", {}) if isinstance(graph_projection, dict) else {}
        config_id = str(config.get("config_id") or "default")
        config_name = str(config.get("config_name") or "")
        config_profile = str(config.get("config_profile") or "")
        nodes = graph_projection.get("nodes") or []
        edges = graph_projection.get("edges") or []
        stats = graph_projection.get("stats", {}) if isinstance(graph_projection, dict) else {}
        generated_at = str(graph_projection.get("generated_at") or "")

        self._log(
            f"Запись graph projection в Memgraph `{self.uri}` "
            f"(config_id='{config_id}', nodes={len(nodes)}, edges={len(edges)})...",
            pbar=pbar,
        )

        self._ensure_indexes(pbar=pbar)

        self._run_query(
            "MATCH (m:GraphProjectionMeta {config_id: $config_id}) DETACH DELETE m",
            pbar=pbar,
            log_context=f"delete meta config_id={config_id}",
            config_id=config_id,
        )
        self._run_query(
            "MATCH (n:GraphNode {config_id: $config_id}) DETACH DELETE n",
            pbar=pbar,
            log_context=f"delete nodes config_id={config_id}",
            config_id=config_id,
        )

        nodes_by_kind: dict[str, list[dict]] = defaultdict(list)
        for node in nodes:
            if not isinstance(node, dict):
                continue
            row = self._sanitize_node_properties(node)
            row["config_id"] = config_id
            row["config_name"] = config_name
            row["config_profile"] = config_profile
            nodes_by_kind[str(node.get("kind") or "GraphNode")].append(row)

        for kind, rows in nodes_by_kind.items():
            label = self.KIND_TO_LABEL.get(kind, "GraphNode")
            total_batches = max(1, (len(rows) + self.batch_size - 1) // self.batch_size)
            for batch_index, start in enumerate(range(0, len(rows), self.batch_size), start=1):
                batch = rows[start:start + self.batch_size]
                self._log_batch_progress(f"nodes:{kind}", batch_index, total_batches, pbar=pbar)
                query = (
                    f"UNWIND $rows AS row "
                    f"MERGE (n:GraphNode:{label} {{id: row.id, config_id: row.config_id}}) "
                    f"SET n += row"
                )
                self._run_query(
                    query,
                    rows=batch,
                    pbar=pbar,
                    log_context=f"node batch {batch_index}/{total_batches} kind={kind}",
                )

        edges_by_type: dict[str, list[dict]] = defaultdict(list)
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            edge_type = str(edge.get("type") or "RELATED_TO").upper()
            safe_edge_type = "".join(ch for ch in edge_type if ch.isalnum() or ch == "_") or "RELATED_TO"
            props = dict(edge)
            props["config_id"] = config_id
            props["edge_key"] = (
                f"{props.get('from', '')}|{props.get('to', '')}|"
                f"{props.get('type', '')}|{props.get('section', '')}|"
                f"{props.get('container', '')}|{props.get('source', '')}|"
                f"{props.get('event', '')}"
            )
            edges_by_type[safe_edge_type].append(props)

        for edge_type, rows in edges_by_type.items():
            total_batches = max(1, (len(rows) + self.batch_size - 1) // self.batch_size)
            for batch_index, start in enumerate(range(0, len(rows), self.batch_size), start=1):
                batch = rows[start:start + self.batch_size]
                self._log_batch_progress(f"edges:{edge_type}", batch_index, total_batches, pbar=pbar)
                query = (
                    "UNWIND $rows AS row "
                    "MATCH (source:GraphNode {id: row.from, config_id: row.config_id}) "
                    "MATCH (target:GraphNode {id: row.to, config_id: row.config_id}) "
                    f"MERGE (source)-[r:{edge_type} {{edge_key: row.edge_key}}]->(target) "
                    "SET r += row"
                )
                self._run_query(
                    query,
                    rows=batch,
                    pbar=pbar,
                    log_context=f"edge batch {batch_index}/{total_batches} type={edge_type}",
                )

        self._run_query(
            "MERGE (m:GraphProjectionMeta {config_id: $config_id}) "
            "SET m.generated_at = $generated_at, "
            "    m.config_name = $config_name, "
            "    m.config_profile = $config_profile, "
            "    m.node_count = $node_count, "
            "    m.edge_count = $edge_count, "
            "    m.node_kinds = $node_kinds, "
            "    m.edge_types = $edge_types",
            pbar=pbar,
            log_context=f"write meta config_id={config_id}",
            config_id=config_id,
            generated_at=generated_at,
            config_name=config_name,
            config_profile=config_profile,
            node_count=stats.get("node_count", 0),
            edge_count=stats.get("edge_count", 0),
            node_kinds=stats.get("node_kinds", {}),
            edge_types=stats.get("edge_types", {}),
        )


def build_graph_writers(
    targets: list[str],
    graph_file: Path,
    memgraph_uri: str,
    memgraph_username: str,
    memgraph_password: str,
    memgraph_database: str = "",
    memgraph_batch_size: int = 1000,
    memgraph_retry_attempts: int = 3,
    memgraph_retry_backoff_seconds: float = 2.0,
) -> list[GraphWriter]:
    writers: list[GraphWriter] = []

    for target in targets:
        normalized_target = target.strip().lower()
        if not normalized_target:
            continue
        if normalized_target == "json":
            writers.append(JsonGraphWriter(graph_file))
        elif normalized_target == "memgraph":
            writers.append(
                MemgraphGraphWriter(
                    uri=memgraph_uri,
                    username=memgraph_username,
                    password=memgraph_password,
                    database=memgraph_database,
                    batch_size=memgraph_batch_size,
                    retry_attempts=memgraph_retry_attempts,
                    retry_backoff_seconds=memgraph_retry_backoff_seconds,
                )
            )
        else:
            raise ValueError(f"Неизвестный graph writer target: {target}")

    return writers

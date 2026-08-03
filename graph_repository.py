import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable


class GraphRepository(ABC):
    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def exists(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_node(self, node_id: str) -> dict | None:
        raise NotImplementedError

    @abstractmethod
    def iter_nodes(self, kind: str | None = None) -> Iterable[dict]:
        raise NotImplementedError

    @abstractmethod
    def find_nodes(
        self,
        kind: str | None = None,
        properties: dict | None = None,
        limit: int = 20,
    ) -> Iterable[dict]:
        raise NotImplementedError

    @abstractmethod
    def iter_edges(
        self,
        node_id: str,
        direction: str = "outgoing",
        edge_type: str | None = None,
    ) -> Iterable[tuple[dict, dict | None]]:
        raise NotImplementedError

    @abstractmethod
    def get_stats(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    def get_generated_at(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_source_label(self) -> str:
        raise NotImplementedError


class JsonGraphRepository(GraphRepository):
    def __init__(self, graph_file: Path):
        self.graph_file = Path(graph_file)
        self._graph_cache: dict | None = None

    def _load_graph(self) -> dict:
        if self._graph_cache is not None:
            return self._graph_cache

        if not self.graph_file.exists():
            self._graph_cache = {}
            return self._graph_cache

        try:
            with open(self.graph_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._graph_cache = data if isinstance(data, dict) else {}
        except Exception:
            self._graph_cache = {}

        return self._graph_cache

    def close(self) -> None:
        self._graph_cache = None

    def exists(self) -> bool:
        return self.graph_file.exists()

    def get_node(self, node_id: str) -> dict | None:
        graph_cache = self._load_graph()
        nodes = graph_cache.get("nodes") or []
        by_id = ((graph_cache.get("indexes") or {}).get("by_id")) or {}
        node_index = by_id.get(node_id)
        if node_index is None or not isinstance(node_index, int):
            return None
        if node_index < 0 or node_index >= len(nodes):
            return None
        node = nodes[node_index]
        return node if isinstance(node, dict) else None

    def iter_nodes(self, kind: str | None = None) -> Iterable[dict]:
        graph_cache = self._load_graph()
        for node in graph_cache.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            if kind and node.get("kind") != kind:
                continue
            yield node

    def find_nodes(
        self,
        kind: str | None = None,
        properties: dict | None = None,
        limit: int = 20,
    ) -> Iterable[dict]:
        properties = properties or {}
        found = 0
        for node in self.iter_nodes(kind):
            if any(node.get(key) != value for key, value in properties.items()):
                continue
            yield node
            found += 1
            if found >= limit:
                return

    def iter_edges(
        self,
        node_id: str,
        direction: str = "outgoing",
        edge_type: str | None = None,
    ) -> Iterable[tuple[dict, dict | None]]:
        graph_cache = self._load_graph()
        indexes = graph_cache.get("indexes") or {}
        edge_indexes = indexes.get(direction, {}).get(node_id, [])
        edges = graph_cache.get("edges") or []

        for edge_index in edge_indexes:
            if not isinstance(edge_index, int) or edge_index < 0 or edge_index >= len(edges):
                continue
            edge = edges[edge_index]
            if not isinstance(edge, dict):
                continue
            if edge_type and edge.get("type") != edge_type:
                continue

            target_id = edge.get("to") if direction == "outgoing" else edge.get("from")
            yield edge, self.get_node(target_id)

    def get_stats(self) -> dict:
        graph_cache = self._load_graph()
        stats = graph_cache.get("stats", {})
        return stats if isinstance(stats, dict) else {}

    def get_generated_at(self) -> str:
        graph_cache = self._load_graph()
        generated_at = graph_cache.get("generated_at", "unknown")
        return generated_at if isinstance(generated_at, str) else "unknown"

    def get_source_label(self) -> str:
        return f"json:{self.graph_file}"


class MemgraphGraphRepository(GraphRepository):
    def __init__(
        self,
        uri: str,
        username: str,
        password: str,
        config_id: str,
        database: str = "",
    ):
        self.uri = uri
        self.username = username
        self.password = password
        self.config_id = config_id
        self.database = database.strip()
        self._driver = None

    def _get_driver(self):
        if self._driver is None:
            from neo4j import GraphDatabase

            driver_kwargs = {}
            if self.username:
                driver_kwargs["auth"] = (self.username, self.password)
            self._driver = GraphDatabase.driver(self.uri, **driver_kwargs)
        return self._driver

    def _session_kwargs(self) -> dict:
        if self.database:
            return {"database": self.database}
        return {}

    def close(self) -> None:
        if self._driver is None:
            return
        try:
            self._driver.close()
        except Exception:
            pass
        finally:
            self._driver = None

    def exists(self) -> bool:
        try:
            driver = self._get_driver()
            with driver.session(**self._session_kwargs()) as session:
                record = session.run(
                    "MATCH (n:GraphNode {config_id: $config_id}) "
                    "RETURN count(n) > 0 AS exists",
                    config_id=self.config_id,
                ).single()
            return bool(record and record.get("exists"))
        except Exception:
            return False

    def get_node(self, node_id: str) -> dict | None:
        try:
            driver = self._get_driver()
            with driver.session(**self._session_kwargs()) as session:
                record = session.run(
                    "MATCH (n:GraphNode {id: $node_id, config_id: $config_id}) "
                    "RETURN properties(n) AS props "
                    "LIMIT 1",
                    node_id=node_id,
                    config_id=self.config_id,
                ).single()
            if not record:
                return None
            props = record.get("props")
            return props if isinstance(props, dict) else None
        except Exception:
            return None

    def iter_nodes(self, kind: str | None = None) -> Iterable[dict]:
        try:
            driver = self._get_driver()
            with driver.session(**self._session_kwargs()) as session:
                if kind:
                    query = (
                        "MATCH (n:GraphNode {config_id: $config_id}) "
                        "WHERE n.kind = $kind "
                        "RETURN properties(n) AS props"
                    )
                    records = list(session.run(query, config_id=self.config_id, kind=kind))
                else:
                    query = (
                        "MATCH (n:GraphNode {config_id: $config_id}) "
                        "RETURN properties(n) AS props"
                    )
                    records = list(session.run(query, config_id=self.config_id))
        except Exception:
            return iter(())

        items: list[dict] = []
        for record in records:
            props = record.get("props")
            if isinstance(props, dict):
                items.append(props)
        return iter(items)

    def find_nodes(
        self,
        kind: str | None = None,
        properties: dict | None = None,
        limit: int = 20,
    ) -> Iterable[dict]:
        properties = properties or {}
        safe_properties = {
            key: value
            for key, value in properties.items()
            if key.replace("_", "").isalnum()
        }
        conditions = ["n.config_id = $config_id"]
        params = {"config_id": self.config_id, "limit": max(1, limit)}

        if kind:
            conditions.append("n.kind = $kind")
            params["kind"] = kind

        for index, (key, value) in enumerate(safe_properties.items()):
            param_name = f"prop_{index}"
            conditions.append(f"n.`{key}` = ${param_name}")
            params[param_name] = value

        query = (
            "MATCH (n:GraphNode) "
            f"WHERE {' AND '.join(conditions)} "
            "RETURN properties(n) AS props "
            "LIMIT $limit"
        )

        try:
            driver = self._get_driver()
            with driver.session(**self._session_kwargs()) as session:
                records = list(session.run(query, **params))
        except Exception:
            return iter(())

        items: list[dict] = []
        for record in records:
            props = record.get("props")
            if isinstance(props, dict):
                items.append(props)
        return iter(items)

    def iter_edges(
        self,
        node_id: str,
        direction: str = "outgoing",
        edge_type: str | None = None,
    ) -> Iterable[tuple[dict, dict | None]]:
        if direction not in {"outgoing", "incoming"}:
            return iter(())

        try:
            driver = self._get_driver()
            with driver.session(**self._session_kwargs()) as session:
                if direction == "outgoing":
                    query = (
                        "MATCH (source:GraphNode {id: $node_id, config_id: $config_id})-[r]->(target:GraphNode {config_id: $config_id}) "
                        "RETURN properties(r) AS edge_props, properties(target) AS target_props"
                    )
                else:
                    query = (
                        "MATCH (source:GraphNode {config_id: $config_id})-[r]->(target:GraphNode {id: $node_id, config_id: $config_id}) "
                        "RETURN properties(r) AS edge_props, properties(source) AS target_props"
                    )

                records = list(session.run(query, node_id=node_id, config_id=self.config_id))
        except Exception:
            return iter(())

        items: list[tuple[dict, dict | None]] = []
        for record in records:
            edge_props = record.get("edge_props")
            if not isinstance(edge_props, dict):
                continue
            if edge_type and edge_props.get("type") != edge_type:
                continue
            target_props = record.get("target_props")
            items.append((edge_props, target_props if isinstance(target_props, dict) else None))

        return iter(items)

    def get_stats(self) -> dict:
        try:
            driver = self._get_driver()
            with driver.session(**self._session_kwargs()) as session:
                meta_record = session.run(
                    "MATCH (m:GraphProjectionMeta {config_id: $config_id}) "
                    "RETURN properties(m) AS meta "
                    "LIMIT 1",
                    config_id=self.config_id,
                ).single()
                if meta_record:
                    meta = meta_record.get("meta")
                    if isinstance(meta, dict):
                        return {
                            "node_count": meta.get("node_count", "unknown"),
                            "edge_count": meta.get("edge_count", "unknown"),
                            "node_kinds": meta.get("node_kinds", {}),
                            "edge_types": meta.get("edge_types", {}),
                        }

                counts = session.run(
                    "MATCH (n:GraphNode {config_id: $config_id}) "
                    "OPTIONAL MATCH (n)-[r]->(:GraphNode {config_id: $config_id}) "
                    "RETURN count(DISTINCT n) AS node_count, count(DISTINCT r) AS edge_count",
                    config_id=self.config_id,
                ).single()
            if not counts:
                return {}
            return {
                "node_count": counts.get("node_count", "unknown"),
                "edge_count": counts.get("edge_count", "unknown"),
            }
        except Exception:
            return {}

    def get_generated_at(self) -> str:
        try:
            driver = self._get_driver()
            with driver.session(**self._session_kwargs()) as session:
                record = session.run(
                    "MATCH (m:GraphProjectionMeta {config_id: $config_id}) "
                    "RETURN m.generated_at AS generated_at "
                    "LIMIT 1",
                    config_id=self.config_id,
                ).single()
            if not record:
                return "unknown"
            value = record.get("generated_at")
            return value if isinstance(value, str) else "unknown"
        except Exception:
            return "unknown"

    def get_source_label(self) -> str:
        return f"memgraph:{self.uri}/{self.config_id}"


class NullGraphRepository(GraphRepository):
    def close(self) -> None:
        return None

    def exists(self) -> bool:
        return False

    def get_node(self, node_id: str) -> dict | None:
        return None

    def iter_nodes(self, kind: str | None = None) -> Iterable[dict]:
        return iter(())

    def find_nodes(
        self,
        kind: str | None = None,
        properties: dict | None = None,
        limit: int = 20,
    ) -> Iterable[dict]:
        return iter(())

    def iter_edges(
        self,
        node_id: str,
        direction: str = "outgoing",
        edge_type: str | None = None,
    ) -> Iterable[tuple[dict, dict | None]]:
        return iter(())

    def get_stats(self) -> dict:
        return {}

    def get_generated_at(self) -> str:
        return "unknown"

    def get_source_label(self) -> str:
        return "none"


def build_graph_repository(
    backend: str,
    graph_file: Path,
    memgraph_uri: str,
    memgraph_username: str,
    memgraph_password: str,
    config_id: str,
    memgraph_database: str = "",
) -> GraphRepository:
    normalized_backend = backend.strip().lower()
    if normalized_backend == "json":
        return JsonGraphRepository(graph_file)
    if normalized_backend == "memgraph":
        return MemgraphGraphRepository(
            uri=memgraph_uri,
            username=memgraph_username,
            password=memgraph_password,
            config_id=config_id,
            database=memgraph_database,
        )
    return NullGraphRepository()

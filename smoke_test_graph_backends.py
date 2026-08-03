import argparse
import os
import tempfile
from pathlib import Path

from graph_repository import JsonGraphRepository, MemgraphGraphRepository
from graph_writers import build_graph_writers

VALID_TARGETS = {"json", "memgraph"}


def parse_targets(value: str) -> list[str]:
    targets = [item.strip().lower() for item in value.split(",") if item.strip()]
    unknown = sorted(set(targets) - VALID_TARGETS)
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown graph target(s): {', '.join(unknown)}. Use json, memgraph, or json,memgraph."
        )
    if not targets:
        raise argparse.ArgumentTypeError("at least one graph target is required")
    return targets


def build_smoke_projection() -> dict:
    projection = {
        "version": 1,
        "generated_at": "2026-07-02T12:00:00",
        "config": {
            "config_id": "smoke_config",
            "config_name": "SmokeConfig",
            "config_profile": "generic",
            "platform_version": "test",
            "export_path": "D:/Smoke/Export",
            "index_filter": "",
        },
        "stats": {
            "node_count": 9,
            "edge_count": 13,
            "node_kinds": {
                "metadata": 2,
                "form": 1,
                "form_element": 1,
                "command": 1,
                "handler": 1,
                "module": 1,
                "method": 2,
            },
            "edge_types": {
                "contains_form": 1,
                "contains_element": 1,
                "invokes_command": 1,
                "contains_command": 1,
                "handles_event": 1,
                "handled_by": 1,
                "implements_handler": 1,
                "contains_module": 1,
                "declares_method": 2,
                "references_metadata": 1,
                "calls": 1,
                "uses_metadata": 1,
            },
        },
        "nodes": [
            {
                "id": "metadata:Catalog.Products",
                "kind": "metadata",
                "object_type": "Catalog",
                "object_name": "Products",
                "synonym": "Номенклатура",
                "file_path": "D:/Smoke/Export/Catalogs/Products.xml",
                "document": "Большая карточка метаданных должна остаться вне Memgraph.",
            },
            {
                "id": "metadata:Catalog.Categories",
                "kind": "metadata",
                "object_type": "Catalog",
                "object_name": "Categories",
                "file_path": "D:/Smoke/Export/Catalogs/Categories.xml",
            },
            {
                "id": "form:Catalog.Products.MainForm",
                "kind": "form",
                "form_name": "MainForm",
                "owner_type": "Catalogs",
                "owner_object_type": "Catalog",
                "owner_name": "Products",
                "root_type": "Form",
                "file_path": "D:/Smoke/Export/Catalogs/Products/Forms/MainForm.xml",
            },
            {
                "id": "form_element:form:Catalog.Products.MainForm.1",
                "kind": "form_element",
                "element_id": "1",
                "element_name": "SaveButton",
                "element_type": "Button",
                "command_name": "Form.Command.Save",
                "command_ref": "Save",
                "visible": True,
                "enabled": True,
                "read_only": False,
                "depth": 0,
                "event_count": 1,
                "form_name": "MainForm",
            },
            {
                "id": "command:form:Catalog.Products.MainForm.Save",
                "kind": "command",
                "command_name": "Save",
                "action": "BeforeWrite",
                "form_name": "MainForm",
            },
            {
                "id": "handler:form:Catalog.Products.MainForm.BeforeWrite",
                "kind": "handler",
                "handler_name": "BeforeWrite",
                "handler_kind": "command",
                "form_name": "MainForm",
            },
            {
                "id": "module:Catalogs.Products.ObjectModule",
                "kind": "module",
                "module_type": "Catalogs",
                "module_name": "Products",
                "module_path": "Catalogs.Products.ObjectModule",
                "file_path": "D:/Smoke/Export/Catalogs/Products/ObjectModule.bsl",
            },
            {
                "id": "method:Catalogs.Products.ObjectModule.BeforeWrite:10",
                "kind": "method",
                "module_type": "Catalogs",
                "module_name": "Products",
                "module_path": "Catalogs.Products.ObjectModule",
                "method_name": "BeforeWrite",
                "start_line": 10,
                "end_line": 25,
                "file_path": "D:/Smoke/Export/Catalogs/Products/ObjectModule.bsl",
                "document": "Процедура BeforeWrite()\nКонецПроцедуры",
            },
            {
                "id": "method:Catalogs.Products.ObjectModule.ValidateCategory:30",
                "kind": "method",
                "module_type": "Catalogs",
                "module_name": "Products",
                "module_path": "Catalogs.Products.ObjectModule",
                "method_name": "ValidateCategory",
                "start_line": 30,
                "end_line": 45,
                "file_path": "D:/Smoke/Export/Catalogs/Products/ObjectModule.bsl",
            },
        ],
        "edges": [
            {
                "from": "metadata:Catalog.Products",
                "to": "form:Catalog.Products.MainForm",
                "type": "contains_form",
            },
            {
                "from": "form:Catalog.Products.MainForm",
                "to": "form_element:form:Catalog.Products.MainForm.1",
                "type": "contains_element",
            },
            {
                "from": "form_element:form:Catalog.Products.MainForm.1",
                "to": "command:form:Catalog.Products.MainForm.Save",
                "type": "invokes_command",
                "source": "Form.Command.Save",
            },
            {
                "from": "form:Catalog.Products.MainForm",
                "to": "command:form:Catalog.Products.MainForm.Save",
                "type": "contains_command",
            },
            {
                "from": "command:form:Catalog.Products.MainForm.Save",
                "to": "handler:form:Catalog.Products.MainForm.BeforeWrite",
                "type": "handled_by",
                "source": "Save",
            },
            {
                "from": "form_element:form:Catalog.Products.MainForm.1",
                "to": "handler:form:Catalog.Products.MainForm.BeforeWrite",
                "type": "handles_event",
                "source": "SaveButton",
                "event": "Click",
            },
            {
                "from": "handler:form:Catalog.Products.MainForm.BeforeWrite",
                "to": "method:Catalogs.Products.ObjectModule.BeforeWrite:10",
                "type": "implements_handler",
                "source": "Save",
                "event": "command",
            },
            {
                "from": "metadata:Catalog.Products",
                "to": "module:Catalogs.Products.ObjectModule",
                "type": "contains_module",
            },
            {
                "from": "module:Catalogs.Products.ObjectModule",
                "to": "method:Catalogs.Products.ObjectModule.ValidateCategory:30",
                "type": "declares_method",
            },
            {
                "from": "method:Catalogs.Products.ObjectModule.BeforeWrite:10",
                "to": "method:Catalogs.Products.ObjectModule.ValidateCategory:30",
                "type": "calls",
                "source": "ValidateCategory",
                "resolution": "local",
            },
            {
                "from": "method:Catalogs.Products.ObjectModule.BeforeWrite:10",
                "to": "metadata:Catalog.Categories",
                "type": "uses_metadata",
                "namespace": "Справочники",
                "source": "Справочники.Categories",
            },
            {
                "from": "module:Catalogs.Products.ObjectModule",
                "to": "method:Catalogs.Products.ObjectModule.BeforeWrite:10",
                "type": "declares_method",
            },
            {
                "from": "metadata:Catalog.Products",
                "to": "metadata:Catalog.Categories",
                "type": "references_metadata",
                "section": "attributes",
                "source": "Category",
            },
        ],
    }

    nodes = projection["nodes"]
    edges = projection["edges"]
    by_id = {node["id"]: index for index, node in enumerate(nodes)}
    outgoing: dict[str, list[int]] = {}
    incoming: dict[str, list[int]] = {}
    for edge_index, edge in enumerate(edges):
        outgoing.setdefault(edge["from"], []).append(edge_index)
        incoming.setdefault(edge["to"], []).append(edge_index)

    projection["indexes"] = {
        "by_id": by_id,
        "outgoing": outgoing,
        "incoming": incoming,
    }
    return projection


def validate_repository(repo, expected_source: str) -> None:
    assert repo.exists(), f"{expected_source}: repository does not exist"

    stats = repo.get_stats()
    assert stats.get("node_count") == 9, f"{expected_source}: unexpected node_count {stats}"
    assert stats.get("edge_count") == 13, f"{expected_source}: unexpected edge_count {stats}"
    assert repo.get_generated_at() == "2026-07-02T12:00:00", f"{expected_source}: wrong generated_at"

    node = repo.get_node("metadata:Catalog.Products")
    assert node is not None, f"{expected_source}: metadata node not found"
    assert node.get("object_name") == "Products", f"{expected_source}: wrong node payload"
    if expected_source == "json":
        assert "document" in node, f"{expected_source}: document payload should be preserved"
    if expected_source == "memgraph":
        assert "document" not in node, f"{expected_source}: document payload should not be written"

    form_edges = list(repo.iter_edges("metadata:Catalog.Products", "outgoing", "contains_form"))
    assert len(form_edges) == 1, f"{expected_source}: contains_form mismatch"
    assert form_edges[0][1] is not None, f"{expected_source}: contains_form target missing"
    assert form_edges[0][1].get("form_name") == "MainForm", f"{expected_source}: wrong form target"

    element_edges = list(repo.iter_edges("form:Catalog.Products.MainForm", "outgoing", "contains_element"))
    assert len(element_edges) == 1, f"{expected_source}: contains_element mismatch"
    assert element_edges[0][1] is not None, f"{expected_source}: form element target missing"
    assert element_edges[0][1].get("element_name") == "SaveButton", f"{expected_source}: wrong element target"

    command_edges = list(repo.iter_edges("form:Catalog.Products.MainForm", "outgoing", "contains_command"))
    assert len(command_edges) == 1, f"{expected_source}: contains_command mismatch"
    assert command_edges[0][1] is not None, f"{expected_source}: command target missing"
    assert command_edges[0][1].get("command_name") == "Save", f"{expected_source}: wrong command target"

    entrypoint_edges = list(repo.iter_edges(
        "method:Catalogs.Products.ObjectModule.BeforeWrite:10",
        "incoming",
        "implements_handler",
    ))
    assert len(entrypoint_edges) == 1, f"{expected_source}: implements_handler mismatch"
    assert entrypoint_edges[0][1] is not None, f"{expected_source}: handler target missing"
    assert entrypoint_edges[0][1].get("handler_name") == "BeforeWrite", f"{expected_source}: wrong handler target"

    ref_edges = list(repo.iter_edges("metadata:Catalog.Products", "outgoing", "references_metadata"))
    assert len(ref_edges) == 1, f"{expected_source}: references_metadata mismatch"
    assert ref_edges[0][0].get("source") == "Category", f"{expected_source}: wrong edge payload"
    assert ref_edges[0][1] is not None, f"{expected_source}: references target missing"
    assert ref_edges[0][1].get("object_name") == "Categories", f"{expected_source}: wrong reference target"

    call_edges = list(repo.iter_edges("method:Catalogs.Products.ObjectModule.BeforeWrite:10", "outgoing", "calls"))
    assert len(call_edges) == 1, f"{expected_source}: calls mismatch"
    assert call_edges[0][1] is not None, f"{expected_source}: calls target missing"
    assert call_edges[0][1].get("method_name") == "ValidateCategory", f"{expected_source}: wrong callee"

    usage_edges = list(repo.iter_edges("method:Catalogs.Products.ObjectModule.BeforeWrite:10", "outgoing", "uses_metadata"))
    assert len(usage_edges) == 1, f"{expected_source}: uses_metadata mismatch"
    assert usage_edges[0][1] is not None, f"{expected_source}: usage target missing"
    assert usage_edges[0][1].get("object_name") == "Categories", f"{expected_source}: wrong usage target"


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test graph writers and repositories.")
    parser.add_argument(
        "--targets",
        type=parse_targets,
        default=parse_targets(os.getenv("GRAPH_WRITE_TARGETS", "json")),
        help="Graph targets to test: json, memgraph, or json,memgraph. Defaults to GRAPH_WRITE_TARGETS or json.",
    )
    parser.add_argument("--memgraph-uri", default=os.getenv("MEMGRAPH_URI", "bolt://localhost:7687"))
    parser.add_argument("--memgraph-user", default=os.getenv("MEMGRAPH_USER", ""))
    parser.add_argument("--memgraph-password", default=os.getenv("MEMGRAPH_PASSWORD", ""))
    parser.add_argument("--memgraph-database", default=os.getenv("MEMGRAPH_DATABASE", ""))
    parser.add_argument(
        "--memgraph-batch-size",
        type=int,
        default=int(os.getenv("MEMGRAPH_BATCH_SIZE", "1000")),
    )
    args = parser.parse_args()

    targets = args.targets

    projection = build_smoke_projection()

    with tempfile.TemporaryDirectory(prefix="graph-smoke-") as tmpdir:
        graph_file = Path(tmpdir) / "graph_cache_smoke.json"
        writers = build_graph_writers(
            targets=targets,
            graph_file=graph_file,
            memgraph_uri=args.memgraph_uri,
            memgraph_username=args.memgraph_user,
            memgraph_password=args.memgraph_password,
            memgraph_database=args.memgraph_database,
            memgraph_batch_size=args.memgraph_batch_size,
        )

        if not writers:
            raise RuntimeError("Не настроены graph writers для smoke test.")

        for writer in writers:
            print(f"[smoke] write -> {writer.target_name}")
            writer.write(projection)

        if "json" in targets:
            print("[smoke] validate json repository")
            validate_repository(JsonGraphRepository(graph_file), "json")

        if "memgraph" in targets:
            print("[smoke] validate memgraph repository")
            validate_repository(
                MemgraphGraphRepository(
                    uri=args.memgraph_uri,
                    username=args.memgraph_user,
                    password=args.memgraph_password,
                    config_id="smoke_config",
                    database=args.memgraph_database,
                ),
                "memgraph",
            )

    print("[smoke] success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

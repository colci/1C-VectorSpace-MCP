import argparse
import json
import logging
import re
import warnings
from dataclasses import dataclass, field
from typing import Literal


ToolName = Literal["metadata", "code", "convergence", "diagnostics", "embedding_status", "bsl_ls_status", "module_smoke", "module_search", "command_search", "event_subscription_search", "workflow", "task_analysis", "extension_overlay", "bsl_validation", "changed_files_validation", "post_change", "form_structure"]


METADATA_OBJECT_RE = re.compile(r"^\*\*Объект:\*\*\s+([A-Za-z]+)\.([^\s(]+)")
CODE_MODULE_RE = re.compile(r"^\*\*Модуль:\*\*\s+`([^`]+)`")
CODE_METHOD_RE = re.compile(r"^\*\*Метод:\*\*\s+`([^`]+)`")


@dataclass
class RegressionCase:
    name: str
    tool: ToolName
    query: str
    limit: int = 5
    target: str = "auto"
    top1_prefixes: tuple[str, ...] = ()
    top3_types: tuple[str, ...] = ()
    top3_label_terms: tuple[str, ...] = ()
    top5_methods: tuple[str, ...] = ()
    top5_label_terms: tuple[str, ...] = ()
    shared_label_terms: tuple[str, ...] = ()
    must_contain: tuple[str, ...] = ()
    forbidden_top1_types: tuple[str, ...] = ()
    forbidden_top1_methods: tuple[str, ...] = ()


@dataclass
class CaseResult:
    case: RegressionCase
    ok: bool
    summary: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


CASES = [
    # Keep cases universal: assert result classes and business anchors, not object names
    # from the current indexed configuration.
    RegressionCase(
        name="metadata_register_customer_settlements",
        tool="metadata",
        query="расчеты с покупателями",
        limit=3,
        top3_types=("AccumulationRegister", "InformationRegister"),
        top3_label_terms=("расчет", "покупател", "прав"),
        forbidden_top1_types=("Subsystem", "Role", "Report"),
    ),
    RegressionCase(
        name="metadata_register_counterparty_settlements",
        tool="metadata",
        query="структура взаиморасчетов с контрагентом",
        limit=3,
        top3_types=("AccumulationRegister", "InformationRegister"),
        top3_label_terms=("взаиморасчет", "контрагент"),
        forbidden_top1_types=("Catalog", "Subsystem", "Role"),
    ),
    RegressionCase(
        name="metadata_counterparty_contract",
        tool="metadata",
        query="реквизиты договора контрагента",
        limit=3,
        top3_types=("Catalog",),
        top3_label_terms=("договор",),
    ),
    RegressionCase(
        name="metadata_goods_receipt_document",
        tool="metadata",
        query="структура электронного документа",
        limit=3,
        top3_types=("Document",),
        top3_label_terms=("электрон", "документ"),
        forbidden_top1_types=("Catalog", "Report", "Subsystem", "Role"),
    ),
    RegressionCase(
        name="code_validation_negative_stock",
        tool="code",
        query="где проверяется возможность проведения документа при отрицательных остатках",
        limit=5,
        top5_methods=("ОбработкаПроведения", "ВыполнитьКонтроль"),
        forbidden_top1_methods=("ПриИзменении", "ПриАктивизацииСтроки", "ПриНачалеРедактирования"),
    ),
    RegressionCase(
        name="noisy_metadata_counterparty_contract",
        tool="metadata",
        query="подскажи в каком объекте лежат реквизиты договора для работы с контрагентом",
        limit=3,
        top3_types=("Catalog",),
        top3_label_terms=("договор",),
    ),
    RegressionCase(
        name="noisy_metadata_goods_receipt_document",
        tool="metadata",
        query="мне нужно понять где в конфигурации описана структура электронного документа",
        limit=3,
        top3_types=("Document",),
        top3_label_terms=("электрон", "документ"),
        forbidden_top1_types=("Catalog", "Report", "Subsystem", "Role"),
    ),
    RegressionCase(
        name="noisy_code_validation_not_enough_goods",
        tool="code",
        query="мне нужно понять где в конфигурации проверяют что документ нельзя провести если не хватает товара",
        limit=5,
        top5_methods=("ОбработкаПроведения", "ВыполнитьКонтроль"),
        forbidden_top1_methods=("ПриИзменении", "ПриАктивизацииСтроки", "ПриНачалеРедактирования"),
    ),
    RegressionCase(
        name="convergence_customer_order",
        tool="convergence",
        query="как работает заказ клиента",
        limit=5,
        top3_types=("Document",),
        top3_label_terms=("заказ", "клиент", "покупател"),
        top5_label_terms=("заказ", "клиент", "покупател"),
        shared_label_terms=("заказ", "клиент", "покупател"),
    ),
    RegressionCase(
        name="convergence_counterparty_settlements",
        tool="convergence",
        query="как ведутся расчеты с контрагентом",
        limit=5,
        top3_types=("AccumulationRegister", "InformationRegister", "Document"),
        top3_label_terms=("расчет", "взаиморасчет", "контрагент"),
        top5_label_terms=("расчет", "взаиморасчет", "контрагент"),
        shared_label_terms=("расчет", "взаиморасчет", "контрагент"),
    ),
    RegressionCase(
        name="diagnostics_customer_order",
        tool="diagnostics",
        query="как работает заказ клиента",
        limit=2,
        target="auto",
        must_contain=(
            "## Explain Search Result",
            "## Metadata Diagnostics",
            "## Code Diagnostics",
            "Best source",
            "Score components",
        ),
    ),
    RegressionCase(
        name="embedding_status_smoke",
        tool="embedding_status",
        query="provider_readiness",
        must_contain=(
            "## Embedding Status",
            "- Provider: `local`",
            "- Configuration ready: `True`",
            "## Issues",
            "## Warnings",
        ),
    ),
    RegressionCase(
        name="bsl_ls_status_smoke",
        tool="bsl_ls_status",
        query="provider_readiness",
        must_contain=(
            "## BSL Language Server Status",
            "- Available:",
            "- Resolved binary:",
            "- Timeout seconds:",
            "- Fallback: `built-in structural bootstrap`",
        ),
    ),
    RegressionCase(
        name="module_navigation_smoke",
        tool="module_smoke",
        query="common_module_roundtrip",
        must_contain=(
            "###",
            "## Module Methods",
            "## Methods",
        ),
    ),
    RegressionCase(
        name="module_search_smoke",
        tool="module_search",
        query="first_indexed_module",
        limit=3,
        must_contain=(
            "## Module Search",
            "### Module 1",
            "- Method preview:",
        ),
    ),
    RegressionCase(
        name="command_search_smoke",
        tool="command_search",
        query="first_indexed_command",
        limit=3,
        must_contain=(
            "## Command Search",
            "### Command 1",
            "- Action:",
            "- Form:",
        ),
    ),
    RegressionCase(
        name="event_subscription_search_smoke",
        tool="event_subscription_search",
        query="first_event_subscription",
        limit=3,
        must_contain=(
            "## Event Subscription Search",
            "### Subscription 1",
            "- Event:",
            "- Sources:",
            "- Handler:",
        ),
    ),
    RegressionCase(
        name="workflow_implementation_context_smoke",
        tool="workflow",
        query="document posting validation",
        limit=2,
        must_contain=(
            "## Implementation Context",
            "## Metadata Candidates",
            "## Code Candidates",
            "## Next MCP Calls",
        ),
    ),
    RegressionCase(
        name="task_analysis_smoke",
        tool="task_analysis",
        query="добавить проверку заполнения документа перед записью",
        limit=2,
        must_contain=(
            "## Task Analysis",
            "## Configuration Scope",
            "## Evidence: Metadata Candidates",
            "## Evidence: Code Candidates",
            "## Recommended Change Points",
            "## Risks And Unknowns",
            "## Implementation Plan",
            "## Validation Checklist",
        ),
    ),
    RegressionCase(
        name="extension_overlay_smoke",
        tool="extension_overlay",
        query="do_cfe",
        limit=3,
        must_contain=(
            "## Extension Overlay Report",
            "- Base config: `do_cf`",
            "- Extension: `do_cfe`",
            "## Metadata Objects",
            "## Methods",
            "- Extension annotations:",
            "- Resolved annotation targets:",
            "## Current Limitations",
        ),
    ),
    RegressionCase(
        name="bsl_validation_smoke",
        tool="bsl_validation",
        query="first_indexed_bsl",
        must_contain=(
            "## BSL Validation",
            "- Status:",
            "- Methods:",
            "- Errors:",
            "## Diagnostics",
        ),
    ),
    RegressionCase(
        name="changed_files_validation_smoke",
        tool="changed_files_validation",
        query="first_indexed_bsl",
        must_contain=(
            "## Changed Files Validation",
            "- Source: `explicit_path`",
            "## Summary",
            "- Files validated: `1`",
            "## Files",
        ),
    ),
    RegressionCase(
        name="post_change_report_smoke",
        tool="post_change",
        query="first_indexed_bsl",
        must_contain=(
            "## Post-Change Report",
            "## Overall Status",
            "## Validation",
            "## Graph Impact",
            "## Freshness",
            "## Required Actions",
        ),
    ),
    RegressionCase(
        name="form_structure_report_smoke",
        tool="form_structure",
        query="first_indexed_form",
        limit=10,
        must_contain=(
            "## Form Structure Report",
            "## Commands",
            "## Top-Level Elements",
            "## Resolved Handler Methods",
        ),
    ),
]


def quiet_runtime_logs() -> None:
    logging.getLogger().setLevel(logging.WARNING)
    for logger_name in (
        "httpx",
        "httpcore",
        "qdrant_client",
        "fastembed",
        "fastmcp",
    ):
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    warnings.filterwarnings(
        "ignore",
        message=".*now uses mean pooling instead of CLS embedding.*",
        category=UserWarning,
    )


def parse_metadata_results(text: str) -> list[dict[str, str]]:
    results = []
    for line in text.splitlines():
        match = METADATA_OBJECT_RE.match(line)
        if not match:
            continue
        object_type, object_name = match.groups()
        results.append({
            "type": object_type,
            "name": object_name,
            "label": f"{object_type}.{object_name}",
        })
    return results


def parse_code_results(text: str) -> list[dict[str, str]]:
    results = []
    current_module = ""
    for line in text.splitlines():
        module_match = CODE_MODULE_RE.match(line)
        if module_match:
            current_module = module_match.group(1)
            continue

        method_match = CODE_METHOD_RE.match(line)
        if not method_match:
            continue

        method_name = method_match.group(1)
        results.append({
            "module": current_module,
            "method": method_name,
            "label": f"{current_module}.{method_name}" if current_module else method_name,
        })
    return results


def starts_with_any(value: str, prefixes: tuple[str, ...]) -> bool:
    return any(value.startswith(prefix) for prefix in prefixes)


def label_has_any_term(value: str, terms: tuple[str, ...]) -> bool:
    normalized = value.lower().replace("ё", "е")
    return any(term.lower().replace("ё", "е") in normalized for term in terms)


def evaluate_metadata(case: RegressionCase, text: str) -> CaseResult:
    parsed = parse_metadata_results(text)
    result = CaseResult(case=case, ok=True)
    result.summary = [item["label"] for item in parsed[:case.limit]]

    if not parsed:
        result.ok = False
        result.failures.append("metadata results are empty")
        return result

    top1 = parsed[0]
    if case.top1_prefixes and not starts_with_any(top1["label"], case.top1_prefixes):
        result.ok = False
        result.failures.append(
            f"top-1 `{top1['label']}` does not match expected prefixes {case.top1_prefixes}"
        )

    if case.top3_types:
        top3_types = {item["type"] for item in parsed[:3]}
        if not top3_types.intersection(case.top3_types):
            result.ok = False
            result.failures.append(f"top-3 types {sorted(top3_types)} do not include {case.top3_types}")

    if case.top3_label_terms:
        top3_labels = [item["label"] for item in parsed[:3]]
        if not any(label_has_any_term(label, case.top3_label_terms) for label in top3_labels):
            result.ok = False
            result.failures.append(f"top-3 labels {top3_labels} do not include terms {case.top3_label_terms}")

    if top1["type"] in case.forbidden_top1_types:
        result.ok = False
        result.failures.append(f"top-1 type `{top1['type']}` is forbidden")

    return result


def evaluate_code(case: RegressionCase, text: str) -> CaseResult:
    parsed = parse_code_results(text)
    result = CaseResult(case=case, ok=True)
    result.summary = [item["label"] for item in parsed[:case.limit]]

    if not parsed:
        result.ok = False
        result.failures.append("code results are empty")
        return result

    top1_method = parsed[0]["method"]
    if starts_with_any(top1_method, case.forbidden_top1_methods):
        result.ok = False
        result.failures.append(f"top-1 method `{top1_method}` is forbidden")

    if case.top5_methods:
        top5_methods = [item["method"] for item in parsed[:5]]
        if not any(starts_with_any(method, case.top5_methods) for method in top5_methods):
            result.ok = False
            result.failures.append(f"top-5 methods {top5_methods} do not include {case.top5_methods}")

    if case.top5_label_terms:
        top5_labels = [item["label"] for item in parsed[:5]]
        if not any(label_has_any_term(label, case.top5_label_terms) for label in top5_labels):
            result.ok = False
            result.failures.append(
                f"top-5 labels {top5_labels} do not include terms {case.top5_label_terms}"
            )

    return result


def evaluate_convergence(case: RegressionCase, metadata_text: str, code_text: str) -> CaseResult:
    metadata = parse_metadata_results(metadata_text)
    code = parse_code_results(code_text)
    result = CaseResult(case=case, ok=True)
    result.summary = [
        f"metadata: {item['label']}" for item in metadata[:3]
    ] + [
        f"code: {item['label']}" for item in code[:5]
    ]

    if not metadata:
        result.ok = False
        result.failures.append("metadata results are empty")
    if not code:
        result.ok = False
        result.failures.append("code results are empty")
    if not result.ok:
        return result

    top3_types = {item["type"] for item in metadata[:3]}
    if case.top3_types and not top3_types.intersection(case.top3_types):
        result.ok = False
        result.failures.append(f"metadata top-3 types {sorted(top3_types)} do not include {case.top3_types}")

    top3_metadata_labels = [item["label"] for item in metadata[:3]]
    if case.top3_label_terms and not any(
        label_has_any_term(label, case.top3_label_terms) for label in top3_metadata_labels
    ):
        result.ok = False
        result.failures.append(
            f"metadata top-3 labels {top3_metadata_labels} do not include terms {case.top3_label_terms}"
        )

    top5_code_labels = [item["label"] for item in code[:5]]
    if case.top5_label_terms and not any(
        label_has_any_term(label, case.top5_label_terms) for label in top5_code_labels
    ):
        result.ok = False
        result.failures.append(
            f"code top-5 labels {top5_code_labels} do not include terms {case.top5_label_terms}"
        )

    if case.shared_label_terms:
        metadata_has_shared = any(
            label_has_any_term(label, case.shared_label_terms) for label in top3_metadata_labels
        )
        code_has_shared = any(
            label_has_any_term(label, case.shared_label_terms) for label in top5_code_labels
        )
        if not metadata_has_shared or not code_has_shared:
            result.ok = False
            result.failures.append(
                "metadata/code results do not converge on shared business terms "
                f"{case.shared_label_terms}"
            )

    return result


def evaluate_diagnostics(case: RegressionCase, text: str) -> CaseResult:
    result = CaseResult(case=case, ok=True)
    summary = []
    for line in text.splitlines():
        if line.startswith("## ") or line.startswith("- Query:") or line.startswith("- Target:"):
            summary.append(line)
        if len(summary) >= 8:
            break
    result.summary = summary

    if not text.strip():
        result.ok = False
        result.failures.append("diagnostics output is empty")
        return result

    for marker in case.must_contain:
        if marker not in text:
            result.ok = False
            result.failures.append(f"diagnostics output does not contain `{marker}`")

    return result


def evaluate_module_smoke(case: RegressionCase, find_text: str, methods_text: str, module_path: str) -> CaseResult:
    result = CaseResult(case=case, ok=True)
    result.summary = [
        f"module: {module_path}",
    ]

    if not find_text.strip():
        result.ok = False
        result.failures.append("find_module output is empty")
    if not methods_text.strip():
        result.ok = False
        result.failures.append("list_module_methods output is empty")
    if not result.ok:
        return result

    if module_path not in find_text:
        result.ok = False
        result.failures.append(f"find_module output does not contain `{module_path}`")
    if module_path not in methods_text:
        result.ok = False
        result.failures.append(f"list_module_methods output does not contain `{module_path}`")

    combined = f"{find_text}\n{methods_text}"
    for marker in case.must_contain:
        if marker not in combined:
            result.ok = False
            result.failures.append(f"module smoke output does not contain `{marker}`")

    return result


def evaluate_text_markers(case: RegressionCase, text: str) -> CaseResult:
    result = CaseResult(case=case, ok=True)
    result.summary = [
        line
        for line in text.splitlines()
        if line.startswith("## ") or line.startswith("- Config:") or line.startswith("- Task:")
    ][:8]

    if not text.strip():
        result.ok = False
        result.failures.append("workflow output is empty")
        return result

    for marker in case.must_contain:
        if marker not in text:
            result.ok = False
            result.failures.append(f"workflow output does not contain `{marker}`")

    return result


def run_case(case: RegressionCase) -> CaseResult:
    quiet_runtime_logs()
    import mcp_server

    quiet_runtime_logs()
    if case.tool == "metadata":
        text = mcp_server.search_metadata(case.query, limit=case.limit)
        return evaluate_metadata(case, text)
    if case.tool == "convergence":
        metadata_text = mcp_server.search_metadata(case.query, limit=max(3, case.limit))
        code_text = mcp_server.search_code(case.query, limit=max(5, case.limit))
        return evaluate_convergence(case, metadata_text, code_text)
    if case.tool == "diagnostics":
        text = mcp_server.explain_search_result(case.query, target=case.target, limit=case.limit)
        return evaluate_diagnostics(case, text)
    if case.tool == "embedding_status":
        text = mcp_server.embedding_status()
        return evaluate_text_markers(case, text)
    if case.tool == "bsl_ls_status":
        text = mcp_server.bsl_ls_status()
        return evaluate_text_markers(case, text)
    if case.tool == "module_smoke":
        common_modules = [
            payload
            for payload in mcp_server.load_module_payloads()
            if payload.get("module_type") == "CommonModules"
        ]
        if not common_modules:
            return CaseResult(
                case=case,
                ok=False,
                failures=["no common modules found in current index"],
            )

        common_modules.sort(key=lambda item: item.get("module_path", ""))
        selected = common_modules[0]
        module_name = selected.get("module_name") or selected.get("module_path", "")
        module_path = selected.get("module_path", "")
        find_text = mcp_server.find_common_module(module_name, limit=3)
        methods_text = mcp_server.list_module_methods(module_path, limit=5)
        return evaluate_module_smoke(case, find_text, methods_text, module_path)
    if case.tool == "module_search":
        modules = list(mcp_server.load_module_payloads())
        if not modules:
            return CaseResult(
                case=case,
                ok=False,
                failures=["no modules found in current index or graph"],
            )
        modules.sort(key=lambda item: item.get("module_path", ""))
        module_path = modules[0].get("module_path", "")
        text = mcp_server.search_modules(module_path, limit=case.limit)
        return evaluate_text_markers(case, text)
    if case.tool == "command_search":
        commands = list(mcp_server.graph_repository.iter_nodes("command"))
        if not commands:
            return CaseResult(
                case=case,
                ok=False,
                failures=["no commands found in current graph"],
            )
        commands.sort(key=lambda item: (item.get("form_name", ""), item.get("command_name", "")))
        command_name = commands[0].get("command_name", "")
        text = mcp_server.search_commands(command_name, limit=case.limit)
        return evaluate_text_markers(case, text)
    if case.tool == "event_subscription_search":
        subscriptions = list(mcp_server.load_event_subscription_payloads(mcp_server.EXPORT_PATH))
        if not subscriptions:
            return CaseResult(
                case=case,
                ok=False,
                failures=["no event subscriptions found in current export"],
            )
        subscriptions.sort(key=lambda item: item.get("subscription_name", ""))
        subscription_name = subscriptions[0].get("subscription_name", "")
        text = mcp_server.search_event_subscriptions(subscription_name, limit=case.limit)
        return evaluate_text_markers(case, text)
    if case.tool == "workflow":
        text = mcp_server.build_implementation_context(
            case.query,
            limit=case.limit,
            include_snippets=False,
        )
        return evaluate_text_markers(case, text)
    if case.tool == "task_analysis":
        text = mcp_server.analyze_task(case.query, limit=case.limit)
        return evaluate_text_markers(case, text)
    if case.tool == "extension_overlay":
        text = mcp_server.extension_overlay_report(case.query, limit=case.limit)
        return evaluate_text_markers(case, text)
    if case.tool == "bsl_validation":
        modules = [
            module
            for module in mcp_server.load_module_payloads()
            if str(module.get("file_path", "")).lower().endswith(".bsl")
        ]
        if not modules:
            return CaseResult(
                case=case,
                ok=False,
                failures=["no BSL modules found in current graph or index"],
            )
        modules.sort(key=lambda item: item.get("file_path", ""))
        text = mcp_server.validate_bsl(modules[0].get("file_path", ""))
        return evaluate_text_markers(case, text)
    if case.tool == "changed_files_validation":
        modules = [
            module
            for module in mcp_server.load_module_payloads()
            if str(module.get("file_path", "")).lower().endswith(".bsl")
        ]
        if not modules:
            return CaseResult(
                case=case,
                ok=False,
                failures=["no BSL modules found in current graph or index"],
            )
        modules.sort(key=lambda item: item.get("file_path", ""))
        text = mcp_server.validate_changed_files(modules[0].get("file_path", ""), max_files=1)
        return evaluate_text_markers(case, text)
    if case.tool == "post_change":
        modules = [
            module
            for module in mcp_server.load_module_payloads()
            if str(module.get("file_path", "")).lower().endswith(".bsl")
        ]
        if not modules:
            return CaseResult(
                case=case,
                ok=False,
                failures=["no BSL modules found in current graph or index"],
            )
        modules.sort(key=lambda item: item.get("file_path", ""))
        text = mcp_server.post_change_report(
            modules[0].get("file_path", ""),
            max_files=1,
            method_limit=5,
        )
        return evaluate_text_markers(case, text)
    if case.tool == "form_structure":
        forms = list(mcp_server.load_form_payloads())
        if not forms:
            return CaseResult(
                case=case,
                ok=True,
                summary=["skipped: no forms found in current index"],
            )

        forms.sort(
            key=lambda item: (
                -int(item.get("form_element_count") or 0),
                item.get("owner_name", ""),
                item.get("form_name", ""),
            )
        )
        selected = forms[0]
        text = mcp_server.form_structure_report(
            selected.get("form_name", ""),
            owner_name=selected.get("owner_name", ""),
            owner_type=selected.get("owner_object_type") or selected.get("owner_type", ""),
            limit=case.limit,
        )
        return evaluate_text_markers(case, text)

    text = mcp_server.search_code(case.query, limit=case.limit)
    return evaluate_code(case, text)


def main() -> int:
    quiet_runtime_logs()
    parser = argparse.ArgumentParser(description="Run live MCP search regression checks.")
    parser.add_argument(
        "--case",
        action="append",
        dest="cases",
        help="Run only selected case name. Can be passed multiple times.",
    )
    parser.add_argument(
        "--tool",
        action="append",
        dest="tools",
        choices=sorted({case.tool for case in CASES}),
        help="Run or list only cases for the selected tool category. Can be passed multiple times.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available regression cases without importing the MCP runtime.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print selected case counts by tool without importing the MCP runtime.",
    )
    parser.add_argument(
        "--list-format",
        choices=("text", "json"),
        default="text",
        help="Output format for --list.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first failed case.",
    )
    args = parser.parse_args()

    selected = CASES
    if args.tools:
        requested_tools = set(args.tools)
        selected = [case for case in selected if case.tool in requested_tools]
    if args.cases:
        requested = set(args.cases)
        selected = [case for case in selected if case.name in requested]
        unknown = requested - {case.name for case in CASES}
        if unknown:
            print(f"[regression] unknown cases: {', '.join(sorted(unknown))}. Use --list to inspect cases.")
            return 2

    if args.summary:
        counts: dict[str, int] = {}
        for case in selected:
            counts[case.tool] = counts.get(case.tool, 0) + 1
        print(f"[regression] selected cases: {len(selected)}")
        for tool_name in sorted(counts):
            print(f"- {tool_name}: {counts[tool_name]}")
        return 0

    if args.list:
        if args.list_format == "json":
            print(json.dumps([
                {
                    "name": case.name,
                    "tool": case.tool,
                    "query": case.query,
                    "limit": case.limit,
                }
                for case in selected
            ], ensure_ascii=False, indent=2))
            return 0

        for case in selected:
            print(f"{case.name}\t{case.tool}\t{case.query}")
        print(f"[regression] listed: {len(selected)}")
        return 0

    if not selected:
        print("[regression] no cases selected. Use --list to inspect cases.")
        return 2

    failures = 0
    for case in selected:
        result = run_case(case)
        status = "PASS" if result.ok else "FAIL"
        print(f"[{status}] {case.name}")
        print(f"  tool: {case.tool}")
        print(f"  query: {case.query}")
        for index, item in enumerate(result.summary, start=1):
            print(f"  {index}. {item}")
        for failure in result.failures:
            print(f"  failure: {failure}")
        if not result.ok:
            failures += 1
            if args.fail_fast:
                print("[regression] stopped after first failure because --fail-fast is enabled")
                break

    if failures:
        print(f"[regression] failed: {failures}/{len(selected)}")
        return 1

    print(f"[regression] success: {len(selected)}/{len(selected)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

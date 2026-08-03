import re


BSL_METHOD_DECL_RE = re.compile(
    r"^\s*(?P<kind>Процедура|Procedure|Функция|Function)\s+"
    r"(?P<name>[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*)\s*\(",
    re.IGNORECASE,
)
BSL_METHOD_END_RE = re.compile(
    r"^\s*(?P<kind>КонецПроцедуры|EndProcedure|КонецФункции|EndFunction)\b",
    re.IGNORECASE,
)
BSL_EXTENSION_ANNOTATION_RE = re.compile(
    r'^\s*&(Перед|После|Вместо|ИзменениеИКонтроль|Before|After|Instead|ChangeAndValidate)'
    r'\s*\(\s*["\']([^"\']+)["\']\s*\)',
    re.IGNORECASE,
)
BSL_CONTEXT_DIRECTIVE_RE = re.compile(
    r"^\s*&(НаКлиенте|НаСервере|НаСервереБезКонтекста|НаКлиентеНаСервере|"
    r"НаКлиентеНаСервереБезКонтекста|AtClient|AtServer|AtServerNoContext|"
    r"AtClientAtServer|AtClientAtServerNoContext)\b",
    re.IGNORECASE,
)
BSL_RETURN_RE = re.compile(r"^\s*(Возврат|Return)\b(?P<tail>.*)$", re.IGNORECASE)


def normalize_bsl_method_kind(value: str) -> str:
    normalized = value.lower()
    return "function" if "функц" in normalized or "function" in normalized else "procedure"


def strip_bsl_inline_comment(line: str) -> str:
    in_string = False
    index = 0
    while index < len(line):
        char = line[index]
        if char == '"':
            if in_string and index + 1 < len(line) and line[index + 1] == '"':
                index += 2
                continue
            in_string = not in_string
            index += 1
            continue
        if not in_string and line[index:index + 2] == "//":
            return line[:index]
        index += 1
    return line


def return_has_value(line: str) -> bool:
    code = strip_bsl_inline_comment(line)
    match = BSL_RETURN_RE.match(code)
    if not match:
        return False
    tail = match.group("tail").strip()
    return bool(tail.strip(";").strip())


def analyze_bsl_structure(source: str) -> dict:
    issues: list[dict] = []
    method_stack: list[dict] = []
    method_names: dict[str, int] = {}
    region_stack: list[int] = []
    preprocessor_stack: list[int] = []
    pending_directives: list[tuple[int, str]] = []

    def add_issue(severity: str, line: int, message: str) -> None:
        issues.append({"severity": severity, "line": line, "message": message})

    for line_number, raw_line in enumerate(source.splitlines(), start=1):
        stripped = raw_line.strip()
        lowered = stripped.lower()
        if not stripped or stripped.startswith("//"):
            continue

        declaration = BSL_METHOD_DECL_RE.match(raw_line)
        method_end = BSL_METHOD_END_RE.match(raw_line)
        extension_annotation = BSL_EXTENSION_ANNOTATION_RE.match(raw_line)
        context_directive = BSL_CONTEXT_DIRECTIVE_RE.match(raw_line)
        return_statement = BSL_RETURN_RE.match(strip_bsl_inline_comment(raw_line))

        if stripped.startswith("&"):
            if extension_annotation or context_directive:
                pending_directives.append((line_number, stripped))
            else:
                add_issue("warning", line_number, f"Неизвестная или неподдержанная директива `{stripped}`.")
            continue

        if declaration:
            name = declaration.group("name")
            kind = normalize_bsl_method_kind(declaration.group("kind"))
            if method_stack:
                add_issue(
                    "error",
                    line_number,
                    f"Метод `{name}` начинается до завершения `{method_stack[-1]['name']}`.",
                )
            method_stack.append({
                "name": name,
                "kind": kind,
                "line": line_number,
                "has_return": False,
            })
            normalized_name = name.lower()
            if normalized_name in method_names:
                add_issue(
                    "error",
                    line_number,
                    f"Повторное объявление метода `{name}`; первое объявление на строке {method_names[normalized_name]}.",
                )
            else:
                method_names[normalized_name] = line_number
            pending_directives = []
            continue

        if pending_directives:
            for directive_line, directive in pending_directives:
                add_issue(
                    "warning",
                    directive_line,
                    f"Директива `{directive}` не привязана к следующему объявлению метода.",
                )
            pending_directives = []

        if return_statement and method_stack:
            current_method = method_stack[-1]
            current_method["has_return"] = True
            if current_method["kind"] == "procedure" and return_has_value(raw_line):
                add_issue(
                    "error",
                    line_number,
                    f"Процедура `{current_method['name']}` содержит `Возврат` со значением.",
                )

        if method_end:
            end_kind = normalize_bsl_method_kind(method_end.group("kind"))
            if not method_stack:
                add_issue("error", line_number, f"Лишний `{method_end.group('kind')}` без начала метода.")
                continue
            method = method_stack.pop()
            if method["kind"] != end_kind:
                add_issue(
                    "error",
                    line_number,
                    f"Метод `{method['name']}` открыт как {method['kind']}, но закрыт как {end_kind}.",
                )
            if method["kind"] == "function" and not method["has_return"]:
                add_issue(
                    "warning",
                    method["line"],
                    f"Функция `{method['name']}` не содержит явного `Возврат`.",
                )
            continue

        if lowered.startswith("#область") or lowered.startswith("#region"):
            region_stack.append(line_number)
        elif lowered.startswith("#конецобласти") or lowered.startswith("#endregion"):
            if region_stack:
                region_stack.pop()
            else:
                add_issue("error", line_number, "Лишний конец области без `#Область`.")

        if re.match(r"^#(если|if)\b", lowered):
            preprocessor_stack.append(line_number)
        elif re.match(r"^#(конецесли|endif)\b", lowered):
            if preprocessor_stack:
                preprocessor_stack.pop()
            else:
                add_issue("error", line_number, "Лишний конец препроцессорного условия.")

    for method in method_stack:
        add_issue(
            "error",
            method["line"],
            f"Метод `{method['name']}` не закрыт до конца файла.",
        )
    for line_number in region_stack:
        add_issue("error", line_number, "Область не закрыта `#КонецОбласти`.")
    for line_number in preprocessor_stack:
        add_issue("error", line_number, "Препроцессорное условие не закрыто `#КонецЕсли`.")
    for directive_line, directive in pending_directives:
        add_issue("warning", directive_line, f"Директива `{directive}` находится в конце файла без метода.")

    errors = [issue for issue in issues if issue["severity"] == "error"]
    warnings = [issue for issue in issues if issue["severity"] == "warning"]
    return {
        "method_count": len(method_names),
        "issues": issues,
        "error_count": len(errors),
        "warning_count": len(warnings),
    }

import json
import tempfile
from pathlib import Path

from bsl_language_server import (
    analyze_bsl_files,
    collect_bsl_ls_status,
    normalize_severity,
    parse_bsl_ls_report,
)


def main() -> int:
    assert normalize_severity(1) == "error"
    assert normalize_severity(2) == "warning"
    assert normalize_severity("WARNING") == "warning"
    assert normalize_severity("Information") == "information"
    assert normalize_severity("Hint") == "hint"
    assert normalize_severity(None) == "unknown"

    with tempfile.TemporaryDirectory(prefix="bsl-ls-smoke-") as tmpdir:
        tmp_path = Path(tmpdir)
        source_file = tmp_path / "Original.bsl"
        copied_file = tmp_path / "000000.bsl"
        report_file = tmp_path / "bsl-json.json"
        source_file.write_text("Процедура Тест()\nКонецПроцедуры\n", encoding="utf-8")
        copied_file.write_text(source_file.read_text(encoding="utf-8"), encoding="utf-8")

        report = {
            "fileinfos": [
                {
                    "path": str(copied_file),
                    "diagnostics": [
                        {
                            "severity": 1,
                            "range": {"start": {"line": 1, "character": 2}},
                            "code": "SmokeError",
                            "message": "Ошибка smoke",
                        },
                        {
                            "severity": "Warning",
                            "range": {"start": {"line": 0, "character": 0}},
                            "code": "SmokeWarning",
                            "message": "Предупреждение smoke",
                        },
                    ],
                }
            ]
        }
        report_file.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

        parsed = parse_bsl_ls_report(
            report_file,
            {
                str(copied_file.resolve()).lower(): source_file,
                copied_file.name.lower(): source_file,
            },
        )
        assert parsed["error_count"] == 1
        assert parsed["warning_count"] == 1
        assert parsed["information_count"] == 0
        assert parsed["hint_count"] == 0
        assert parsed["unknown_count"] == 0
        assert len(parsed["diagnostics"]) == 2
        assert parsed["diagnostics"][0]["file"] == str(source_file)
        assert parsed["diagnostics"][0]["line"] == 2
        assert parsed["diagnostics"][0]["column"] == 3
        assert parsed["diagnostics"][0]["severity"] == "error"

        empty_result = analyze_bsl_files([], tmp_path)
        assert empty_result["status"] in {"not_run", "unavailable"}
        assert empty_result["diagnostics"] == []

    status = collect_bsl_ls_status(probe_runtime=False)
    assert "available" in status
    assert "timeout_seconds" in status

    print("[bsl-language-server-smoke] success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

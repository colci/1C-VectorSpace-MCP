import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


SEVERITY_NAMES = {
    1: "error",
    2: "warning",
    3: "information",
    4: "hint",
}


def resolve_bsl_ls_binary() -> Path | None:
    configured = os.getenv("BSL_LS_BINARY", "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        return candidate.resolve() if candidate.is_file() else None

    executable = shutil.which("bsl-language-server")
    return Path(executable).resolve() if executable else None


def resolve_bsl_ls_config() -> Path | None:
    configured = os.getenv("BSL_LS_CONFIG", "").strip()
    if not configured:
        return None
    candidate = Path(configured).expanduser()
    return candidate.resolve() if candidate.is_file() else None


def build_bsl_ls_command(binary: Path) -> list[str] | None:
    if binary.suffix.lower() == ".jar":
        java = shutil.which("java")
        if not java:
            return None
        return [java, "-jar", str(binary)]
    return [str(binary)]


def collect_bsl_ls_status(probe_runtime: bool = True) -> dict:
    configured = os.getenv("BSL_LS_BINARY", "").strip()
    binary = resolve_bsl_ls_binary()
    command = build_bsl_ls_command(binary) if binary else None
    runtime_ready = bool(binary and command)
    runtime_version = "not probed"
    runtime_issue = ""
    if runtime_ready and probe_runtime:
        try:
            process = subprocess.run(
                [*command, "version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=min(15, max(1, int(os.getenv("BSL_LS_TIMEOUT", "60")))),
                check=False,
            )
            runtime_ready = process.returncode == 0
            runtime_output = (process.stdout or process.stderr).strip() or "unknown"
            runtime_version = runtime_output if len(runtime_output) <= 2000 else (
                f"{runtime_output[:1000]}\n... output truncated ...\n{runtime_output[-1000:]}"
            )
            if not runtime_ready:
                runtime_issue = runtime_version
        except (OSError, subprocess.SubprocessError) as error:
            runtime_ready = False
            runtime_issue = str(error)
    return {
        "available": runtime_ready,
        "configured": configured or "not set",
        "binary": str(binary) if binary else "not found",
        "config": str(resolve_bsl_ls_config() or "built-in minimal config"),
        "timeout_seconds": max(1, int(os.getenv("BSL_LS_TIMEOUT", "60"))),
        "java": shutil.which("java") or "not found",
        "mode": "jar" if binary and binary.suffix.lower() == ".jar" else "executable",
        "version": runtime_version,
        "runtime_issue": runtime_issue or "none",
    }


def normalize_severity(value) -> str:
    if isinstance(value, int):
        return SEVERITY_NAMES.get(value, "unknown")
    normalized = str(value or "").strip().lower()
    if "error" in normalized:
        return "error"
    if "warn" in normalized:
        return "warning"
    if "info" in normalized:
        return "information"
    if "hint" in normalized:
        return "hint"
    return normalized or "unknown"


def parse_bsl_ls_report(report_path: Path, copied_files: dict[str, Path] | None = None) -> dict:
    with report_path.open(encoding="utf-8") as report_file:
        report = json.load(report_file)

    copied_files = copied_files or {}
    diagnostics = []
    counts = {"error": 0, "warning": 0, "information": 0, "hint": 0, "unknown": 0}
    for file_info in report.get("fileinfos", []):
        reported_path = str(file_info.get("path") or "")
        if reported_path.lower().startswith("file:"):
            normalized_path = reported_path.lower()
        else:
            normalized_path = os.path.normcase(os.path.abspath(reported_path)) if reported_path else ""
        source_path = copied_files.get(normalized_path)
        if source_path is None and reported_path:
            source_path = copied_files.get(reported_path.replace("\\", "/").rsplit("/", 1)[-1].lower())
        for item in file_info.get("diagnostics", []):
            severity = normalize_severity(item.get("severity"))
            counts[severity if severity in counts else "unknown"] += 1
            start = item.get("range", {}).get("start", {})
            diagnostics.append({
                "file": str(source_path or reported_path),
                "line": int(start.get("line") or 0) + 1,
                "column": int(start.get("character") or 0) + 1,
                "code": str(item.get("code") or "unknown"),
                "severity": severity,
                "message": str(item.get("message") or "").strip(),
            })

    return {
        "diagnostics": diagnostics,
        "error_count": counts["error"],
        "warning_count": counts["warning"],
        "information_count": counts["information"],
        "hint_count": counts["hint"],
        "unknown_count": counts["unknown"],
    }


def analyze_bsl_files(files: list[Path], source_root: Path) -> dict:
    status = collect_bsl_ls_status(probe_runtime=False)
    if not status["available"]:
        return {
            "status": "unavailable",
            "message": "BSL Language Server не найден. Укажите BSL_LS_BINARY.",
            "diagnostics": [],
            "error_count": 0,
            "warning_count": 0,
            "information_count": 0,
            "hint_count": 0,
        }
    if not files:
        return {
            "status": "not_run",
            "message": "Нет BSL-файлов для анализа.",
            "diagnostics": [],
            "error_count": 0,
            "warning_count": 0,
            "information_count": 0,
            "hint_count": 0,
        }

    binary = resolve_bsl_ls_binary()
    assert binary is not None
    command_prefix = build_bsl_ls_command(binary)
    assert command_prefix is not None

    with tempfile.TemporaryDirectory(prefix="1c_vector_bsl_ls_") as temp_value:
        temp_root = Path(temp_value)
        source_dir = temp_root / "src"
        output_dir = temp_root / "out"
        source_dir.mkdir()
        output_dir.mkdir()

        copied_files: dict[str, Path] = {}
        for index, source_file in enumerate(files):
            resolved_file = source_file.resolve()
            target = source_dir / f"{index:06d}.bsl"
            shutil.copy2(resolved_file, target)
            copied_files[os.path.normcase(os.path.abspath(target))] = resolved_file
            copied_files[target.resolve().as_uri().lower()] = resolved_file
            copied_files[target.name.lower()] = resolved_file

        config_path = resolve_bsl_ls_config()
        if config_path is None:
            config_path = temp_root / ".bsl-language-server.json"
            config_path.write_text(
                json.dumps({"language": "ru", "diagnostics": {"computeDiagnostics": True}}),
                encoding="utf-8",
            )

        command = [
            *command_prefix,
            "-c",
            str(config_path),
            "analyze",
            "-s",
            str(source_dir),
            "-r",
            "json",
            "-o",
            str(output_dir),
            "-q",
        ]
        try:
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=status["timeout_seconds"],
                check=False,
                cwd=temp_root,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "timeout",
                "message": f"BSL Language Server превысил timeout {status['timeout_seconds']} секунд.",
                "diagnostics": [],
                "error_count": 0,
                "warning_count": 0,
                "information_count": 0,
                "hint_count": 0,
            }
        except OSError as error:
            return {
                "status": "failed",
                "message": f"Не удалось запустить BSL Language Server: {error}",
                "diagnostics": [],
                "error_count": 0,
                "warning_count": 0,
                "information_count": 0,
                "hint_count": 0,
            }

        report_path = output_dir / "bsl-json.json"
        if not report_path.is_file():
            process_output = (process.stderr or process.stdout).strip()
            details = process_output if len(process_output) <= 12000 else (
                f"{process_output[:6000]}\n... output truncated ...\n{process_output[-6000:]}"
            )
            return {
                "status": "failed",
                "message": f"BSL LS не создал JSON-отчет (exit={process.returncode}): {details or 'no output'}",
                "diagnostics": [],
                "error_count": 0,
                "warning_count": 0,
                "information_count": 0,
                "hint_count": 0,
            }

        result = parse_bsl_ls_report(report_path, copied_files)
        result["status"] = "completed"
        result["message"] = f"BSL Language Server завершен с exit code {process.returncode}."
        return result

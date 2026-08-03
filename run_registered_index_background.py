import argparse
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run registered config indexing with timestamped logs.")
    parser.add_argument("--config-id", required=True)
    parser.add_argument("--fastembed-threads", type=int, default=4)
    parser.add_argument("--embedding-provider", choices=("local", "openai"), default="local")
    parser.add_argument("--index-filter", default="")
    parser.add_argument("--graph-only", action="store_true")
    parser.add_argument("--force-reindex", action="store_true")
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved logs and environment without importing index_config.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.fastembed_threads < 1:
        raise ValueError("--fastembed-threads must be greater than zero")

    project_root = Path(__file__).resolve().parent
    log_dir = (project_root / args.log_dir).resolve()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_log = log_dir / f"{args.config_id}_full_{timestamp}.out.log"
    err_log = log_dir / f"{args.config_id}_full_{timestamp}.err.log"

    runtime_env = {
        "ACTIVE_CONFIG_ID": args.config_id,
        "FASTEMBED_THREADS": str(args.fastembed_threads),
        "EMBEDDING_PROVIDER": args.embedding_provider,
        "GRAPH_ONLY": "1" if args.graph_only else "",
        "FORCE_REINDEX": "1" if args.force_reindex else "",
        "INDEX_FILTER": args.index_filter,
    }

    if args.dry_run:
        print("[index-background] dry run")
        print(f"project_root={project_root}")
        print(f"stdout_log={out_log}")
        print(f"stderr_log={err_log}")
        for key, value in runtime_env.items():
            if value:
                print(f"{key}={value}")
        return 0

    log_dir.mkdir(parents=True, exist_ok=True)
    for key, value in runtime_env.items():
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)
    os.chdir(project_root)

    with open(out_log, "w", encoding="utf-8", buffering=1) as stdout_file:
        with open(err_log, "w", encoding="utf-8", buffering=1) as stderr_file:
            with redirect_stdout(stdout_file), redirect_stderr(stderr_file):
                print(
                    f"[{datetime.now().isoformat(timespec='seconds')}] "
                    f"Starting indexer config_id={args.config_id}, "
                    f"EMBEDDING_PROVIDER={args.embedding_provider}, "
                    f"FASTEMBED_THREADS={args.fastembed_threads}, "
                    f"GRAPH_ONLY={bool(args.graph_only)}, "
                    f"FORCE_REINDEX={bool(args.force_reindex)}, "
                    f"INDEX_FILTER={args.index_filter or 'none'}",
                    flush=True,
                )
                print(f"stdout_log={out_log}", flush=True)
                print(f"stderr_log={err_log}", flush=True)
                try:
                    import index_config

                    index_config.process_and_index()
                    print(
                        f"[{datetime.now().isoformat(timespec='seconds')}] Indexing finished",
                        flush=True,
                    )
                    return 0
                except Exception as error:
                    print(
                        f"[{datetime.now().isoformat(timespec='seconds')}] "
                        f"Indexer failed: {error!r}",
                        file=sys.stderr,
                        flush=True,
                    )
                    raise


if __name__ == "__main__":
    raise SystemExit(main())

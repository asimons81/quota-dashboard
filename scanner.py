import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_SAMPLE_INTERVAL_MIN = 10
DEFAULT_MAX_HISTORY = 288
DEFAULT_BLACKLIST = "gemini-2.5-pro,gemini-2.5-flash-thinking,gemini-2.5-flash-lite"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_key(provider: str, label: str) -> str:
    return f"{(provider or '').strip().lower()}:{(label or '').strip().lower()}"


def normalize_percent(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    number = max(0.0, min(100.0, number))
    return round(number, 2)


def normalize_reset_at(value: Any) -> Tuple[Optional[int], Optional[str]]:
    if value in (None, "", "null"):
        return None, None

    try:
        numeric = int(float(value))
    except (TypeError, ValueError):
        return None, None

    if numeric <= 0:
        return None, None

    # Heuristic: if seconds epoch slipped in, convert to ms.
    if numeric < 10_000_000_000:
        numeric *= 1000

    iso = datetime.fromtimestamp(numeric / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return numeric, iso


def atomic_write_json(path: Path, payload: Any) -> None:
    temp_path = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def load_json_safe(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return default


def run_openclaw(timeout_s: int = 30) -> subprocess.CompletedProcess:
    if os.name == "nt":
        command = "openclaw.cmd status --usage --json"
        return subprocess.run(
            ["cmd.exe", "/c", command],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=True,
        )

    return subprocess.run(
        ["openclaw", "status", "--usage", "--json"],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=True,
    )


def build_models(raw_data: Dict[str, Any], scanner_time_iso: str, blacklist: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    providers = raw_data.get("usage", {}).get("providers", [])
    models: List[Dict[str, Any]] = []
    blacklist_set = set(blacklist) if blacklist else set()

    for provider in providers:
        provider_name = str(provider.get("displayName") or provider.get("provider") or "unknown").strip()
        source_last_updated = provider.get("lastUpdated") or provider.get("updatedAt")

        for window in provider.get("windows", []):
            label = str(window.get("label") or "unknown").strip()
            if label in blacklist_set:
                continue

            percent = normalize_percent(window.get("usedPercent"))
            reset_ms, reset_iso = normalize_reset_at(window.get("resetAt"))
            source_window_updated = window.get("lastUpdated") or window.get("updatedAt") or source_last_updated

            model = {
                "key": canonical_key(provider_name, label),
                "provider": provider_name,
                "label": label,
                "percentage": percent,
                "resetAt": reset_ms,
                "resetAtIso": reset_iso,
                "lastUpdated": scanner_time_iso,
            }

            if source_window_updated is not None:
                model["sourceLastUpdated"] = source_window_updated

            models.append(model)

    models.sort(key=lambda item: item["key"])
    return models


def data_semantic_signature(data_payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "models": [
            {
                "key": model.get("key"),
                "provider": model.get("provider"),
                "label": model.get("label"),
                "percentage": model.get("percentage"),
                "resetAt": model.get("resetAt"),
                "resetAtIso": model.get("resetAtIso"),
                "sourceLastUpdated": model.get("sourceLastUpdated"),
            }
            for model in data_payload.get("models", [])
        ]
    }


def update_history(
    history_payload: Dict[str, List[Dict[str, Any]]],
    models: List[Dict[str, Any]],
    timestamp_iso: str,
    sample_interval_min: int,
    max_samples: int,
) -> Tuple[Dict[str, List[Dict[str, Any]]], bool]:
    changed = False
    now_dt = datetime.fromisoformat(timestamp_iso.replace("Z", "+00:00"))

    for model in models:
        key = model["key"]
        series = history_payload.get(key, [])
        entry = {"timestamp": timestamp_iso, "percentage": model["percentage"]}

        should_append = False
        if not series:
            should_append = True
        else:
            last_entry = series[-1]
            last_pct = normalize_percent(last_entry.get("percentage"))
            last_ts_raw = last_entry.get("timestamp")

            elapsed_min = sample_interval_min
            if last_ts_raw:
                try:
                    last_dt = datetime.fromisoformat(last_ts_raw.replace("Z", "+00:00"))
                    elapsed_min = (now_dt - last_dt).total_seconds() / 60
                except ValueError:
                    elapsed_min = sample_interval_min

            pct_changed = abs(last_pct - model["percentage"]) > 0.0001
            should_append = pct_changed or elapsed_min >= sample_interval_min

        if should_append:
            new_series = (series + [entry])[-max_samples:]
            history_payload[key] = new_series
            changed = True

    return history_payload, changed


def run_git(script_dir: Path, commit_message: str, files: List[str], dry_run: bool, no_git: bool) -> None:
    if no_git:
        print("Skipping git operations (--no-git enabled).")
        return

    if dry_run:
        print("Dry-run mode: would run git add/commit/push.")
        return

    subprocess.run(["git", "add", *files], cwd=script_dir, check=True)
    status = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=script_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    staged = [line.strip() for line in status.stdout.splitlines() if line.strip()]
    if not staged:
        print("No staged changes; skipping commit.")
        return

    subprocess.run(["git", "commit", "-m", commit_message], cwd=script_dir, check=True)
    subprocess.run(["git", "push"], cwd=script_dir, check=True)
    print("Successfully committed and pushed changes.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch and persist OpenClaw quota usage.")
    parser.add_argument("--dry-run", action="store_true", help="Process data without writing files or running git.")
    parser.add_argument("--no-git", action="store_true", help="Write output files but skip git add/commit/push.")
    parser.add_argument(
        "--history-interval-min",
        type=int,
        default=int(os.getenv("HISTORY_INTERVAL_MIN", DEFAULT_SAMPLE_INTERVAL_MIN)),
        help="Append history sample when unchanged values age past this many minutes.",
    )
    parser.add_argument(
        "--history-max-samples",
        type=int,
        default=int(os.getenv("HISTORY_MAX_SAMPLES", DEFAULT_MAX_HISTORY)),
        help="Maximum number of history samples retained per key.",
    )
    parser.add_argument(
        "--blacklist",
        type=str,
        default=os.getenv("QUOTA_BLACKLIST", DEFAULT_BLACKLIST),
        help="Comma-separated list of model labels to exclude from the dashboard.",
    )
    return parser.parse_args()


def write_summary(path: Path, summary: Dict[str, Any], dry_run: bool) -> None:
    if dry_run:
        print("Dry-run mode: summary.json not written.")
        print(json.dumps(summary, indent=2))
        return
    atomic_write_json(path, summary)


def main() -> int:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    data_path = script_dir / "data.json"
    history_path = script_dir / "history.json"
    summary_path = script_dir / "summary.json"

    scanner_time_iso = utc_now_iso()

    try:
        result = run_openclaw()
    except FileNotFoundError:
        summary = {
            "lastUpdated": scanner_time_iso,
            "status": "error",
            "error": "openclaw executable not found in PATH",
            "counts": {"models": 0, "historyKeys": 0},
        }
        write_summary(summary_path, summary, args.dry_run)
        print(summary["error"], file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        summary = {
            "lastUpdated": scanner_time_iso,
            "status": "error",
            "error": "openclaw command timed out after 30s",
            "counts": {"models": 0, "historyKeys": 0},
        }
        write_summary(summary_path, summary, args.dry_run)
        print(summary["error"], file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        summary = {
            "lastUpdated": scanner_time_iso,
            "status": "error",
            "error": f"openclaw failed with exit code {exc.returncode}",
            "stderr": (exc.stderr or "").strip(),
            "counts": {"models": 0, "historyKeys": 0},
        }
        write_summary(summary_path, summary, args.dry_run)
        print(summary["error"], file=sys.stderr)
        if summary["stderr"]:
            print(summary["stderr"], file=sys.stderr)
        return 1

    try:
        raw_data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        summary = {
            "lastUpdated": scanner_time_iso,
            "status": "error",
            "error": f"Failed to parse OpenClaw JSON: {exc}",
            "stderr": (result.stderr or "").strip(),
            "stdoutSnippet": (result.stdout or "")[:500],
            "counts": {"models": 0, "historyKeys": 0},
        }
        write_summary(summary_path, summary, args.dry_run)
        print(summary["error"], file=sys.stderr)
        return 1

    blacklist = [item.strip() for item in args.blacklist.split(",") if item.strip()]
    models = build_models(raw_data, scanner_time_iso, blacklist=blacklist)
    new_data_payload = {"lastUpdated": scanner_time_iso, "models": models}

    existing_data = load_json_safe(data_path, {"models": []})
    data_changed = data_semantic_signature(existing_data) != data_semantic_signature(new_data_payload)

    history_payload = load_json_safe(history_path, {})
    if not isinstance(history_payload, dict):
        history_payload = {}

    updated_history, history_changed = update_history(
        history_payload,
        models,
        scanner_time_iso,
        max(1, args.history_interval_min),
        max(1, args.history_max_samples),
    )

    wrote_files: List[str] = []

    if data_changed and not args.dry_run:
        atomic_write_json(data_path, new_data_payload)
        wrote_files.append("data.json")

    if history_changed and not args.dry_run:
        atomic_write_json(history_path, updated_history)
        wrote_files.append("history.json")

    summary = {
        "lastUpdated": scanner_time_iso,
        "status": "ok",
        "error": None,
        "counts": {
            "models": len(models),
            "historyKeys": len(updated_history.keys()),
            "dataChanged": data_changed,
            "historyChanged": history_changed,
        },
    }
    write_summary(summary_path, summary, args.dry_run)

    print(
        f"Scan complete. models={len(models)} dataChanged={data_changed} historyChanged={history_changed} dryRun={args.dry_run}"
    )

    if data_changed or history_changed:
        run_git(
            script_dir,
            commit_message=f"Auto-sync usage data: {scanner_time_iso}",
            files=["data.json", "history.json", "summary.json"],
            dry_run=args.dry_run,
            no_git=args.no_git,
        )
    else:
        print("No meaningful data/history changes; git commit skipped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

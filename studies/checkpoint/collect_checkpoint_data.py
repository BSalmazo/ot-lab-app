#!/usr/bin/env python3
import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests


def now_ts() -> float:
    return time.time()


def read_json_file(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def api_call(session: requests.Session, method: str, url: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    started = now_ts()
    try:
        if method == "GET":
            response = session.get(url, timeout=4)
        else:
            response = session.post(url, json=payload or {}, timeout=6)
        elapsed_ms = round((now_ts() - started) * 1000.0, 2)
        data: Any
        try:
            data = response.json()
        except Exception:
            data = {"raw_text": response.text[:1000]}
        return {
            "ts": started,
            "elapsed_ms": elapsed_ms,
            "http_status": response.status_code,
            "http_ok": response.ok,
            "data": data,
        }
    except Exception as exc:
        elapsed_ms = round((now_ts() - started) * 1000.0, 2)
        return {
            "ts": started,
            "elapsed_ms": elapsed_ms,
            "http_status": None,
            "http_ok": False,
            "error": str(exc),
            "data": {},
        }


def poll_action_status(
    session: requests.Session,
    base_url: str,
    command_id: str,
    max_wait_s: float = 20.0,
    poll_s: float = 0.3,
) -> Dict[str, Any]:
    started = now_ts()
    history = []
    final = None

    while now_ts() - started <= max_wait_s:
        result = api_call(session, "GET", f"{base_url}/api/actions/modbus/commands")
        snapshot = {
            "ts": result["ts"],
            "http_ok": result.get("http_ok"),
            "http_status": result.get("http_status"),
        }
        commands = []
        if isinstance(result.get("data"), dict):
            commands = result["data"].get("commands") or []

        target = None
        for item in commands:
            if item.get("id") == command_id:
                target = item
                break

        snapshot["command"] = target
        history.append(snapshot)

        if target and str(target.get("status", "")).lower() in {"done", "error"}:
            final = target
            break

        time.sleep(poll_s)

    finished = now_ts()
    return {
        "command_id": command_id,
        "started_at": started,
        "finished_at": finished,
        "duration_s": round(finished - started, 3),
        "final_status": (str(final.get("status")).lower() if final else None),
        "final_message": (final.get("message") if final else None),
        "history": history,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect checkpoint evidence for OT Lab agent tests.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--session-id", required=True, help="Session ID used by the agent/UI.")
    parser.add_argument("--duration", type=float, default=1800.0, help="Collection window in seconds.")
    parser.add_argument("--interval", type=float, default=1.0, help="Polling interval in seconds.")
    parser.add_argument("--output-dir", default="artifacts/checkpoint_run")
    parser.add_argument("--action-file", default="", help="Optional JSON payload for POST /api/actions/modbus/execute.")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    status_jsonl = output_dir / "status.jsonl"
    events_jsonl = output_dir / "events.jsonl"
    commands_jsonl = output_dir / "commands.jsonl"
    lifecycle_json = output_dir / "action_lifecycle.json"
    metadata_json = output_dir / "metadata.json"

    session = requests.Session()
    session.cookies.set("scada_session_id", args.session_id)

    metadata = {
        "base_url": base_url,
        "session_id": args.session_id,
        "duration_requested_s": args.duration,
        "interval_s": args.interval,
        "started_at": now_ts(),
        "action_file": args.action_file or None,
    }

    print(f"[collector] collecting into: {output_dir}")
    print(f"[collector] session_id={args.session_id}")

    # Warm-up call (also validates connectivity).
    warmup = api_call(session, "GET", f"{base_url}/api/status")
    append_jsonl(status_jsonl, warmup)
    print(f"[collector] warmup status http={warmup.get('http_status')}")

    action_lifecycle = None
    if args.action_file:
        payload = read_json_file(Path(args.action_file))
        action_result = api_call(session, "POST", f"{base_url}/api/actions/modbus/execute", payload=payload)
        write_json(output_dir / "action_submit_response.json", action_result)
        command_id = None
        if isinstance(action_result.get("data"), dict):
            command_id = action_result["data"].get("command_id")
        if command_id:
            print(f"[collector] action submitted command_id={command_id}")
            action_lifecycle = poll_action_status(session, base_url, command_id=command_id)
            write_json(lifecycle_json, action_lifecycle)
        else:
            print("[collector] action submission did not return command_id")

    end_at = now_ts() + float(args.duration)
    samples = 0
    ok_events = 0

    while now_ts() < end_at:
        status_result = api_call(session, "GET", f"{base_url}/api/status")
        events_result = api_call(session, "GET", f"{base_url}/api/events")
        commands_result = api_call(session, "GET", f"{base_url}/api/actions/modbus/commands")

        append_jsonl(status_jsonl, status_result)
        append_jsonl(events_jsonl, events_result)
        append_jsonl(commands_jsonl, commands_result)

        samples += 1
        if events_result.get("http_ok"):
            ok_events += 1

        time.sleep(max(0.05, float(args.interval)))

    finished_at = now_ts()
    metadata.update(
        {
            "finished_at": finished_at,
            "duration_actual_s": round(finished_at - metadata["started_at"], 3),
            "samples": samples,
            "events_http_ok_ratio": (round(ok_events / samples, 4) if samples else 0.0),
            "action_lifecycle_file": ("action_lifecycle.json" if action_lifecycle else None),
        }
    )
    write_json(metadata_json, metadata)
    print(f"[collector] done. samples={samples} events_http_ok_ratio={metadata['events_http_ok_ratio']}")


if __name__ == "__main__":
    main()


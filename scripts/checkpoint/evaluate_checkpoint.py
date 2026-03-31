#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def extract_events_snapshots(rows: List[Dict[str, Any]]) -> List[Tuple[float, Dict[str, Any]]]:
    out: List[Tuple[float, Dict[str, Any]]] = []
    for row in rows:
        if not row.get("http_ok"):
            continue
        data = row.get("data") or {}
        if not isinstance(data, dict):
            continue
        out.append((float(row.get("ts") or 0.0), data))
    return out


def status_line(name: str, passed: bool, detail: str) -> Dict[str, Any]:
    return {
        "case": name,
        "status": "PASS" if passed else "FAIL",
        "detail": detail,
    }


def evaluate(input_dir: Path, require_multi_interface: bool = True) -> Dict[str, Any]:
    metadata = load_json(input_dir / "metadata.json")
    event_rows = load_jsonl(input_dir / "events.jsonl")
    command_rows = load_jsonl(input_dir / "commands.jsonl")
    action_lifecycle_path = input_dir / "action_lifecycle.json"
    action_lifecycle = load_json(action_lifecycle_path) if action_lifecycle_path.exists() else None

    snapshots = extract_events_snapshots(event_rows)
    results: List[Dict[str, Any]] = []

    # CT1 - basic detection within ~3s (best observed not-detected -> detected transition)
    ct1_pass = False
    ct1_detail = "No detected transition found."
    previous_ts = None
    previous_detected = None
    for ts, payload in snapshots:
        summary = payload.get("modbus_summary") or {}
        events = payload.get("events") or []
        detected = bool(summary.get("detected")) and str(summary.get("state", "")).lower() == "active" and bool(events)
        if previous_detected is False and detected is True and previous_ts is not None:
            delta = ts - previous_ts
            ct1_pass = delta <= 3.0
            ct1_detail = f"Transition observed in {delta:.3f}s (threshold: <=3.0s)."
            break
        previous_ts = ts
        previous_detected = detected
    results.append(status_line("CT1 Detecao basica", ct1_pass, ct1_detail))

    # CT2 - interface correctness
    detected_ifaces = []
    for _ts, payload in snapshots:
        summary = payload.get("modbus_summary") or {}
        if not summary.get("detected"):
            continue
        iface = str(summary.get("interface") or "").strip()
        if iface:
            detected_ifaces.append(iface)
    unique_ifaces = sorted(set(detected_ifaces))
    has_all = any(item.upper() == "ALL" for item in unique_ifaces)
    non_all_count = len([item for item in unique_ifaces if item.upper() != "ALL"])
    if require_multi_interface:
        ct2_pass = (not has_all) and non_all_count >= 2
        ct2_detail = f"Detected interfaces={unique_ifaces}; require >=2 non-ALL interfaces."
    else:
        ct2_pass = (not has_all) and non_all_count >= 1
        ct2_detail = f"Detected interfaces={unique_ifaces}; require >=1 non-ALL interface."
    results.append(status_line("CT2 Interface correta", ct2_pass, ct2_detail))

    # CT3 - writes
    writes_seen = False
    for _ts, payload in snapshots:
        summary = payload.get("modbus_summary") or {}
        if summary.get("writes_detected"):
            writes_seen = True
            break
        for event in payload.get("events") or []:
            event_type = str(event.get("type") or "").upper()
            if "WRITE" in event_type:
                writes_seen = True
                break
        if writes_seen:
            break
    results.append(status_line("CT3 Escritas", writes_seen, "writes_detected true or WRITE_* event observed."))

    # CT4 - exceptions/alerts
    exception_seen = False
    for _ts, payload in snapshots:
        for event in payload.get("events") or []:
            if str(event.get("type") or "").upper() == "EXCEPTION_RESPONSE":
                exception_seen = True
                break
        if exception_seen:
            break
        for alert in payload.get("alerts") or []:
            if str(alert.get("event_type") or "").upper() == "EXCEPTION_RESPONSE":
                exception_seen = True
                break
        if exception_seen:
            break
    results.append(status_line("CT4 Excecoes", exception_seen, "EXCEPTION_RESPONSE event/alert observed."))

    # CT5 - Active -> Inactive transition around 2s
    ct5_pass = False
    ct5_detail = "No Active->Inactive transition observed."
    last_active_ts = None
    for ts, payload in snapshots:
        state = str((payload.get("modbus_summary") or {}).get("state") or "").lower()
        if state == "active":
            last_active_ts = ts
            continue
        if state == "inactive" and last_active_ts is not None:
            gap = ts - last_active_ts
            ct5_pass = gap >= 2.0 and gap <= 6.0
            ct5_detail = f"Active->Inactive observed in {gap:.3f}s (expected around 2s)."
            break
    results.append(status_line("CT5 Estado conexao", ct5_pass, ct5_detail))

    # CT6 - action lifecycle done/error <=20s
    ct6_pass = False
    ct6_detail = "No action lifecycle evidence found."
    if action_lifecycle:
        status = str(action_lifecycle.get("final_status") or "").lower()
        duration = float(action_lifecycle.get("duration_s") or 0.0)
        ct6_pass = status in {"done", "error"} and duration <= 20.0
        ct6_detail = f"Action final_status={status or '-'} duration={duration:.3f}s."
    else:
        for row in command_rows:
            if not row.get("http_ok"):
                continue
            data = row.get("data") or {}
            commands = data.get("commands") or []
            for cmd in commands:
                if str(cmd.get("type") or "") == "RUN_MODBUS_ACTION":
                    if str(cmd.get("status") or "").lower() in {"done", "error"}:
                        ct6_pass = True
                        ct6_detail = "RUN_MODBUS_ACTION reached done/error (duration unavailable)."
                        break
            if ct6_pass:
                break
    results.append(status_line("CT6 Acoes remotas", ct6_pass, ct6_detail))

    # CT7 - stability for load window
    ratio = float(metadata.get("events_http_ok_ratio") or 0.0)
    duration_actual = float(metadata.get("duration_actual_s") or 0.0)
    ct7_pass = ratio >= 0.95 and duration_actual >= 300.0
    ct7_detail = f"events_http_ok_ratio={ratio:.4f}, duration_actual_s={duration_actual:.3f}."
    results.append(status_line("CT7 Estabilidade", ct7_pass, ct7_detail))

    overall_pass = all(item["status"] == "PASS" for item in results)
    return {
        "overall": "CHECKPOINT APROVADO" if overall_pass else "CHECKPOINT REPROVADO",
        "results": results,
        "metadata": metadata,
    }


def to_markdown(report: Dict[str, Any]) -> str:
    lines = []
    lines.append("# Checkpoint Report")
    lines.append("")
    lines.append(f"**Decision:** {report['overall']}")
    lines.append("")
    lines.append("## Test Cases")
    lines.append("")
    for row in report["results"]:
        lines.append(f"- **{row['case']}**: {row['status']} - {row['detail']}")
    lines.append("")
    lines.append("## Metadata")
    lines.append("")
    lines.append(f"- Base URL: `{report['metadata'].get('base_url')}`")
    lines.append(f"- Session ID: `{report['metadata'].get('session_id')}`")
    lines.append(f"- Duration (actual): `{report['metadata'].get('duration_actual_s')}` s")
    lines.append(f"- Poll interval: `{report['metadata'].get('interval_s')}` s")
    lines.append(f"- Events OK ratio: `{report['metadata'].get('events_http_ok_ratio')}`")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate OT Lab checkpoint evidence.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output", default="")
    parser.add_argument("--single-interface-ok", action="store_true", help="Allow CT2 with >=1 non-ALL interface.")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    report = evaluate(input_dir, require_multi_interface=not args.single_interface_ok)

    report_json = input_dir / "checkpoint_result.json"
    report_json.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")

    markdown = to_markdown(report)
    if args.output:
        Path(args.output).write_text(markdown + "\n", encoding="utf-8")
    else:
        print(markdown)

    print(f"[evaluate] {report['overall']}")
    print(f"[evaluate] json={report_json}")


if __name__ == "__main__":
    main()


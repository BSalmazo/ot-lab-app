from __future__ import annotations

import time
import uuid
from dataclasses import dataclass


PROCESS_PROFILES = {
    "tank_v1": {
        "id": "tank_v1",
        "version": "1.0.0",
        "name": "Tank Process v1",
        "protocols": ["modbus", "ethercat"],
        "states": ["idle", "fill", "drain", "alarm"],
        "variables": [
            {"tag": "level", "unit": "%", "min": 0, "max": 100},
            {"tag": "pump", "type": "bool"},
            {"tag": "valve", "type": "bool"},
            {"tag": "alarm_hi", "type": "bool"},
            {"tag": "alarm_lo", "type": "bool"},
        ],
        "io_map": {
            "modbus": {"pump": 2, "valve": 3, "alarm_hi_th": 8, "alarm_lo_th": 9},
            "ethercat": {"pump": "0x7000:02", "valve": "0x7000:03", "level": "0x6000:00"},
        },
        "constraints": {
            "level_hi_block": 95,
            "level_lo_block": 5,
            "min_command_interval_s": 0.15,
        },
    },
    "pumping_line_v1": {
        "id": "pumping_line_v1",
        "version": "1.0.0",
        "name": "Pumping Line v1",
        "protocols": ["modbus", "ethercat"],
        "states": ["idle", "transfer", "interlocked"],
        "variables": [
            {"tag": "line_pressure", "unit": "bar", "min": 0, "max": 16},
            {"tag": "pump", "type": "bool"},
            {"tag": "valve", "type": "bool"},
        ],
        "io_map": {
            "modbus": {"pump": 2, "valve": 3},
            "ethercat": {"pump": "0x7000:02", "valve": "0x7000:03"},
        },
        "constraints": {"min_command_interval_s": 0.20},
    },
}


SEMANTIC_POLICIES = {
    "tank_v1": {
        "policy_id": "policy_tank_v1",
        "version": "1.0.0",
        "rules": [
            {"id": "R001", "type": "critical", "name": "Write requires running process"},
            {"id": "R002", "type": "critical", "name": "Prevent pump ON at high level"},
            {"id": "R003", "type": "critical", "name": "Prevent valve ON at very low level"},
            {"id": "R004", "type": "warning", "name": "Valve ON without recent pump activity"},
            {"id": "R005", "type": "warning", "name": "Command burst / replay pattern"},
        ],
    },
    "pumping_line_v1": {
        "policy_id": "policy_pumping_line_v1",
        "version": "1.0.0",
        "rules": [
            {"id": "R001", "type": "critical", "name": "Write requires running process"},
            {"id": "R005", "type": "warning", "name": "Command burst / replay pattern"},
        ],
    },
}


ATTACK_LIBRARY = {
    "wrong_timing_write": {
        "id": "wrong_timing_write",
        "name": "Legitimate command at wrong timing",
        "framework": "ATT&CK ICS",
        "technique": "T0855 (Change Operating Mode)",
        "profile": "tank_v1",
        "steps": [
            {"address": 3, "value": 1, "delay_s": 0.1},
            {"address": 3, "value": 0, "delay_s": 0.1},
        ],
    },
    "setpoint_out_of_envelope": {
        "id": "setpoint_out_of_envelope",
        "name": "Out-of-envelope setpoint",
        "framework": "ATT&CK ICS",
        "technique": "T0831 (Manipulation of Control)",
        "profile": "tank_v1",
        "steps": [
            {"address": 8, "value": 99, "delay_s": 0.1},
            {"address": 9, "value": 1, "delay_s": 0.1},
        ],
    },
    "sequence_violation": {
        "id": "sequence_violation",
        "name": "Valid command with invalid sequence",
        "framework": "ATT&CK ICS",
        "technique": "T0806 (Command-Line Interface misuse analogue)",
        "profile": "tank_v1",
        "steps": [
            {"address": 3, "value": 1, "delay_s": 0.05},
            {"address": 2, "value": 0, "delay_s": 0.05},
        ],
    },
    "write_burst_replay": {
        "id": "write_burst_replay",
        "name": "Write burst / replay",
        "framework": "ATT&CK ICS",
        "technique": "T0813 (Denial style via command flood)",
        "profile": "tank_v1",
        "steps": [
            {"address": 2, "value": 1, "delay_s": 0.02},
            {"address": 2, "value": 0, "delay_s": 0.02},
            {"address": 2, "value": 1, "delay_s": 0.02},
            {"address": 2, "value": 0, "delay_s": 0.02},
        ],
    },
}


@dataclass
class PolicyDecision:
    decision: str
    rule_id: str
    reason: str
    risk_score: int
    impact_avoided: str | None = None


def _extract_level(register_values: list[int]) -> int:
    if not register_values:
        return 0
    try:
        return int(register_values[0])
    except Exception:
        return 0


def evaluate_semantic_policy(
    *,
    profile_id: str,
    process_running: bool,
    register_values: list[int],
    address: int,
    value: int,
    now_ts: float,
    last_writes: list[dict],
) -> PolicyDecision:
    profile = PROCESS_PROFILES.get(profile_id) or PROCESS_PROFILES["tank_v1"]
    constraints = profile.get("constraints") or {}
    level = _extract_level(register_values)

    if not process_running:
        return PolicyDecision("BLOCK", "R001", "Process is not running for command execution", 95, "Unsafe write while process offline")

    if address == 2 and value == 1 and level >= int(constraints.get("level_hi_block", 95)):
        return PolicyDecision("BLOCK", "R002", f"Pump ON denied: level={level} is above high bound", 92, "Overflow escalation avoided")

    if address == 3 and value == 1 and level <= int(constraints.get("level_lo_block", 5)):
        return PolicyDecision("BLOCK", "R003", f"Valve ON denied: level={level} is below low bound", 90, "Dry-run / drain-risk avoided")

    recent_same = [
        item for item in last_writes
        if int(item.get("address", -1)) == int(address)
        and (now_ts - float(item.get("ts", 0))) <= float(constraints.get("min_command_interval_s", 0.15))
    ]
    if recent_same:
        return PolicyDecision("ALLOW_WITH_ALERT", "R005", "High-frequency repeated write pattern detected", 72, None)

    if address == 3 and value == 1:
        recent_pump_on = any(
            int(item.get("address", -1)) == 2
            and int(item.get("value", 0)) == 1
            and (now_ts - float(item.get("ts", 0))) <= 3.0
            for item in last_writes
        )
        if not recent_pump_on:
            return PolicyDecision("ALLOW_WITH_ALERT", "R004", "Valve ON without recent pump activation", 66, None)

    return PolicyDecision("ALLOW", "R000", "Command accepted by semantic policy", 25, None)


def ai_support_for_decision(decision: PolicyDecision) -> dict:
    if decision.decision == "BLOCK":
        suggestion = "Review process sequence and state preconditions before reissuing this command."
    elif decision.decision == "ALLOW_WITH_ALERT":
        suggestion = "Command allowed, but should be reviewed for contextual consistency."
    else:
        suggestion = "Behavior aligned with current semantic policy."
    return {
        "risk_score": decision.risk_score,
        "explanation": decision.reason,
        "suggested_policy_adjustment": suggestion,
    }


def make_policy_trace_entry(*, profile_id: str, command: dict, decision: PolicyDecision, ai_meta: dict) -> dict:
    return {
        "id": f"pd_{uuid.uuid4().hex[:16]}",
        "timestamp": time.time(),
        "profile_id": profile_id,
        "command": command,
        "decision": decision.decision,
        "rule_id": decision.rule_id,
        "reason": decision.reason,
        "impact_avoided": decision.impact_avoided,
        "ai": ai_meta,
    }


def evaluate_execution_impact(entries: list[dict], final_registers: list[int]) -> dict:
    blocked = sum(1 for e in entries if e.get("decision") == "BLOCK")
    warned = sum(1 for e in entries if e.get("decision") == "ALLOW_WITH_ALERT")
    allowed = sum(1 for e in entries if e.get("decision") == "ALLOW")
    level = _extract_level(final_registers)
    physical_risk = 100 if level >= 96 or level <= 2 else (60 if level >= 90 or level <= 5 else 20)
    score = max(0, physical_risk - blocked * 15)
    return {
        "blocked": blocked,
        "warned": warned,
        "allowed": allowed,
        "final_level": level,
        "impact_score": score,
    }

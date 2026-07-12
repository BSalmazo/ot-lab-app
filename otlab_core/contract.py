"""The NormalizedEvent contract (Phase 1A).

``NormalizedEvent`` is the protocol-neutral, in-memory representation of a single observed
control-network event. Every ``ProtocolExtractor`` produces these; protocol-specific fields
(Modbus ``function_code``/``unit_id``/``transaction_id``, OPC UA node attributes later, ...)
live in the ``raw`` bag rather than as first-class fields.

Design constraint for this phase: ``to_wire_dict()`` must reproduce the EXACT JSON the OT Lab
backend already consumes over ``/api/agent/events_batch`` (see ``scripts/tshark_runtime.py`` on
the ``v2-dev`` baseline), so introducing this type changes no downstream behaviour. The two new
forward-looking fields (``security_mode`` and ``state_signal_value``) are therefore held in
memory only and are intentionally NOT emitted onto the wire in Phase 1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class NormalizedEvent:
    # --- protocol-neutral fields the current Modbus _parse_line already produces ---
    timestamp: float
    protocol: str                       # e.g. "MODBUS/TCP"
    op: str                             # normalized operation / event type, e.g. "WRITE_REQUEST"
    direction: str                      # "request" | "response"
    src_ip: Optional[str] = None
    src_port: Optional[int] = None
    dst_ip: Optional[str] = None
    dst_port: Optional[int] = None
    client: Optional[str] = None        # "ip:port" of the client endpoint
    server: Optional[str] = None        # "ip:port" of the server endpoint
    target: Optional[int] = None        # normalized target address (Modbus register/coil; OPC UA node later)
    value: Any = None                   # written / observed value
    quantity: Optional[int] = None
    summary: str = ""

    # --- new forward-looking fields (Phase 1A) ---
    security_mode: str = "clear"                # Modbus is always cleartext
    state_signal_value: Optional[float] = None  # reconstructed process-variable sample, else None

    # --- protocol-specific bag (function_code, unit_id, transaction_id, exception_code, ...) ---
    raw: dict = field(default_factory=dict)

    def to_wire_dict(self, session_id: str, agent_id: str, iface: str) -> dict:
        """Serialise to the exact legacy event dict posted to /api/agent/events_batch.

        The key set and insertion order match ``scripts/tshark_runtime.py`` as of the
        ``v2-dev`` baseline. ``session_id``/``agent_id``/``iface`` are runtime context and
        are injected here rather than carried on the event. The NormalizedEvent-only fields
        (``security_mode``, ``state_signal_value``, ``raw`` internals such as
        ``reg_val_list``) are deliberately omitted so the posted JSON is unchanged.
        """
        return {
            "session_id": session_id,
            "agent_id": agent_id,
            "timestamp": self.timestamp,
            "src_ip": self.src_ip,
            "src_port": self.src_port,
            "dst_ip": self.dst_ip,
            "dst_port": self.dst_port,
            "client": self.client,
            "server": self.server,
            "direction": self.direction,
            "transaction_id": self.raw.get("transaction_id"),
            "function_code": self.raw.get("function_code"),
            "unit_id": self.raw.get("unit_id"),
            "protocol": self.protocol,
            "type": self.op,
            "register": self.target,
            "start_addr": self.target,
            "quantity": self.quantity,
            "value": self.value,
            "exception_code": self.raw.get("exception_code"),
            "iface": iface,
            "summary": self.summary,
        }

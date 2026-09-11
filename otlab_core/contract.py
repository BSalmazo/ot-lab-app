"""The NormalizedEvent contract.

``NormalizedEvent`` is the protocol-neutral, in-memory representation of a single observed
control-network event. Every ``ProtocolExtractor`` produces these; protocol-specific fields
(Modbus ``function_code``/``unit_id``/``transaction_id``, OPC UA service and variant ids, S7 items)
live in the ``raw`` bag rather than as first-class fields.

``to_wire_dict()`` is the flat dictionary shape of the retired OT Lab backend (tag
``legacy-engine-v1``); it is kept because ``tests/test_modbus_parity.py`` pins the Modbus parse to it,
and nothing in the observer calls it.
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

    # --- protocol-neutral fields the observer consumes ---
    security_mode: str = "clear"                # Modbus is always cleartext
    state_signal_value: Optional[float] = None  # reconstructed process-variable sample, else None

    # --- protocol-specific bag (function_code, unit_id, transaction_id, exception_code, ...) ---
    raw: dict = field(default_factory=dict)

    def to_wire_dict(self, session_id: str, agent_id: str, iface: str) -> dict:
        """Serialise to the flat event dict of the retired backend (kept for the Modbus parity test).

        ``session_id``/``agent_id``/``iface`` are runtime context injected here rather than carried
        on the event. ``security_mode``, ``state_signal_value`` and ``raw`` internals are omitted.
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

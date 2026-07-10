"""OPC UA (opc.tcp binary) passive extractor (Phase 2).

Field names and semantics are EMPIRICALLY CONFIRMED against the lab's own pcaps on the Pi
(tshark 4.4.15): none / sign / signAndEncrypt. See the recon notes; the key facts:

- Every application message is ``opcua.transport.type == MSG``; the service is the discriminator
  ``opcua.servicenodeid.numeric`` (631 ReadReq / 634 ReadResp / 673 WriteReq / 676 WriteResp /
  826 PublishReq / 829 PublishResp).
- Target NodeId = ``opcua.nodeid.numeric`` + ``opcua.nodeid.nsindex`` (Siemens uses NUMERIC ids).
  These are MULTI-VALUED lists under ``-E occurrence=a``: e.g. numeric "847832407,0,23",
  nsindex "0,4". The first element (ns=0) is the request AuthenticationToken, NOT the target;
  the target is the element whose nsindex != 0 (here ns=4, i=23). Never take element [0].
- Value = the typed field indicated by ``opcua.variant.has_value`` (0x0a=Float; the tank level is
  a Float ~47.x). Float -> opcua.Float, Int32 -> opcua.Int32, else opcua.Value.
- Under SignAndEncrypt the MSG body is opaque: service/nodeid/value are empty; only the envelope
  (transport.type, transport.scid, size) survives -> security_mode "encrypted".
- Security policy / mode fields (opcua.security.spu / opcua.MessageSecurityMode) live in the OPN
  handshake, not in MSG frames.

This extractor is protocol-specific; it produces the SAME NormalizedEvent the learned engine
already consumes (state_signal_value + a normalized target/value). engine/ is untouched.
"""

from __future__ import annotations

from typing import Any, List, Optional

from ..config import OpcUaConfig
from ..contract import NormalizedEvent
from .base import ProtocolExtractor

# opcua.servicenodeid.numeric -> normalized operation (empirically confirmed on the lab pcaps).
SERVICE_OP = {
    673: "WRITE_REQUEST",
    676: "WRITE_RESPONSE",
    631: "READ_REQUEST",
    634: "READ_RESPONSE",
    829: "PUBLISH_RESPONSE",
    826: "PUBLISH_REQUEST",
}

# OPC UA BuiltInType ids we read a scalar value from.
VARIANT_FLOAT = 0x0A
VARIANT_INT32 = 0x06

# Column order MUST match tshark_fields() below (the runtime builds `-e` from it in order).
_FIELDS = [
    "frame.time_epoch",        # 0
    "frame.protocols",         # 1
    "ip.src",                  # 2
    "ip.dst",                  # 3
    "tcp.srcport",             # 4
    "tcp.dstport",             # 5
    "opcua.transport.type",    # 6
    "opcua.servicenodeid.numeric",  # 7
    "opcua.nodeid.numeric",    # 8
    "opcua.nodeid.nsindex",    # 9
    "opcua.Float",             # 10
    "opcua.Int32",             # 11
    "opcua.Value",             # 12
    "opcua.variant.has_value", # 13
    "opcua.transport.scid",    # 14
    "opcua.transport.size",    # 15
]


def _to_int(value, default=None):
    if value is None:
        return default
    raw = str(value).strip()
    if raw == "":
        return default
    try:
        return int(raw, 0)  # base 0 => handles "0x0a" too
    except Exception:
        return default


def _first(value):
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    return raw.split(",")[0].strip()


def _int_list(value):
    if value is None:
        return []
    raw = str(value).strip()
    if not raw:
        return []
    out = []
    for token in raw.split(","):
        n = _to_int(token.strip())
        if n is not None:
            out.append(n)
    return out


def _col(cols: List[str], idx: int) -> str:
    return cols[idx] if idx < len(cols) else ""


class OpcUaExtractor(ProtocolExtractor):
    name = "OPCUA/binary"

    def __init__(self, config: Optional[OpcUaConfig] = None):
        self.config = config or OpcUaConfig()

    # OPC UA NodeId/value fields are legitimately multi-valued -> request all occurrences.
    def occurrence(self) -> str:
        return "a"

    def tshark_fields(self) -> List[str]:
        return list(_FIELDS)

    def capture_filter(self) -> str:
        return f"tcp port {int(self.config.port)}"

    def security_mode(self, cols: List[str]) -> str:
        # A MSG frame with no dissectable service body is an encrypted (SignAndEncrypt)
        # SecureConversation: only the envelope survives. Everything else is "clear"
        # (None or Sign both leave the body dissectable; distinguishing the two needs the
        # OPN handshake fields, opcua.security.spu / opcua.MessageSecurityMode).
        transport_type = _col(cols, 6).strip()
        service = _first(_col(cols, 7))
        if transport_type == "MSG" and not service:
            return "encrypted"
        return "clear"

    def parse_line(self, cols: List[str]) -> Optional[NormalizedEvent]:
        protocols = _col(cols, 1).lower()
        transport_type = _col(cols, 6).strip()
        # Gate: only OPC UA frames. Encrypted MSG frames still carry the opcua layer and are
        # kept (emitted blind), so we gate on the layer, not on payload readability.
        if "opcua" not in protocols and not transport_type:
            return None

        ts_raw = _col(cols, 0)
        try:
            ts = float(ts_raw) if ts_raw else None
        except Exception:
            ts = None
        if ts is None:
            import time
            ts = time.time()

        src_ip = _col(cols, 2) or None
        dst_ip = _col(cols, 3) or None
        src_port = _to_int(_col(cols, 4))
        dst_port = _to_int(_col(cols, 5))
        if not src_ip or not dst_ip:
            return None

        service = _to_int(_first(_col(cols, 7)))
        op = SERVICE_OP.get(service, "UNKNOWN") if service is not None else "UNKNOWN"

        # --- target NodeId: pick the element whose nsindex != 0 ---
        numerics = _int_list(_col(cols, 8))
        nsindexes = _int_list(_col(cols, 9))
        target, auth_token = self._select_target(numerics, nsindexes)

        # --- value: read the typed field indicated by the variant type ---
        variant = _to_int(_first(_col(cols, 13)))
        value, value_is_float = self._read_value(cols, variant)

        is_req = op.endswith("REQUEST")
        is_resp = op.endswith("RESPONSE")
        direction = "request" if is_req else ("response" if is_resp else "unknown")
        client_ep = f"{src_ip}:{src_port}" if is_req else f"{dst_ip}:{dst_port}"
        server_ep = f"{dst_ip}:{dst_port}" if is_req else f"{src_ip}:{src_port}"

        scid = _to_int(_first(_col(cols, 14)))
        size = _to_int(_first(_col(cols, 15)))
        sec_mode = self.security_mode(cols)

        summary = (
            f"OPCUA {op} from {src_ip}:{src_port} to {dst_ip}:{dst_port}"
            + (f" | node={target}" if target else "")
            + (f" value={value}" if value is not None else "")
            + (" | encrypted" if sec_mode == "encrypted" else "")
        )

        evt = NormalizedEvent(
            timestamp=ts,
            protocol="OPCUA/binary",
            op=op,
            direction=direction,
            src_ip=src_ip,
            src_port=src_port,
            dst_ip=dst_ip,
            dst_port=dst_port,
            client=client_ep,
            server=server_ep,
            target=target,
            value=value,
            quantity=None,
            summary=summary,
            security_mode=sec_mode,
            raw={
                "servicenodeid": service,
                "scid": scid,
                "size": size,
                "auth_token": auth_token,   # ns=0 nodeid element: session/actor signal (mainly on requests)
                "variant_type": variant,
                "value_is_float": value_is_float,
                "transport_type": transport_type,
            },
        )
        evt.state_signal_value = self.extract_state_signal(evt)
        return evt

    def _select_target(self, numerics: List[int], nsindexes: List[int]):
        """Return (target, auth_token).

        target is "ns=<nsindex>;i=<numeric>" for the element whose nsindex != 0, or None.
        auth_token is the leading ns=0 NodeId element (numerics[0]) — the request's
        AuthenticationToken, kept as an actor signal.

        The numeric and nsindex lists can differ in length (TwoByte NodeIds carry no explicit
        namespace and so emit no nsindex), so we tail-align nsindex onto numeric: the target
        node and its namespace sit at/after the request header, and the confirmed example
        (numeric "847832407,0,23", nsindex "0,4" -> ns=4;i=23) aligns correctly this way.
        """
        auth_token = numerics[0] if numerics else None
        if not numerics or not nsindexes:
            return None, auth_token
        tail = numerics[-len(nsindexes):]
        for num, ns in zip(tail, nsindexes):
            if ns != 0:
                return f"ns={ns};i={num}", auth_token
        return None, auth_token

    def _read_value(self, cols: List[str], variant: Optional[int]):
        """Return (value, value_is_float) reading the field indicated by the variant type."""
        if variant == VARIANT_FLOAT:
            raw = _first(_col(cols, 10))  # opcua.Float
            is_float = True
        elif variant == VARIANT_INT32:
            raw = _first(_col(cols, 11))  # opcua.Int32
            is_float = False
        else:
            raw = _first(_col(cols, 12))  # opcua.Value (FT_FLOAT)
            is_float = True
        if raw is None:
            return None, is_float
        try:
            return (float(raw) if is_float else int(raw, 0)), is_float
        except Exception:
            return None, is_float

    def extract_state_signal(self, evt: NormalizedEvent) -> Optional[float]:
        # Replaces Modbus "element [5]": the process variable is the Float value carried in a
        # read/publish response.
        #
        # DEBT D1 (single-tag assumption): with no configured state_signal_node we infer the
        # state signal as "the Float in a read/publish response". This is valid ONLY because a
        # single tag is subscribed. For multiple monitored items, a PublishResponse conveys the
        # value keyed by ClientHandle (not NodeId), so correct attribution needs a
        # ClientHandle->NodeId map built from CreateMonitoredItems. Not handled in Phase 2.
        if evt.op not in ("READ_RESPONSE", "PUBLISH_RESPONSE"):
            return None
        if not evt.raw.get("value_is_float"):
            return None
        if not isinstance(evt.value, (int, float)):
            return None
        node = self.config.state_signal_node
        if node and evt.target is not None and evt.target != node:
            return None
        return float(evt.value)

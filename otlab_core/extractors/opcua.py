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

from collections import Counter
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

# OPC UA Variant Type (BuiltInType id) -> human type name. The wire DECLARES this
# (opcua.variant.has_value), so a datatype here is certain. Unmapped ids -> None (uncertain).
VARIANT_TYPES = {
    0x01: "Boolean", 0x02: "SByte", 0x03: "Byte",
    0x04: "Int16", 0x05: "UInt16", 0x06: "Int32", 0x07: "UInt32",
    0x08: "Int64", 0x09: "UInt64", 0x0A: "Float", 0x0B: "Double",
    0x0C: "String",
}

# Variant BuiltInType id -> (column index in _FIELDS, parse kind). tshark exposes a typed field
# per built-in scalar (verified with `tshark -G fields`: opcua.Boolean=FT_BOOLEAN, the integer
# types FT_INT*/FT_UINT*, opcua.Float/Double FT_FLOAT/FT_DOUBLE, opcua.String FT_STRING).
# Float(10)/Int32(11)/Value(12) predate this expansion; every other typed column is APPENDED to
# _FIELDS (indices 16+) so the existing 0-15 indices never shift. Unmapped ids fall back to the
# generic opcua.Value (FT_FLOAT) field.
_VARIANT_COL = {
    0x01: (16, "bool"),    # Boolean -> opcua.Boolean
    0x02: (17, "int"),     # SByte   -> opcua.SByte
    0x03: (18, "int"),     # Byte    -> opcua.Byte
    0x04: (19, "int"),     # Int16   -> opcua.Int16
    0x05: (20, "int"),     # UInt16  -> opcua.UInt16
    0x06: (11, "int"),     # Int32   -> opcua.Int32   (pre-existing column)
    0x07: (21, "int"),     # UInt32  -> opcua.UInt32
    0x08: (22, "int"),     # Int64   -> opcua.Int64
    0x09: (23, "int"),     # UInt64  -> opcua.UInt64
    0x0A: (10, "float"),   # Float   -> opcua.Float   (pre-existing column)
    0x0B: (24, "float"),   # Double  -> opcua.Double
    0x0C: (25, "str"),     # String  -> opcua.String
}
_VALUE_FALLBACK_COL = 12   # opcua.Value (FT_FLOAT), for an unknown/unmapped variant type


def variant_typename(variant):
    """(typename, certain). Certain iff the protocol declared a variant we recognise."""
    if variant is None:
        return None, False
    name = VARIANT_TYPES.get(variant)
    return name, (name is not None)

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
    # --- typed value columns (APPENDED so indices 0-15 above never shift; see _VARIANT_COL) ---
    "opcua.Boolean",           # 16
    "opcua.SByte",             # 17
    "opcua.Byte",              # 18
    "opcua.Int16",             # 19
    "opcua.UInt16",            # 20
    "opcua.UInt32",            # 21
    "opcua.Int64",             # 22
    "opcua.UInt64",            # 23
    "opcua.Double",            # 24
    "opcua.String",            # 25
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


def _float_list(value):
    """Parse a comma-list of floats in wire order (empty/keep-alive -> [])."""
    if value is None:
        return []
    raw = str(value).strip()
    if not raw:
        return []
    out = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            out.append(float(token))
        except Exception:
            continue
    return out


def _col(cols: List[str], idx: int) -> str:
    return cols[idx] if idx < len(cols) else ""


def _parse_bool(raw):
    """tshark FT_BOOLEAN under -T fields -> 1/0 (or True/False). Returns int 1/0 or None."""
    s = str(raw).strip().lower()
    if s in ("1", "true", "0x1"):
        return 1
    if s in ("0", "false", "0x0"):
        return 0
    n = _to_int(s)
    return None if n is None else (1 if n != 0 else 0)


def _as_number(value):
    """Coerce a parsed OPC UA value to a float for the sample series, or None.

    Numeric types (int/float/bool/numeric string) become a float; a String value (or anything
    non-numeric) returns None so it is NOT appended as a phase/step sample — the flow still
    exists via its role hint (see group_flows)."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
        # A response may carry MANY Float samples in one message (occurrence=a comma-list),
        # in wire order (first = older, last = newest). Keep them ALL; never assume a count.
        float_samples = _float_list(_col(cols, 10))

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
                "float_samples": float_samples,   # ALL Float samples in this message, wire order
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
        """Return (value, value_is_float) reading the typed field the variant type declares.

        Decodes the common OPC UA built-in scalars — Boolean, SByte, Byte, Int16, UInt16, Int32,
        UInt32, Int64, UInt64, Float, Double, String (see _VARIANT_COL). Integers come back as
        int, floats/doubles as float, Boolean as int 0/1, String as the raw text. An unknown or
        unmapped variant falls back to the generic opcua.Value (FT_FLOAT) field, as before.
        """
        col, kind = _VARIANT_COL.get(variant, (_VALUE_FALLBACK_COL, "float"))
        is_float = (kind == "float")
        raw = _first(_col(cols, col))
        if raw is None:
            return None, is_float
        try:
            if kind == "float":
                return float(raw), True
            if kind == "int":
                return int(raw, 0), False
            if kind == "bool":
                return _parse_bool(raw), False
            return raw, False  # str
        except Exception:
            return None, is_float

    def extract_state_samples(self, evt: NormalizedEvent) -> List[float]:
        """ALL process-variable samples carried by this event, in wire order (oldest -> newest).

        A single OPC UA response can batch several Float samples in one message (the
        ``opcua.Float`` comma-list under ``-E occurrence=a``). This returns every one of them,
        in order, making NO assumption about how many a message carries or about the sampling
        rate: 1, 2, ... N are all returned as-is; a keep-alive with no Float returns ``[]``.

        Replaces Modbus "element [5]": the process variable is the Float value(s) carried in a
        read/publish response.

        DEBT D1 (single-tag assumption): with no configured state_signal_node we infer the state
        signal as "the Float(s) in a read/publish response". Valid ONLY because a single tag is
        subscribed. For multiple monitored items, a PublishResponse conveys values keyed by
        ClientHandle (not NodeId), so correct attribution needs a ClientHandle->NodeId map built
        from CreateMonitoredItems. Not handled here.
        """
        if evt.op not in ("READ_RESPONSE", "PUBLISH_RESPONSE"):
            return []
        node = self.config.state_signal_node
        if node and evt.target is not None and evt.target != node:
            return []
        return list(evt.raw.get("float_samples") or [])

    def extract_state_signal(self, evt: NormalizedEvent) -> Optional[float]:
        """The single newest state sample, or None.

        Convenience for callers that want one value per event: returns the LAST (newest) element
        of ``extract_state_samples(evt)``. Consumers driving phase inference should prefer
        ``extract_state_samples`` and feed every sample, in order, so no samples are dropped.
        """
        samples = self.extract_state_samples(evt)
        return samples[-1] if samples else None

    def group_flows(self, events) -> list:
        """Group parsed OPC UA events into role-hinted flows for the behavioural classifier.

        Protocol-specific ROLE HINTS reinforce (they do not replace) the core's behavioural
        decision (otlab_core.engine.discover):

        - PublishResponse (829) Floats -> ONE flow, role_hint="telemetry" (the state candidate),
          unfolding each message's multi-sample Float list into the full per-sample series.
          DEBT D1 (single-tag): all publish Floats are treated as one flow. Multi-tag correlation
          (ClientHandle -> NodeId from CreateMonitoredItems) is level 4b.
        - WriteRequest (673) -> flows keyed by target NodeId (the ns!=0 element, from the existing
          tail-aligned target parse), role_hint="command".
        - ReadResponse (634) -> a "read" flow, role_hint="read" (metadata).
        """
        from ..engine.discover import Flow  # local import: keep extractor import lightweight

        # Per flow: (t, value) samples plus the variant type ids seen, so each flow's declared
        # data type (the modal Variant Type) travels with it.
        samples: dict = {}    # key -> list[(t, value)]
        roles: dict = {}      # key -> role_hint
        variants: dict = {}   # key -> list[variant_type id]
        for evt in events:
            key = self.variable_key(evt)
            if key is None:
                continue
            if evt.op == "PUBLISH_RESPONSE":
                roles.setdefault(key, "telemetry")
                for s in self.extract_state_samples(evt):
                    samples.setdefault(key, []).append((evt.timestamp, float(s)))
            elif evt.op == "WRITE_REQUEST":
                # FIX #1: the command flow exists as soon as its target NodeId is seen — the same
                # target-based gate the evaluator uses — so a command ALWAYS yields a variable_found
                # (hence a VARIABLES row), even when its value did not parse to a number (unknown
                # type / encrypted). A numeric value, when present, is recorded as a sample.
                roles.setdefault(key, "command")
                num = _as_number(evt.value)
                if num is not None:
                    samples.setdefault(key, []).append((evt.timestamp, num))
            elif evt.op == "READ_RESPONSE":
                num = _as_number(evt.value)
                if num is None:
                    continue
                roles.setdefault(key, "read")
                samples.setdefault(key, []).append((evt.timestamp, num))
            else:
                continue
            vt = evt.raw.get("variant_type")
            if vt is not None:
                variants.setdefault(key, []).append(vt)

        # Build one Flow per discovered role key (NOT per sample key): a value-less command has a
        # role but no samples, and must still become a Flow so it is classified and surfaced.
        flows: list = []
        for key, role in roles.items():
            datatype, certain = self._modal_datatype(variants.get(key, []))
            flows.append(Flow(key=key, samples=samples.get(key, []), role_hint=role,
                              datatype=datatype, datatype_certain=certain))
        return flows

    @staticmethod
    def _modal_datatype(variant_ids):
        """The declared type for a flow: the most common Variant Type across its samples."""
        if not variant_ids:
            return None, False
        modal = Counter(variant_ids).most_common(1)[0][0]
        return variant_typename(modal)

    def variable_key(self, evt):
        """The flow/variable key an event belongs to (the same keys group_flows produces).

        Used both by group_flows and by the observer to attach a live value to the right variable.
        """
        if evt.op == "PUBLISH_RESPONSE":
            return "opcua:publish:telemetry"      # DEBT D1: single merged telemetry flow (level 4b)
        if evt.op == "READ_RESPONSE":
            return "opcua:read"
        if evt.op == "WRITE_REQUEST" and evt.target is not None:
            return f"opcua:write:{evt.target}"
        return None

    def write_datatype(self, evt):
        """(datatype_name, datatype_certain) declared by a WRITE_REQUEST, from the event alone.

        OPC UA carries the Variant Type on the event (raw["variant_type"]); the same pure mapping
        group_flows uses turns it into a name + certainty. Unmapped / absent -> (None, False) -> "?".
        """
        return variant_typename(evt.raw.get("variant_type"))

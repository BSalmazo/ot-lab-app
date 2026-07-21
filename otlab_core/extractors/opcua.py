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
- Value = the typed field the Variant Type declares (``opcua.variant.has_value``, e.g. 0x0a=Float).
  Read GENERICALLY: the type byte dispatches to the matching field (Float->opcua.Float,
  Double->opcua.Double, every Int/UInt width, Byte/SByte, Boolean->opcua.Boolean, ...), never a
  Float+Int32 hardcode; an unmapped/array type is reported, not guessed (see _VARIANT_COL).
- A subscription PublishResponse (829) batches an array of MonitoredItemNotification, each a
  (ClientHandle, Variant value). tshark emits parallel positional lists under -E occurrence=a --
  opcua.ClientHandle and opcua.variant.has_value zip item-for-item, the value in the type's own
  field -- so the extractor splits ONE flow per handle (opcua:sub:<handle>) instead of fusing every
  monitored node into one serpentining signal. The handle number is the honest key: the
  handle->NodeId->name map lives in CreateMonitoredItems at setup, which may predate the capture.
  Types are MIXED within one message and dispatched PER ITEM. Bench-confirmed (180s full-cycle
  capture): the PLC's Real tags are Float (0x0a -> opcua.Float), its Int tags are Int16
  (0x04 -> opcua.Int16, NOT Int32 -- a live -e opcua.Int32 was empty while the ints changed). One
  observed message: ClientHandle 1,2,6,7,7 / has_value 0x0a,0x04,0x04,0x0a,0x0a. Over a full cycle
  seven handles (the seven tags) appeared; a command only publishes when it changes, so a short
  one-phase capture legitimately sees only the moving signals -- the observe window must cover a
  full process cycle (the same >=1.5-cycle rule the discover logic already applies).
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
    "opcua.ClientHandle",      # 26  per-MonitoredItemNotification handle (subscription publishes)
    "opcua.AttributeId",       # 27  per-WriteValue attribute (13=Value); its count = N write targets
]

_CLIENTHANDLE_COL = 26     # opcua.ClientHandle: the per-item handle list in a PublishResponse
_ATTRIBUTEID_COL = 27      # opcua.AttributeId: one per WriteValue -> count = number of write targets
_VARIANT_TYPE_COL = 13     # opcua.variant.has_value: the per-item Variant Type byte (aligned with above)
_VARIANT_ARRAY_BIT = 0x80  # Variant encoding byte: bit 7 = IsArray; low 6 bits = BuiltInType id
_VARIANT_BUILTIN_MASK = 0x3F


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


def _value_list(cols: List[str], col: int, kind: str) -> list:
    """The positional value list for one variant column, parsed by its FT kind (float/int/bool/str).

    tshark emits one comma-separated list PER field under -E occurrence=a, so opcua.Float holds only
    the Float items, opcua.Int32 only the Int32 items, etc. Callers zip these by a per-column cursor
    to reconstruct each MonitoredItemNotification's value in wire order. String is split on comma too
    (a value containing a comma would split wrong -- acceptable for the numeric process signals this
    targets; noted, not silently wrong)."""
    raw = _col(cols, col)
    if raw is None or str(raw).strip() == "":
        return []
    if kind == "float":
        return _float_list(raw)
    if kind == "int":
        return _int_list(raw)
    if kind == "bool":
        return [_parse_bool(tok) for tok in str(raw).split(",") if tok.strip() != ""]
    if kind == "str":
        return list(str(raw).split(","))
    return _float_list(raw)


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
    wire_layer = "opcua"   # frame.protocols dissector substring (single source of truth; see gate)

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
        # kept (emitted blind), so we gate on the layer, not on payload readability. The layer
        # substring is the declared wire_layer (single source of truth); the transport_type
        # allowance is unchanged.
        if self.wire_layer not in protocols and not transport_type:
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
        # A subscription PublishResponse carries per-item (ClientHandle, Variant, value); split it into
        # per-handle items so each monitored node is its OWN signal (opcua:sub:<handle>) rather than
        # fusing them into one serpentining flow. Empty for reads/writes (no ClientHandle on the wire).
        sub_items, unhandled_variants = ([], [])
        if op == "PUBLISH_RESPONSE":
            sub_items, unhandled_variants = self._parse_sub_items(cols)
        # A WriteRequest carries N (target NodeId, Variant value) write items -- the command path. Split
        # them out (see _parse_write_items) so each written node is its own command flow, and use the
        # first item to fill the event's single target/value/variant (backward-compatible with the
        # earlier single-target parse and with directly-built write events that have no AttributeId).
        write_items = []
        if op == "WRITE_REQUEST":
            write_items, wu = self._parse_write_items(cols)
            if write_items:
                unhandled_variants = unhandled_variants + wu
                target = write_items[0]["target"]
                value = write_items[0]["value"]
                value_is_float = isinstance(value, float)
                variant = write_items[0]["variant_type"] if write_items[0]["variant_type"] is not None else variant

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
                "sub_items": sub_items,           # per-handle [(handle, variant_type, value)] (publishes)
                "write_items": write_items,       # per-target [(target, variant_type, value)] (writes)
                "unhandled_variants": unhandled_variants,  # variant type ids seen but not yet readable
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

    def _parse_sub_items(self, cols: List[str]):
        """Split a subscription PublishResponse into per-MonitoredItemNotification items.

        Each notification carries a ClientHandle and a DataValue whose Variant holds one value. Under
        -E occurrence=a the dissector emits parallel, POSITIONALLY-ALIGNED lists: opcua.ClientHandle
        and opcua.variant.has_value (the per-item Variant Type byte) run item-for-item, exactly like
        Modbus regnum16/regval. The value itself lives in the TYPE's own field (opcua.Float holds only
        the Float items, opcua.Int32 only the Int32 items, ...), so a per-column cursor reconstructs
        each item's value in wire order -- generic across variant types, no Float/Int hardcode.

        Returns (items, unhandled) where each item is {handle, variant_type, value, unhandled}. An
        array variant (encoding bit 7) or a BuiltInType with no mapped field is REPORTED, not silently
        dropped: its item is kept with value=None and unhandled=True, and its type id is collected in
        `unhandled`, so a signal of a type this extractor cannot yet read still surfaces as a handle.
        The handle number is the honest key -- ClientHandle->NodeId->name lives in CreateMonitoredItems
        at setup, which may predate the capture; we never invent a name.
        """
        handles = _int_list(_col(cols, _CLIENTHANDLE_COL))
        if not handles:
            return [], []
        values, unhandled = self._zip_values(cols, _int_list(_col(cols, _VARIANT_TYPE_COL)))
        items = []
        for i, handle in enumerate(handles):
            vt, value, unh = values[i] if i < len(values) else (None, None, True)
            items.append({"handle": handle, "variant_type": vt, "value": value, "unhandled": unh})
        return items, unhandled

    @staticmethod
    def _zip_values(cols: List[str], type_bytes: list):
        """Reconstruct each item's value from the per-Variant-Type byte list, in wire order.

        Shared by the subscription (per ClientHandle) and write (per NodeId) paths: given the parallel
        ``opcua.variant.has_value`` list (one Variant Type byte per item), read each item's value from
        that type's OWN field via a per-column cursor (opcua.Float holds only the Float items, etc.).
        Returns [(variant_type, value, unhandled)] in order. An array variant (encoding bit 7) or a
        BuiltInType with no mapped field is REPORTED (value=None, unhandled=True, id collected), never
        silently dropped -- the same honesty as UNCLAIMED. Generic across variant types, no hardcode.
        """
        out: list = []
        unhandled: list = []
        col_lists: dict = {}   # column index -> parsed value list (parsed once)
        cursors: dict = {}     # column index -> next position within that type's list
        for raw_vt in type_bytes:
            builtin = raw_vt & _VARIANT_BUILTIN_MASK if raw_vt is not None else None
            is_array = raw_vt is not None and bool(raw_vt & _VARIANT_ARRAY_BIT)
            mapping = None if (builtin is None or is_array) else _VARIANT_COL.get(builtin)
            if mapping is None:                          # array, unknown type, or missing type byte
                unhandled.append(raw_vt)
                out.append((raw_vt, None, True))
                continue
            col, kind = mapping
            if col not in col_lists:
                col_lists[col] = _value_list(cols, col, kind)
                cursors[col] = 0
            idx = cursors[col]
            cursors[col] += 1
            vals = col_lists[col]
            out.append((builtin, vals[idx] if idx < len(vals) else None, False))
        return out, unhandled

    @staticmethod
    def _ns_align(numerics: list, nsindexes: list) -> list:
        """A namespace index per opcua.nodeid.numeric position (default ns=0).

        The two lists differ in length: a NodeId in ns=0 (or a TwoByte encoding) carries no explicit
        namespace and emits no nsindex, so the nsindexes tail-align onto the numerics -- the SAME
        rule _select_target already uses (numeric "847832407,0,23", nsindex "0,4" -> the 23 is ns=4).
        A position with no aligned nsindex is ns=0 (a plain-integer NodeId, e.g. the write target 45).
        """
        ns_by_pos = [0] * len(numerics)
        if nsindexes:
            offset = len(numerics) - len(nsindexes)
            for j, ns in enumerate(nsindexes):
                pos = offset + j
                if 0 <= pos < len(numerics):
                    ns_by_pos[pos] = ns
        return ns_by_pos

    def _parse_write_items(self, cols: List[str]):
        """Split a WriteRequest into per-target (node, value) write items.

        There is NO write-target-specific NodeId field in the dissector (verified with tshark -G
        fields); the WriteValue NodeIds share the generic opcua.nodeid.numeric list with the request
        envelope (AuthenticationToken, a TypeId). But every WriteValue emits an ``opcua.AttributeId``
        (13 = Value), so N = count(AttributeId) is the authoritative number of write targets, and the
        targets are the LAST N of nodeid.numeric -- the leading entries are the envelope. That is far
        safer than a hardcoded "skip the first two": the count comes from the structure, not a guess.
        Values dispatch through the shared variant table and pair to targets by position (value None
        for a value-less write -- the command still surfaces). Returns (items, unhandled), each item
        {target, variant_type, value, unhandled}; empty when no AttributeId (caller falls back to the
        single-target _select_target path for directly-built events).
        """
        n = len(_int_list(_col(cols, _ATTRIBUTEID_COL)))
        if n == 0:
            return [], []
        numerics = _int_list(_col(cols, 8))
        targets = numerics[-n:] if len(numerics) >= n else numerics
        base = len(numerics) - len(targets)
        ns_by_pos = self._ns_align(numerics, _int_list(_col(cols, 9)))
        values, unhandled = self._zip_values(cols, _int_list(_col(cols, _VARIANT_TYPE_COL)))
        items = []
        for i, num in enumerate(targets):
            ns = ns_by_pos[base + i] if (base + i) < len(ns_by_pos) else 0
            vt, value, unh = values[i] if i < len(values) else (None, None, True)
            items.append({"target": f"ns={ns};i={num}", "variant_type": vt, "value": value, "unhandled": unh})
        return items, unhandled

    def extract_state_samples(self, evt: NormalizedEvent) -> List[float]:
        """The process-variable sample(s) this event carries, in wire order (oldest -> newest).

        A subscription PublishResponse batches per-item (ClientHandle, value): each handle is its OWN
        signal. With ``config.state_signal_node`` set to a handle key ("opcua:sub:<handle>", or the
        bare handle), ONLY that handle's values are returned -- correct attribution across a
        multi-item subscription. Unset (the single-item default), every readable item's value is a
        sample, in order. A ReadResponse carries one value (or a Float batch) the same way.

        A publish with no per-item breakdown (no ClientHandle on the wire, or a synthetic event)
        falls back to the flat ``opcua.Float`` list as a single fused signal, unchanged. Numeric
        types become floats; a String / unreadable variant contributes no sample. Keep-alive -> [].
        """
        if evt.op == "PUBLISH_RESPONSE":
            items = evt.raw.get("sub_items")
            if items:
                want = self.config.state_signal_node
                out: List[float] = []
                for item in items:
                    key = f"opcua:sub:{item['handle']}"
                    if want is not None and str(want) not in (key, str(item["handle"])):
                        continue
                    num = _as_number(item.get("value"))
                    if num is not None:
                        out.append(num)
                return out
            # legacy / no ClientHandle: the flat Float list is the single fused signal.
            node = self.config.state_signal_node
            if node and evt.target is not None and evt.target != node:
                return []
            return list(evt.raw.get("float_samples") or [])
        if evt.op == "READ_RESPONSE":
            node = self.config.state_signal_node
            if node and evt.target is not None and evt.target != node:
                return []
            return list(evt.raw.get("float_samples") or [])
        return []

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

        - PublishResponse (829) -> ONE flow PER ClientHandle, key "opcua:sub:<handle>",
          role_hint="telemetry" (each a state candidate). The core discovers which handle is the
          state signal, exactly as it does for s7:db1:0 / modbus:hr:2 -- no domain knowledge here.
          A handle whose variant type this extractor cannot read yet still yields its flow (reported,
          not dropped), with its unknown type id shown. A publish with no per-item breakdown (no
          ClientHandle, or a synthetic event) falls back to the single fused "opcua:publish:telemetry".
        - WriteRequest (673) -> ONE command flow PER written NodeId, key "opcua:write:ns=<n>;i=<id>",
          role_hint="command", value dispatched through the variant table. N targets = the last N of
          opcua.nodeid.numeric (N = count of AttributeId), so the request envelope is not mistaken for
          a target; multi-node writes yield N flows. A directly-built event (no AttributeId) falls back
          to the single target on evt.target.
        - ReadResponse (634) -> a "read" flow, role_hint="read" (metadata).
        """
        from ..engine.discover import Flow  # local import: keep extractor import lightweight

        # Per flow: (t, value) samples plus the variant type ids seen, so each flow's declared
        # data type (the modal Variant Type) travels with it.
        samples: dict = {}    # key -> list[(t, value)]
        roles: dict = {}      # key -> role_hint
        variants: dict = {}   # key -> list[variant_type id]
        servers: dict = {}    # key -> observed server endpoint "ip:port" (from the traffic)
        for evt in events:
            if evt.op == "PUBLISH_RESPONSE":
                # One flow per ClientHandle. Each item is read exactly ONCE (positional), so the two
                # notifications a subscription may batch for one handle are two genuine samples, kept
                # both -- never one value counted twice.
                items = evt.raw.get("sub_items")
                if items:
                    for item in items:
                        key = f"opcua:sub:{item['handle']}"
                        roles.setdefault(key, "telemetry")
                        if evt.server:
                            servers.setdefault(key, evt.server)
                        vt = item.get("variant_type")
                        if vt is not None:               # keep the type even for an unreadable item,
                            variants.setdefault(key, []).append(vt)   # so its flow reports its type id
                        num = _as_number(item.get("value"))
                        if num is not None:
                            samples.setdefault(key, []).append((evt.timestamp, num))
                else:
                    key = "opcua:publish:telemetry"      # legacy / no ClientHandle: single fused flow
                    roles.setdefault(key, "telemetry")
                    if evt.server:
                        servers.setdefault(key, evt.server)
                    for s in self.extract_state_samples(evt):
                        samples.setdefault(key, []).append((evt.timestamp, float(s)))
                    vt = evt.raw.get("variant_type")
                    if vt is not None:
                        variants.setdefault(key, []).append(vt)
                continue
            if evt.op == "WRITE_REQUEST":
                # One COMMAND flow per written NodeId. The command flow exists as soon as its target
                # is seen (the same target-based gate the evaluator uses), so it ALWAYS yields a
                # VARIABLES row even when the value did not parse (unknown type / encrypted); a numeric
                # value, when present, is a sample. write_items is the split WriteRequest; a directly-
                # built event (no AttributeId) falls back to the single target on evt.target.
                witems = evt.raw.get("write_items")
                if witems:
                    for item in witems:
                        wkey = f"opcua:write:{item['target']}"
                        roles.setdefault(wkey, "command")
                        if evt.server:
                            servers.setdefault(wkey, evt.server)
                        vt = item.get("variant_type")
                        if vt is not None:
                            variants.setdefault(wkey, []).append(vt)
                        num = _as_number(item.get("value"))
                        if num is not None:
                            samples.setdefault(wkey, []).append((evt.timestamp, num))
                    continue
                key = self.variable_key(evt)
                if key is None:
                    continue
                roles.setdefault(key, "command")
                if evt.server:
                    servers.setdefault(key, evt.server)
                num = _as_number(evt.value)
                if num is not None:
                    samples.setdefault(key, []).append((evt.timestamp, num))
                vt = evt.raw.get("variant_type")
                if vt is not None:
                    variants.setdefault(key, []).append(vt)
                continue
            key = self.variable_key(evt)
            if key is None:
                continue
            if evt.op == "READ_RESPONSE":
                num = _as_number(evt.value)
                if num is None:
                    continue
                roles.setdefault(key, "read")
                if evt.server:
                    servers.setdefault(key, evt.server)
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
            # Attach the observed server endpoint (ip:port) from the traffic, as Modbus/S7 do, so
            # flows are endpoint-keyed and the UI shows the observed OPC UA endpoint (not a config port).
            flows.append(Flow(key=key, samples=samples.get(key, []), role_hint=role,
                              datatype=datatype, datatype_certain=certain, server=servers.get(key)))
        return flows

    @staticmethod
    def _modal_datatype(variant_ids):
        """The declared type for a flow: the most common Variant Type across its samples. A variant
        this extractor does not map yet is surfaced by its raw id ("variant:0x2d", uncertain) instead
        of a bare "?", so an unreadable type is reported specifically -- the same honesty as UNCLAIMED.
        """
        ids = [v for v in variant_ids if v is not None]
        if not ids:
            return None, False
        modal = Counter(ids).most_common(1)[0][0]
        name, certain = variant_typename(modal)
        if name is None:
            return f"variant:0x{int(modal) & 0xFF:02x}", False
        return name, certain

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

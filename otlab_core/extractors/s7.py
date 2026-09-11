"""S7comm (ISO-on-TCP, TCP port 102) passive extractor.

Mirrors the Modbus extractor's structure (group_flows / variable_key / extract_state_samples /
a _pair_* helper / bounds-safe column access). S7comm differs from Modbus in one structural way
that shapes this file: a read response (Ack_Data) carries NO address, and the Wireshark dissector
does not inject one (unlike Modbus regnum16). So ``parse_line`` is STATEFUL:

  * a Read Job (ROSCTR=1, func=0x04) records its ordered request items keyed by
    ``s7comm.header.pduref``;
  * the matching Ack_Data (ROSCTR=3, func=0x04) zips those remembered items onto the ordered
    values in ``s7comm.resp.data``;
  * a Write Job (ROSCTR=1, func=0x05) carries items AND values in the same frame and is emitted
    directly.

Facts encoded here (field names, that the values live in ``s7comm.resp.data`` on both the write
Job and the read Ack_Data, that the REQUEST-side ``transp_size`` is the authoritative datatype,
big-endian values, the area codes, and that no dedup is needed because tshark marks the mirror's
second copy as a TCP retransmission and does not re-dissect it) were validated against real S7-300
pcaps and live bench traffic on tshark 4.4.15. They are taken as given, not re-derived.
"""

from __future__ import annotations

import re
import struct
import time
from collections import OrderedDict
from typing import List, Optional, Tuple

from ..contract import NormalizedEvent
from .base import ProtocolExtractor

# tshark -e field list, in the exact order parse_line indexes it.
_FIELDS = [
    "frame.time_epoch",                 # 0
    "frame.protocols",                  # 1
    "ip.src",                           # 2
    "tcp.srcport",                      # 3
    "ip.dst",                           # 4
    "tcp.dstport",                      # 5
    "s7comm.header.rosctr",             # 6   1=Job 2=Ack 3=Ack_Data 7=Userdata
    "s7comm.header.pduref",             # 7   16-bit request/response correlator (wraps)
    "s7comm.param.func",                # 8   0x04=Read Var 0x05=Write Var 0xf0=Setup Comm
    "s7comm.param.itemcount",           # 9
    "s7comm.param.item.area",           # 10  0x84=DB 0x83=M 0x81=I 0x82=Q 0x1d=Timer 0x1c=Counter
    "s7comm.param.item.db",             # 11
    "s7comm.param.item.address.byte",   # 12
    "s7comm.param.item.address.bit",    # 13
    "s7comm.param.item.transp_size",    # 14  REQUEST-side WordLen -> the authoritative datatype
    "s7comm.param.item.length",         # 15
    "s7comm.data.returncode",           # 16
    "s7comm.data.transportsize",        # 17  response-side code table (UNRELIABLE; NOT used)
    "s7comm.data.length",               # 18
    "s7comm.resp.data",                 # 19  value bytes, one occurrence per data item
    "data.data",                        # 20  raw payload; used only by security_mode
]

# ISO-on-TCP (RFC 1006) service port -- S7 binds it; the capture filter is "tcp port 102". S7 may
# name its own port (this is the extractor's protocol knowledge); the observer never sees it.
ISO_ON_TCP_PORT = 102

# ROSCTR (message role).
ROSCTR_JOB = 1
ROSCTR_ACK = 2
ROSCTR_ACK_DATA = 3
ROSCTR_USERDATA = 7

# param.func.
FUNC_READ = 0x04
FUNC_WRITE = 0x05
FUNC_SETUP = 0xF0

# Memory areas (s7comm.param.item.area).
AREA_DB = 0x84       # data block
AREA_M = 0x83        # flags / merker
AREA_I = 0x81        # inputs
AREA_Q = 0x82        # outputs
AREA_TIMER = 0x1D
AREA_COUNTER = 0x1C

# REQUEST-side WordLen (s7comm.param.item.transp_size) -> (datatype name, certain). The RESPONSE
# side (s7comm.data.transportsize) uses a different, unreliable code table and is deliberately not
# consulted. BYTE (0x02) is raw-byte transport: the client asked for opaque bytes, so the wire
# declares no meaningful type -- surface it as uncertain (datatype None, datatype_certain False,
# which the UI renders as "?"), exactly as the Modbus extractor does for its typeless registers.
_WORDLEN = {
    0x01: ("Bool", True),      # BIT
    0x02: (None, False),       # BYTE  -> uncertain (see note above)
    0x04: ("Word", True),
    0x05: ("Int", True),
    0x06: ("DWord", True),
    0x07: ("DInt", True),
    0x08: ("Real", True),
    0x1C: ("Counter", True),   # 28
    0x1D: ("Timer", True),     # 29
}

# Pending-map bounds (see _remember for the chosen eviction policy).
_PENDING_MAX = 1024
_PENDING_TTL_S = 30.0


def _col(cols: List[str], idx: int) -> str:
    """Bounds-safe column access: '' when the line is shorter than the field layout."""
    return cols[idx] if idx < len(cols) else ""


def _to_int(value, default=None):
    if value is None:
        return default
    raw = str(value).strip()
    if raw == "":
        return default
    try:
        return int(raw, 0)   # base 0 handles "0x84" too
    except Exception:
        return default


def _first_int(value):
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    return _to_int(raw.split(",")[0].strip())


def _int_list(value):
    if value is None:
        return []
    raw = str(value).strip()
    if not raw:
        return []
    out = []
    for token in raw.split(","):
        v = _to_int(token.strip())
        if v is not None:
            out.append(v)
    return out


def _occurrence_list(value):
    """Ordered raw string tokens of a multi-occurrence field (one per data item)."""
    if value is None:
        return []
    raw = str(value).strip()
    if not raw:
        return []
    return [token.strip() for token in raw.split(",")]


def _hex_bytes(token) -> bytes:
    """Bytes from a tshark FT_BYTES token like '43:96:00:00' or '43960000'."""
    if token is None:
        return b""
    digits = re.sub(r"[^0-9a-fA-F]", "", str(token))
    if len(digits) % 2:
        digits = "0" + digits
    try:
        return bytes.fromhex(digits)
    except Exception:
        return b""


def _as_number(value):
    """Coerce a decoded S7 value to float for a sample series, or None."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _flow_key(area, db, byte) -> Optional[str]:
    """The flow-key string for one addressed item, or None for an unmapped area / missing byte."""
    if byte is None:
        return None
    if area == AREA_DB:
        return f"s7:db{db if db is not None else 0}:{byte}"
    if area == AREA_M:
        return f"s7:m:{byte}"
    if area == AREA_I:
        return f"s7:i:{byte}"
    if area == AREA_Q:
        return f"s7:q:{byte}"
    if area == AREA_TIMER:
        return f"s7:timer:{byte}"
    if area == AREA_COUNTER:
        return f"s7:counter:{byte}"
    return None


def _datatype(wordlen) -> Tuple[Optional[str], bool]:
    return _WORDLEN.get(wordlen, (None, False))


def _decode_value(token, wordlen):
    """Decode one s7comm.resp.data item to a scalar, BIG-ENDIAN, per its REQUEST-side WordLen.

    BYTE/unmapped fall back to a plain big-endian integer over the bytes present, so a value is
    still recovered even where the type is uncertain (as Modbus does for its typeless registers).
    """
    b = _hex_bytes(token)
    if not b:
        return None
    if wordlen == 0x01:                                  # BIT
        return b[0] & 0x01
    if wordlen == 0x08:                                  # REAL (IEEE-754, 4 bytes)
        return struct.unpack(">f", b[:4])[0] if len(b) >= 4 else None
    if wordlen == 0x05:                                  # INT (signed 16)
        return int.from_bytes(b[:2], "big", signed=True)
    if wordlen == 0x07:                                  # DINT (signed 32)
        return int.from_bytes(b[:4], "big", signed=True)
    if wordlen == 0x04:                                  # WORD (unsigned 16)
        return int.from_bytes(b[:2], "big")
    if wordlen == 0x06:                                  # DWORD (unsigned 32)
        return int.from_bytes(b[:4], "big")
    if wordlen in (0x1C, 0x1D):                          # COUNTER / TIMER
        # APPROXIMATION, not a faithful decode: real S7 counters and timers are BCD-encoded (the
        # counter value, and the timer's base + value, are packed as binary-coded decimal). Here
        # they are read as a plain big-endian uint16 magnitude, which is adequate as a value series
        # for phase inference but is NOT the true engineering value. Left as a known limitation.
        return int.from_bytes(b[:2], "big")
    return int.from_bytes(b, "big")                      # BYTE (0x02) and anything unmapped


class S7CommExtractor(ProtocolExtractor):
    name = "S7COMM"
    wire_layer = "s7comm"   # frame.protocols dissector substring (single source of truth; see gate)

    def __init__(self, config=None):
        # No dedicated S7 config section exists yet; accept whatever the runtime passes (may be
        # None) and read optional knobs defensively via getattr.
        self.config = config
        # (conversation, pduref) -> (ordered request items, timestamp). Instance state, because S7
        # read pairing spans two frames (Job -> Ack_Data). pduref is a 16-bit correlator scoped to
        # ONE TCP connection, not globally unique, so the conversation MUST be part of the key: two
        # connections sharing a pduref (two clients to one PLC, or one capture over two PLCs) would
        # otherwise cross-pair -- an Ack_Data popping the other conversation's remembered items and
        # emitting a correct-looking value against the wrong variable. Bounded; see _remember.
        self._pending: "OrderedDict[Tuple[frozenset, int], Tuple[list, float]]" = OrderedDict()

    def occurrence(self) -> str:
        # Item lists and resp.data are legitimately multi-valued (up to 19 items per request
        # observed), so request ALL occurrences, exactly like the Modbus regval_uint16 list.
        return "a"

    def tshark_fields(self) -> List[str]:
        return list(_FIELDS)

    def capture_filter(self) -> str:
        return f"tcp port {ISO_ON_TCP_PORT}"

    def security_mode(self, cols: List[str]) -> str:
        # Frames the s7comm dissector parsed are cleartext by definition.
        if "s7comm" in str(_col(cols, 1)).lower():
            return "clear"
        # Otherwise sniff the first payload byte: 0x32 = S7comm header (clear), 0x72 = S7CommPlus
        # (payload-encrypted by design; no stock dissector exists, so detectable but not readable),
        # 0x16/0x17 = TLS record. Anything else / no payload -> treat as clear.
        b = _hex_bytes(_col(cols, 20))
        if not b:
            return "clear"
        first = b[0]
        if first == 0x32:
            return "clear"
        if first == 0x72:
            return "encrypted"
        if first in (0x16, 0x17):
            return "encrypted"
        return "clear"

    def opaque_endpoint(self, cols: List[str]) -> Optional[Tuple[str, str]]:
        """An ISO-on-TCP endpoint carrying a payload S7 cannot read -> (endpoint, reason), else None.

        Called by the observer only on a "tcp port 102" frame that parse_line declined. S7 can tell
        precisely, from fields already captured:
          - the frame must show the ISO-on-TCP transport (tpkt/cotp in frame.protocols) with NO
            s7comm application layer -- a readable s7comm frame is parse_line's job, not this;
          - the first byte of data.data (col 20) says what the opaque payload is: 0x72 is S7CommPlus
            (its payload is encrypted by design, no stock dissector reads it), 0x16/0x17 is a TLS
            record.
        Anything else -> None: a bare ACK / handshake carries no data.data (transport plumbing);
        a 0x32 first byte would be readable s7comm; an unrecognised byte is not something S7 can
        claim is unreadable. Returning None when unsure is deliberate.

        The reason states what was seen (the transport shape and the first byte), not an
        interpretation beyond it. The endpoint is the ISO-on-TCP server -- the side bound to port
        102, which S7 knows is its own service port.
        """
        protocols = str(_col(cols, 1)).lower()
        if self.wire_layer in protocols:
            return None                      # readable s7comm -> not opaque; parse_line handles it
        if "cotp" not in protocols and "tpkt" not in protocols:
            return None                      # not the ISO-on-TCP transport shape (bare TCP / other)
        b = _hex_bytes(_col(cols, 20))       # data.data
        if not b:
            return None                      # no payload -> transport plumbing (ACK / handshake)
        first = b[0]
        if first == 0x72:
            reason = "ISO-on-TCP (tpkt/cotp), S7CommPlus payload (data.data first byte 0x72)"
        elif first in (0x16, 0x17):
            reason = f"ISO-on-TCP (tpkt/cotp), TLS record (data.data first byte 0x{first:02x})"
        else:
            return None                      # 0x32 (readable) or unrecognised -> S7 cannot claim opaque
        endpoint = self._iso_server_endpoint(cols)
        return (endpoint, reason) if endpoint else None

    @staticmethod
    def _iso_server_endpoint(cols) -> Optional[str]:
        """The ip:port of the ISO-on-TCP server for this frame: the endpoint bound to port 102."""
        src_ip, src_port = _col(cols, 2), _to_int(_col(cols, 3))
        dst_ip, dst_port = _col(cols, 4), _to_int(_col(cols, 5))
        if src_port == ISO_ON_TCP_PORT and src_ip:
            return f"{src_ip}:{src_port}"
        if dst_port == ISO_ON_TCP_PORT and dst_ip:
            return f"{dst_ip}:{dst_port}"
        return None

    # -- pending map (read Job -> Ack_Data pairing) ---------------------------------------------

    @staticmethod
    def _conversation(cols):
        """Direction-independent key for the TCP conversation a frame belongs to.

        pduref (col 7) is a 16-bit correlator scoped to ONE TCP connection, so its values recur
        across connections; the pending map is therefore scoped per conversation. A Read Job travels
        client->server and its Ack_Data server->client, so the key MUST be identical under a src/dst
        swap -- an UNORDERED pair (frozenset) of the two ip:port endpoints achieves that, from the
        endpoint fields already in _FIELDS (ip.src/tcp.srcport at 2/3, ip.dst/tcp.dstport at 4/5).
        """
        src = f"{_col(cols, 2)}:{_col(cols, 3)}"
        dst = f"{_col(cols, 4)}:{_col(cols, 5)}"
        return frozenset((src, dst))

    def _remember(self, conv, pduref, items, ts):
        """Store a Read Job's ordered items under (conversation, pduref), bounded in age AND size.

        EVICTION POLICY (chosen): an insertion-ordered map keyed by (conversation, pduref). On each
        insert we (1) drop any entry older than _PENDING_TTL_S seconds -- a Read whose Ack was lost
        or never mirrored -- then (2) cap the map at _PENDING_MAX entries, dropping the oldest first.
        pduref is 16-bit and wraps, but a request/response completes in milliseconds, so the map is
        normally near-empty and this only bounds the pathological case of unanswered Reads. A
        repeated (conversation, pduref) refreshes its slot (the newer Job supersedes the stale one).

        The composite key raises the theoretical ceiling from one entry per pduref to one per
        (conversation, pduref), but the same bound still applies unchanged: _PENDING_MAX = 1024
        simultaneously-outstanding unanswered Reads across ALL conversations is already far beyond
        any real concurrent load, and the per-insert TTL sweep is key-agnostic. Left as is.
        """
        key = (conv, pduref)
        if key in self._pending:
            del self._pending[key]
        cutoff = ts - _PENDING_TTL_S
        for k in [k for k, (_items, t) in self._pending.items() if t < cutoff]:
            del self._pending[k]
        self._pending[key] = (items, ts)
        while len(self._pending) > _PENDING_MAX:
            self._pending.popitem(last=False)

    # -- parsing --------------------------------------------------------------------------------

    def parse_line(self, cols: List[str]) -> Optional[NormalizedEvent]:
        if len(cols) < 9:
            return None
        # Only frames the s7comm dissector identified. Encrypted (S7CommPlus / TLS) frames carry no
        # readable payload and are dropped here; security_mode still classifies them for callers.
        # Gate on the declared wire_layer so it can never drift from the probe's reverse lookup.
        if self.wire_layer not in str(_col(cols, 1)).lower():
            return None

        rosctr = _first_int(_col(cols, 6))
        func = _first_int(_col(cols, 8))
        src_ip = _col(cols, 2) or None
        dst_ip = _col(cols, 4) or None
        if not src_ip or not dst_ip or rosctr is None:
            return None

        ts = float(_col(cols, 0)) if _col(cols, 0) else time.time()
        pduref = _first_int(_col(cols, 7))
        conv = self._conversation(cols)   # scopes pduref to its TCP connection (direction-independent)

        # Read Job: remember the ordered request items; no event yet (values arrive on the Ack).
        if rosctr == ROSCTR_JOB and func == FUNC_READ:
            self._remember(conv, pduref, self._request_items(cols), ts)
            return None

        # Write Job: the items AND their values are both in this frame -> emit a WRITE_REQUEST.
        if rosctr == ROSCTR_JOB and func == FUNC_WRITE:
            items = self._pair_write(self._request_items(cols), _occurrence_list(_col(cols, 19)))
            return self._make_event(cols, ts, pduref, "WRITE_REQUEST", items)

        # Read Ack_Data: align the remembered request items onto this frame's values by walking the
        # per-item return codes (col 16). A malformed / unalignable response is dropped, not guessed.
        if rosctr == ROSCTR_ACK_DATA and func == FUNC_READ:
            pending = self._pending.pop((conv, pduref), None)
            if pending is None:
                return None   # no matching Job (lost / evicted): the values cannot be addressed
            req_items, _ts = pending
            items = self._pair_response(req_items, _int_list(_col(cols, 16)),
                                        _occurrence_list(_col(cols, 19)))
            if items is None:
                return None   # return-code walk found the frame malformed / unalignable -> drop
            return self._make_event(cols, ts, pduref, "READ_RESPONSE", items)

        # Setup Communication (0xf0), bare Acks (write ack), Userdata: nothing to surface.
        return None

    def _request_items(self, cols):
        """[(flow_key, wordlen, datatype, datatype_certain), ...] from the param.item.* lists."""
        areas = _int_list(_col(cols, 10))
        dbs = _int_list(_col(cols, 11))
        bytes_ = _int_list(_col(cols, 12))
        tsizes = _int_list(_col(cols, 14))
        n = max(len(areas), len(bytes_), len(tsizes))
        items = []
        for i in range(n):
            area = areas[i] if i < len(areas) else None
            db = dbs[i] if i < len(dbs) else None
            byte = bytes_[i] if i < len(bytes_) else None
            wl = tsizes[i] if i < len(tsizes) else None
            dtype, certain = _datatype(wl)
            items.append((_flow_key(area, db, byte), wl, dtype, certain))
        return items

    @staticmethod
    def _pair_write(req_items, raw_values):
        """Write Job pairing: item i's value sits at position i of the SAME frame. Every item
        carries a value (there is no per-item success/failure yet -- that is on the write Ack, which
        this extractor does not emit), so a plain positional zip is correct here. Returns
        [(flow_key, value, datatype, datatype_certain), ...]; a missing token -> value None.
        """
        items = []
        for i, (key, wl, dtype, certain) in enumerate(req_items):
            token = raw_values[i] if i < len(raw_values) else None
            value = _decode_value(token, wl) if token is not None else None
            items.append((key, value, dtype, certain))
        return items

    @staticmethod
    def _pair_response(req_items, returncodes, raw_values):
        """Read Ack_Data pairing: align remembered request items to response values, consulting the
        RETURN CODES (one per requested item -- measured invariant on the real S7-300 capture: 93
        Read Var responses, itemcount == count(returncode) == count(resp.data), 0 mismatches).

        A failed item reports a non-Success (!= 0xff) return code. What such an item does to
        s7comm.resp.data is UNKNOWN and could not be tested (every available capture is all-success):

          Model A -- a failed item emits NO data token. Then M (token count) == number of Successes,
                     so a failure makes M < N; and M == N implies every item succeeded.
          Model B -- a failed item emits a zero-length data token. Then every item emits a token, so
                     M == N always, and a failure is just a non-0xff code at that item's position.

        The rule below is correct under BOTH models, so the open question need not be resolved, and
        it never discards a whole response over one failed item (which -- under Model B, where
        M == N with failures is the NORMAL case -- would blind the extractor permanently on a single
        misconfigured address):

          M == N -> positional zip: item i takes token i, but value = None for any item whose code
                    is not 0xff. Under Model A, M == N means all succeeded, so positional is right.
                    Under Model B, M == N is normal and token i belongs to item i, so positional is
                    right and the failed item's zero-length token is correctly nulled. Either way no
                    data is lost and nothing is mis-assigned.
          M <  N -> only reachable under Model A (failures dropped their tokens): walk the codes and
                    consume a token ONLY on Success; failed items yield None. This realigns.
          M >  N -> impossible under either model given one code per item: genuinely malformed, drop.

        Returns [(flow_key, value, datatype, datatype_certain), ...], or None (drop the frame) when
        malformed: return-code count != request-item count, or more value tokens than items (M > N),
        or an M < N walk that runs out of / leaves over tokens.
        """
        n = len(req_items)
        if len(returncodes) != n:
            return None                       # one code per requested item is the measured invariant
        m = len(raw_values)
        if m > n:
            return None                       # more value tokens than items -> genuinely malformed

        items = []
        if m == n:
            # Positional: token i belongs to item i (correct under both models); a non-Success item
            # takes no value regardless of what its token holds.
            for i, (key, wl, dtype, certain) in enumerate(req_items):
                value = _decode_value(raw_values[i], wl) if returncodes[i] == 0xFF else None
                items.append((key, value, dtype, certain))
            return items

        # M < N (Model A): a failed item dropped its token, so realign by consuming a token only for
        # Success items.
        ti = 0
        for (key, wl, dtype, certain), rc in zip(req_items, returncodes):
            if rc == 0xFF:
                if ti >= m:
                    return None               # a Success item with no token left -> malformed
                value = _decode_value(raw_values[ti], wl)
                ti += 1
            else:
                value = None                  # failed item: no data, no sample
            items.append((key, value, dtype, certain))
        if ti != m:
            return None                       # tokens left unconsumed in the M<N walk -> malformed
        return items

    def _make_event(self, cols, ts, pduref, op, items) -> NormalizedEvent:
        src_ip = _col(cols, 2) or None
        src_port = _to_int(_col(cols, 3))
        dst_ip = _col(cols, 4) or None
        dst_port = _to_int(_col(cols, 5))
        func = _first_int(_col(cols, 8))
        itemcount = _first_int(_col(cols, 9))
        is_req = op.endswith("REQUEST")
        client_ep = f"{src_ip}:{src_port}" if is_req else f"{dst_ip}:{dst_port}"
        server_ep = f"{dst_ip}:{dst_port}" if is_req else f"{src_ip}:{src_port}"

        # The first item drives the single-value observer path (target/value). As in the Modbus
        # write-target convention, target IS the flow-key string, not a raw address. Every item is
        # kept in raw["items"]; group_flows fans them out. A multi-item WRITE is judged only on its
        # first item by the verdict path -- a documented limitation, as with Modbus single-target
        # writes; the bench writes single-item commands and reads multi-item polls.
        first_key = items[0][0] if items else None
        first_val = items[0][1] if items else None

        evt = NormalizedEvent(
            timestamp=ts,
            protocol="S7COMM",
            op=op,
            direction="request" if is_req else "response",
            src_ip=src_ip,
            src_port=src_port,
            dst_ip=dst_ip,
            dst_port=dst_port,
            client=client_ep,
            server=server_ep,
            target=first_key,
            value=first_val,
            quantity=itemcount,
            summary=self._summary(op, src_ip, src_port, dst_ip, dst_port, func, items),
            security_mode="clear",
            raw={
                "rosctr": _first_int(_col(cols, 6)),
                "pduref": pduref,
                "func": func,
                # [(flow_key, value, datatype, datatype_certain), ...] -- the per-item list the
                # flow / state path fans out over (S7's analogue of Modbus raw["reg_values"]).
                "items": items,
            },
        )
        samples = self.extract_state_samples(evt)
        evt.state_signal_value = samples[-1] if samples else None
        return evt

    @staticmethod
    def _summary(op, src_ip, src_port, dst_ip, dst_port, func, items):
        verb = "write" if op.startswith("WRITE") else "read"
        keys = ",".join(str(k) for (k, *_rest) in items if k) or "-"
        return (f"S7 {verb} func=0x{(func or 0):02x} from {src_ip}:{src_port} "
                f"to {dst_ip}:{dst_port} | items={keys}")

    # -- discovery surface (mirrors Modbus) -----------------------------------------------------

    def extract_state_samples(self, evt: NormalizedEvent, state_key: Optional[str] = None) -> List[float]:
        """Read-response values as the process-variable series (all items, in wire order); else [].

        ``state_key`` is accepted for the observer's uniform live routing but NOT honoured here: S7 is
        a polled request/response transport (read responses attribute via the config key already), so
        there is no multi-item publish to route and no per-poll silence. Passing it changes nothing.
        """
        if evt.op != "READ_RESPONSE":
            return []
        want = getattr(self.config, "state_signal_key", None) if self.config else None
        out: List[float] = []
        for key, value, _dtype, _certain in evt.raw.get("items") or []:
            num = _as_number(value)
            if num is None:
                continue
            if want is not None and key != want:
                continue
            out.append(float(num))
        return out

    def variable_key(self, evt: NormalizedEvent) -> Optional[str]:
        """The flow key an event's (first) item belongs to; used to attach a write's live value."""
        items = evt.raw.get("items") or []
        if items and evt.op in ("READ_RESPONSE", "WRITE_REQUEST"):
            return items[0][0]
        return None

    def write_datatype(self, evt: NormalizedEvent):
        """(datatype_name, datatype_certain) declared by a WRITE_REQUEST, from the event alone.

        Each item's request-side WordLen type sits in raw["items"] as (key, value, datatype,
        certain). Reports the FIRST item's type, consistent with variable_key (multi-item writes
        surface only the first item -- the existing limitation, not fixed here). Absent -> "?".
        """
        items = evt.raw.get("items") or []
        if items:
            return items[0][2], items[0][3]
        return (None, False)

    def group_flows(self, events) -> list:
        """One flow per S7 variable (address). Mirrors the Modbus extractor.

        - Read responses (Ack_Data) -> a telemetry flow per item address (a STATE candidate).
        - Write jobs -> a COMMAND flow per item address.

        datatype comes from the REQUEST-side WordLen and is certain, except raw BYTE transport,
        which is surfaced as uncertain ("?"). Single-role-per-address (first-seen role wins), so a
        polled address and a commanded address never mix their series, exactly as Modbus does.
        """
        from ..engine.discover import Flow  # local import: keep extractor import lightweight

        samples: dict = {}     # key -> list[(t, value)]
        roles: dict = {}       # key -> role_hint
        servers: dict = {}     # key -> observed server endpoint "ip:port"
        dtypes: dict = {}      # key -> (datatype, datatype_certain)
        for evt in events:
            if evt.op == "READ_RESPONSE":
                role = "telemetry"
            elif evt.op == "WRITE_REQUEST":
                role = "command"
            else:
                continue
            for key, value, dtype, certain in evt.raw.get("items") or []:
                if key is None:
                    continue
                if roles.setdefault(key, role) != role:
                    continue   # already the other role; do not mix read and write series
                if evt.server:
                    servers.setdefault(key, evt.server)
                dtypes.setdefault(key, (dtype, certain))
                num = _as_number(value)
                if num is not None:
                    samples.setdefault(key, []).append((evt.timestamp, num))

        flows: list = []
        for key, role in roles.items():
            dtype, certain = dtypes.get(key, (None, False))
            flows.append(Flow(key=key, samples=samples.get(key, []), role_hint=role,
                              datatype=dtype, datatype_certain=certain, server=servers.get(key)))
        return flows

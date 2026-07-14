"""Modbus/TCP passive extractor (Phase 1B).

This is a faithful refactor of the parsing that lived in ``scripts/tshark_runtime.py`` on the
``v2-dev`` baseline. The field list, the drop conditions, the register/value/quantity
derivation, and the summary string are reproduced exactly, so the event this produces
serialises (via ``NormalizedEvent.to_wire_dict``) to byte-identical JSON.

The runtime no longer hardcodes ``"modbus" not in protocols`` — that protocol-recognition check
now lives here, where it belongs, inside the Modbus extractor.
"""

from __future__ import annotations

import re
import time
from typing import List, Optional

from ..config import ModbusConfig
from ..contract import NormalizedEvent
from .base import ProtocolExtractor

MODBUS_DEFAULT_PORTS = {502, 5020, 15020}
WRITE_FUNCTIONS = {5, 6, 15, 16}

_FIELDS = [
    "frame.time_epoch",              # 0
    "frame.protocols",               # 1
    "ip.src",                        # 2
    "tcp.srcport",                   # 3
    "ip.dst",                        # 4
    "tcp.dstport",                   # 5
    "mbtcp.trans_id",                # 6
    "mbtcp.unit_id",                 # 7
    "modbus.func_code",              # 8
    "modbus.reference_num",          # 9   write start address
    "modbus.read_reference_num",     # 10  read request start address (EMPTY on responses in
    #                                       tshark 4.2.2 / 4.4.15 -- responses use regnum16)
    "modbus.word_cnt",               # 11
    "modbus.bit_cnt",                # 12
    "modbus.regval_uint16",          # 13  register value(s), multi-valued on multi-register reads
    "modbus.bitval",                 # 14  coil value (FC5)
    "modbus.exception_code",         # 15
    # --- appended so indices 0-15 above never shift (verified on the bench pcap, tshark 4.4.15) ---
    "modbus.regnum16",               # 16  register number(s) the dissector pairs onto a response
    "modbus.data",                   # 17  raw payload bytes; hex value fallback (tshark 4.2.2 FC6)
]


def _to_int(value, default=None):
    if value is None:
        return default
    raw = str(value).strip()
    if raw == "":
        return default
    try:
        return int(raw, 0)
    except Exception:
        return default


def _parse_first_int(value):
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    # tshark fields may contain comma-separated entries.
    token = raw.split(",")[0].strip()
    return _to_int(token)


def _parse_int_list(value):
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


def _col(cols: List[str], idx: int) -> str:
    """Safe column access: '' when the line is shorter than expected (older field layout)."""
    return cols[idx] if idx < len(cols) else ""


def _parse_hex_value(value):
    """Big-endian integer from a modbus.data hex string (e.g. '0064' or '00:64' -> 100).

    Fallback for tshark 4.2.2, where an FC6 write value appears only as raw payload bytes. Takes
    the first occurrence; separators are ignored. Returns None if there are no hex digits.
    """
    if value is None:
        return None
    token = str(value).split(",")[0]
    digits = re.sub(r"[^0-9a-fA-F]", "", token)
    if not digits:
        return None
    try:
        return int(digits, 16)
    except Exception:
        return None


def _as_number(value):
    """Coerce a Modbus value (uint16 int / None) to float for a sample series, or None."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _event_type_for(func_code, dst_port):
    if func_code is None:
        return "UNKNOWN_REQUEST"
    base_fc = func_code & 0x7F if func_code > 127 else func_code
    if dst_port in MODBUS_DEFAULT_PORTS:
        return "WRITE_REQUEST" if base_fc in WRITE_FUNCTIONS else "READ_REQUEST"
    return "WRITE_RESPONSE" if base_fc in WRITE_FUNCTIONS else "READ_RESPONSE"


def _build_summary(function_code, src_ip, src_port, dst_ip, dst_port, event_type, register, value, quantity):
    fc = function_code
    src = f"{src_ip}:{src_port}"
    dst = f"{dst_ip}:{dst_port}"
    et = str(event_type or "UNKNOWN")
    if et.startswith("WRITE"):
        if register is not None:
            return f"FC{fc} write from {src} to {dst} | register={register} value={value}"
        return f"FC{fc} write from {src} to {dst}"
    # NOTE: legacy behaviour uses start_addr (== register) and quantity for reads.
    return f"FC{fc} read from {src} to {dst} | start={register} qty={quantity}"


class ModbusExtractor(ProtocolExtractor):
    name = "MODBUS/TCP"

    def __init__(self, config: Optional[ModbusConfig] = None):
        self.config = config or ModbusConfig()

    def occurrence(self) -> str:
        # Register values (and their numbers) are legitimately multi-valued on a multi-register
        # FC3 response, so request ALL occurrences. Single-valued fields still parse via their
        # first token, exactly as the OPC UA extractor does.
        return "a"

    def tshark_fields(self) -> List[str]:
        return list(_FIELDS)

    def capture_filter(self) -> str:
        # Wireshark-like baseline: capture broad TCP traffic and let the dissector identify
        # Modbus; parse_line drops non-Modbus lines. Matches the v2-dev runtime, which
        # ignored port_mode / custom_ports and always returned "tcp".
        return "tcp"

    def security_mode(self, tsv_fields: List[str]) -> str:
        return "clear"

    def parse_line(self, cols: List[str]) -> Optional[NormalizedEvent]:
        if len(cols) < 15:
            return None
        ts = float(cols[0]) if cols[0] else time.time()
        protocols = str(cols[1] or "").lower()
        src_ip = cols[2] or None
        src_port = _to_int(cols[3])
        dst_ip = cols[4] or None
        dst_port = _to_int(cols[5])
        tx_id = _parse_first_int(cols[6])
        unit_id = _parse_first_int(cols[7])
        func_code = _parse_first_int(cols[8])
        ref_write = _parse_first_int(cols[9])
        ref_read = _parse_first_int(cols[10])
        word_cnt = _parse_first_int(cols[11])
        bit_cnt = _parse_first_int(cols[12])
        reg_val = _parse_first_int(cols[13])
        reg_val_list = _parse_int_list(cols[13])
        bit_val = _parse_first_int(_col(cols, 14))
        exc = _parse_first_int(_col(cols, 15))
        regnum = _parse_first_int(_col(cols, 16))
        regnum_list = _parse_int_list(_col(cols, 16))
        data_val = _parse_hex_value(_col(cols, 17))

        # Detection is automatic by dissector (no hardcoded port filter): this extractor
        # only accepts frames the Modbus dissector identified.
        if "modbus" not in protocols:
            return None
        if not src_ip or not dst_ip or func_code is None:
            return None

        event_type = _event_type_for(func_code, dst_port)
        is_exception = exc is not None

        # Register resolution. On a RESPONSE the address is not in the PDU; the dissector pairs
        # it in as regnum16 (read_reference_num is EMPTY on responses in tshark 4.2.2 / 4.4.15),
        # so responses prefer regnum16, then read_reference_num. Writes use reference_num;
        # read requests carry their start in read_reference_num.
        if event_type == "READ_RESPONSE" and regnum is not None:
            register = regnum
        else:
            register = ref_write if ref_write is not None else ref_read

        quantity = word_cnt if word_cnt is not None else bit_cnt

        # Value resolution: regval_uint16, then the raw modbus.data payload as big-endian hex
        # (tshark 4.2.2 FC6), then a coil bit (FC5). Registers are raw uint16; no types on the wire.
        if reg_val is not None:
            value = reg_val
        elif data_val is not None:
            value = data_val
        else:
            value = bit_val

        # Per-register (number, value) pairs, so a multi-register FC3 response routes each value
        # to ITS register's flow rather than collapsing onto the header register. Single-register
        # (the bench case) yields one pair; nothing is silently dropped.
        reg_values = self._pair_registers(event_type, is_exception, register, reg_val_list,
                                          regnum_list, value)

        is_req = event_type.endswith("REQUEST")
        client_ep = f"{src_ip}:{src_port}" if is_req else f"{dst_ip}:{dst_port}"
        server_ep = f"{dst_ip}:{dst_port}" if is_req else f"{src_ip}:{src_port}"

        summary = _build_summary(
            func_code, src_ip, src_port, dst_ip, dst_port, event_type, register, value, quantity
        )

        # A WRITE_REQUEST's target is the flow-key STRING ("modbus:hr:<n>"), aligning it with
        # variable_key, so the grammar/verdict key is self-describing and survives JSON persistence
        # (no int-vs-str key friction). The raw int register is kept in raw["register"]. Reads and
        # responses keep the int register as their target.
        target = (f"modbus:hr:{register}"
                  if event_type == "WRITE_REQUEST" and register is not None else register)

        evt = NormalizedEvent(
            timestamp=ts,
            protocol="MODBUS/TCP",
            op=event_type,
            direction="request" if is_req else "response",
            src_ip=src_ip,
            src_port=src_port,
            dst_ip=dst_ip,
            dst_port=dst_port,
            client=client_ep,
            server=server_ep,
            target=target,
            value=value,
            quantity=quantity,
            summary=summary,
            security_mode="clear",
            raw={
                "transaction_id": tx_id,
                "function_code": func_code,
                "unit_id": unit_id,
                "exception_code": exc,
                "register": register,       # the raw int register (target may be the flow-key string)
                # Kept internal (not serialised to the wire dict).
                "reg_val_list": reg_val_list,
                "reg_values": reg_values,   # [(register, value), ...] for the flow / state path
            },
        )
        samples = self.extract_state_samples(evt)
        evt.state_signal_value = samples[-1] if samples else None
        return evt

    @staticmethod
    def _pair_registers(event_type, is_exception, register, reg_val_list, regnum_list, value):
        """Return [(register, value), ...] for this event.

        FC3 responses can carry several registers: pair each value with its regnum16 when the
        dissector supplied them, else attribute values to consecutive addresses from the resolved
        start. Non-reads (and exceptions) contribute at most their single (register, value).
        """
        if event_type == "READ_RESPONSE" and not is_exception:
            if regnum_list and reg_val_list:
                return list(zip(regnum_list, reg_val_list))
            if reg_val_list:
                start = register if register is not None else 0
                return [(start + i, v) for i, v in enumerate(reg_val_list)]
            return []
        if register is not None and value is not None and not is_exception:
            return [(register, value)]
        return []

    def extract_state_samples(self, evt: NormalizedEvent) -> List[float]:
        """The process-variable sample(s) this event carries, in wire order; else [].

        The state signal is the value(s) of FC3 read responses. With a configured
        state_signal_register only that register's values are returned; otherwise every read
        value is a state sample (valid when a single register is polled, as on the bench). This
        mirrors the OPC UA extractor's extract_state_samples and replaces the old element-[5]
        LEVEL_REGISTER hardcode.
        """
        if evt.op != "READ_RESPONSE" or evt.raw.get("exception_code") is not None:
            return []
        want = self.config.state_signal_register
        out: List[float] = []
        for reg, val in evt.raw.get("reg_values") or []:
            if val is None:
                continue
            if want is not None and reg != want:
                continue
            out.append(float(val))
        return out

    def variable_key(self, evt: NormalizedEvent) -> Optional[str]:
        """The flow/variable key an event belongs to (the same keys group_flows produces).

        Keyed per holding register ("modbus:hr:<n>"). Exceptions carry no usable register, so they
        route to a single metadata flow. Used both by group_flows and by the observer to attach a
        live value to the right variable.
        """
        if evt.raw.get("exception_code") is not None:
            return "modbus:metadata"
        # Derived from the raw int register (a write's target is the flow-key string, not the int).
        reg = evt.raw.get("register")
        if reg is not None and evt.op in ("READ_RESPONSE", "WRITE_REQUEST"):
            return f"modbus:hr:{reg}"
        return None

    def coalesce_writes(self, events):
        """Fold protocol-level write repetition, yielding events for the rest of the pipeline.

        An HMI holding a button re-issues the SAME write (target register, value) every ~230 ms at
        the wire. That is one operator action, not many. Consecutive WRITE_REQUEST events with an
        identical (register, value) are folded into the first event of the run (its timestamp wins;
        raw["repeat_count"] counts the repeats); the duplicates are dropped.

        A run breaks -- the next write starts a fresh event -- on ANY of:
          - a change of value or target register;
          - a gap from the previous write greater than config.coalesce_window_s;
          - the run's total span (this write minus the run's FIRST timestamp) exceeding
            config.coalesce_window_s.
        The last, span-based bound is what keeps a CONTINUOUS burst from collapsing without limit: a
        gapless 230 ms burst longer than the window splits into ceil(duration / window) events, so a
        phase reversal inside a held button still yields distinct verdicts on each side.

        Non-write events (reads, responses) pass straight through and do NOT break a run, so writes
        interleaved with the FC3 poll still coalesce. Stateful generator, applied identically in the
        observe / learn / evaluate paths (see the observer's capture layer); it removes protocol
        repetition only and changes no verdict logic.
        """
        window = self.config.coalesce_window_s
        run = None   # {"reg", "val", "start_ts", "last_ts", "evt"} of the open run
        for evt in events:
            if evt.op == "WRITE_REQUEST" and evt.target is not None:
                reg, val, ts = evt.target, evt.value, evt.timestamp
                if (run is not None and run["reg"] == reg and run["val"] == val
                        and (ts - run["last_ts"]) <= window
                        and (ts - run["start_ts"]) <= window):
                    kept = run["evt"]
                    kept.raw["repeat_count"] = kept.raw.get("repeat_count", 1) + 1
                    run["last_ts"] = ts        # slide the gap check onto this repeat
                    continue                   # drop the wire repetition
                evt.raw["repeat_count"] = 1
                run = {"reg": reg, "val": val, "start_ts": ts, "last_ts": ts, "evt": evt}
            yield evt

    def group_flows(self, events) -> list:
        """Group parsed Modbus events into role-hinted flows for the behavioural classifier.

        Mirrors the OPC UA extractor. Role hints reinforce (they do not replace) the core's
        behavioural decision (otlab_core.engine.discover):

        - FC3 read responses -> one telemetry flow per register (a STATE candidate). A
          multi-register response routes each value to its own register's flow.
        - FC6 / FC16 writes -> one COMMAND flow per target register (target-gated, so a command
          always surfaces even when its value did not parse).
        - Exceptions -> a single metadata flow.

        datatype is always uncertain ("?"): Modbus declares no types on the wire, so the extractor
        does not infer one. Values are raw uint16.

        Single-role-per-register assumption: a register is treated as either polled OR commanded
        (first-seen role wins); read and write values are never mixed into one series. The bench's
        read and write registers are disjoint, and the core classifier still sees the behaviour.
        """
        from ..engine.discover import Flow  # local import: keep extractor import lightweight

        samples: dict = {}    # key -> list[(t, value)]
        roles: dict = {}      # key -> role_hint
        servers: dict = {}    # key -> observed server endpoint "ip:port" (from the traffic)
        for evt in events:
            if evt.raw.get("exception_code") is not None:
                roles.setdefault("modbus:metadata", "read")   # no register -> metadata, no sample
                if evt.server:
                    servers.setdefault("modbus:metadata", evt.server)
                continue
            if evt.op == "READ_RESPONSE":
                for reg, val in evt.raw.get("reg_values") or []:
                    key = f"modbus:hr:{reg}"
                    if roles.setdefault(key, "telemetry") != "telemetry":
                        continue   # already a command register; do not mix read values in
                    if evt.server:
                        servers.setdefault(key, evt.server)
                    num = _as_number(val)
                    if num is not None:
                        samples.setdefault(key, []).append((evt.timestamp, num))
            elif evt.op == "WRITE_REQUEST" and evt.raw.get("register") is not None:
                key = self.variable_key(evt)   # "modbus:hr:<n>" from the raw int register
                if roles.setdefault(key, "command") != "command":
                    continue   # already a polled register; do not mix write values in
                if evt.server:
                    servers.setdefault(key, evt.server)
                num = _as_number(evt.value)
                if num is not None:
                    samples.setdefault(key, []).append((evt.timestamp, num))
            # READ_REQUEST / WRITE_RESPONSE carry no state or command value -> ignored.

        flows: list = []
        for key, role in roles.items():
            # Attach the observed server endpoint (ip:port) so the UI can show the Modbus port,
            # exactly like OPC UA -- but sourced from the traffic, not a configured port.
            flows.append(Flow(key=key, samples=samples.get(key, []), role_hint=role,
                              datatype=None, datatype_certain=False, server=servers.get(key)))
        return flows

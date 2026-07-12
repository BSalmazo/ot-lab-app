"""Modbus/TCP passive extractor (Phase 1B).

This is a faithful refactor of the parsing that lived in ``scripts/tshark_runtime.py`` on the
``v2-dev`` baseline. The field list, the drop conditions, the register/value/quantity
derivation, and the summary string are reproduced exactly, so the event this produces
serialises (via ``NormalizedEvent.to_wire_dict``) to byte-identical JSON.

The runtime no longer hardcodes ``"modbus" not in protocols`` — that protocol-recognition check
now lives here, where it belongs, inside the Modbus extractor.
"""

from __future__ import annotations

import time
from typing import List, Optional

from ..contract import NormalizedEvent
from .base import ProtocolExtractor

MODBUS_DEFAULT_PORTS = {502, 5020, 15020}
WRITE_FUNCTIONS = {5, 6, 15, 16}

# Index of the tank level within a holding-register read array, hardcoded exactly as
# LTR-2026-03 does with LEVEL_REGISTER=5. This is protocol-specific, so it lives in the
# extractor rather than the (protocol-neutral) engine.
LEVEL_REGISTER = 5

_FIELDS = [
    "frame.time_epoch",
    "frame.protocols",
    "ip.src",
    "tcp.srcport",
    "ip.dst",
    "tcp.dstport",
    "mbtcp.trans_id",
    "mbtcp.unit_id",
    "modbus.func_code",
    "modbus.reference_num",
    "modbus.read_reference_num",
    "modbus.word_cnt",
    "modbus.bit_cnt",
    "modbus.regval_uint16",
    "modbus.bitval",
    "modbus.exception_code",
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
        bit_val = _parse_first_int(cols[14])
        exc = _parse_first_int(cols[15]) if len(cols) > 15 else None

        # Detection is automatic by dissector (no hardcoded port filter): this extractor
        # only accepts frames the Modbus dissector identified.
        if "modbus" not in protocols:
            return None
        if not src_ip or not dst_ip or func_code is None:
            return None

        event_type = _event_type_for(func_code, dst_port)
        register = ref_write if ref_write is not None else ref_read
        quantity = word_cnt if word_cnt is not None else bit_cnt
        value = reg_val if reg_val is not None else bit_val

        is_req = event_type.endswith("REQUEST")
        client_ep = f"{src_ip}:{src_port}" if is_req else f"{dst_ip}:{dst_port}"
        server_ep = f"{dst_ip}:{dst_port}" if is_req else f"{src_ip}:{src_port}"

        summary = _build_summary(
            func_code, src_ip, src_port, dst_ip, dst_port, event_type, register, value, quantity
        )

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
            target=register,
            value=value,
            quantity=quantity,
            summary=summary,
            security_mode="clear",
            raw={
                "transaction_id": tx_id,
                "function_code": func_code,
                "unit_id": unit_id,
                "exception_code": exc,
                # Kept internal (not serialised to the wire dict); used by extract_state_signal.
                "reg_val_list": reg_val_list,
            },
        )
        evt.state_signal_value = self.extract_state_signal(evt)
        return evt

    def extract_state_signal(self, evt: NormalizedEvent) -> Optional[float]:
        # Modbus: the tank level is element [LEVEL_REGISTER] of a holding-register read
        # response, exactly as LTR-2026-03 does. Kept hardcoded for now, by design.
        #
        # NOTE: the current capture granularity (tshark -E occurrence=f, preserved from
        # v2-dev) exposes only the first register value, so reg_val_list has length 1 and
        # this returns None in the live path. When the learned engine is switched into the
        # passive path (a later, explicit step) the capture will request all occurrences and
        # this will yield the real level sample. Nothing consumes this value in Phase 1.
        if evt.op != "READ_RESPONSE":
            return None
        vals = evt.raw.get("reg_val_list") or []
        if len(vals) > LEVEL_REGISTER:
            return float(vals[LEVEL_REGISTER])
        return None

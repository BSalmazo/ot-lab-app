"""Passive protocol extractors.

An extractor confines everything protocol-specific about turning captured tshark output into
a ``NormalizedEvent``: which fields to request, how to recognise the protocol on the wire, how
to read a value, and where the process-state signal lives. Phase 1 shipped Modbus; Phase 2 adds
OPC UA (opc.tcp binary), implementing the same interface.
"""

from typing import Optional

from .base import ProtocolExtractor
from .modbus import ModbusExtractor
from .opcua import OpcUaExtractor

__all__ = ["ProtocolExtractor", "ModbusExtractor", "OpcUaExtractor", "get_extractor"]


def get_extractor(name: str = "modbus", config=None) -> ProtocolExtractor:
    """Select a protocol extractor by name.

    Defaults to Modbus, so the live passive path is unchanged unless explicitly switched.
    ``config`` is an optional ``otlab_core.config.ObserverConfig``; its per-protocol section is
    passed to the extractor when relevant.
    """
    key = (name or "modbus").strip().lower()
    if key in ("modbus", "modbus/tcp"):
        return ModbusExtractor()
    if key in ("opcua", "opc-ua", "opcua/binary", "opc.tcp"):
        opcua_cfg = getattr(config, "opcua", None) if config is not None else None
        return OpcUaExtractor(config=opcua_cfg)
    raise ValueError(f"unknown protocol extractor: {name!r}")

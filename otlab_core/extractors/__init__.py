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
from .s7 import S7CommExtractor

#: Every extractor the runtime knows. The one place to register a protocol: add its class here (and
#: its module). Both get_extractor (by name) and extractor_for_layer (by wire layer) derive from it,
#: so a new protocol needs no second lookup table.
EXTRACTOR_CLASSES = (ModbusExtractor, OpcUaExtractor, S7CommExtractor)

__all__ = [
    "ProtocolExtractor", "ModbusExtractor", "OpcUaExtractor", "S7CommExtractor",
    "EXTRACTOR_CLASSES", "get_extractor", "extractor_for_layer",
]


def extractor_for_layer(layer: str) -> Optional[ProtocolExtractor]:
    """The extractor that consumes a given ``frame.protocols`` dissector substring, or ``None``.

    The inverse of get_extractor's name lookup, derived from each class's declared ``wire_layer``
    rather than a separate protocol->name table. A new protocol becomes reachable here just by being
    added to EXTRACTOR_CLASSES with a wire_layer set; nothing else changes.
    """
    if not layer:
        return None
    for cls in EXTRACTOR_CLASSES:
        if cls.wire_layer and cls.wire_layer == layer:
            return cls()
    return None


def get_extractor(name: str = "modbus", config=None) -> ProtocolExtractor:
    """Select a protocol extractor by name.

    Defaults to Modbus, so the live passive path is unchanged unless explicitly switched.
    ``config`` is an optional ``otlab_core.config.ObserverConfig``; its per-protocol section is
    passed to the extractor when relevant.
    """
    key = (name or "modbus").strip().lower()
    if key in ("modbus", "modbus/tcp"):
        modbus_cfg = getattr(config, "modbus", None) if config is not None else None
        return ModbusExtractor(config=modbus_cfg)
    if key in ("opcua", "opc-ua", "opcua/binary", "opc.tcp"):
        opcua_cfg = getattr(config, "opcua", None) if config is not None else None
        return OpcUaExtractor(config=opcua_cfg)
    if key in ("s7", "s7comm"):
        s7_cfg = getattr(config, "s7", None) if config is not None else None
        return S7CommExtractor(config=s7_cfg)
    raise ValueError(f"unknown protocol extractor: {name!r}")

"""Passive protocol extractors.

An extractor confines everything protocol-specific about turning captured tshark output into
a ``NormalizedEvent``: which fields to request, how to recognise the protocol on the wire, how
to read a value, and where the process-state signal lives. Phase 1 ships Modbus only; Phase 2
will add an OPC UA extractor implementing the same interface.
"""

from .base import ProtocolExtractor
from .modbus import ModbusExtractor

__all__ = ["ProtocolExtractor", "ModbusExtractor"]

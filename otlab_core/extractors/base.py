"""The ProtocolExtractor interface (Phase 1B).

All protocol-specific knowledge for passive dissection sits behind this interface, so the
capture runtime (``scripts/tshark_runtime.py``) and the analysis engine (``otlab_core.engine``)
can stay protocol-neutral. Adding a protocol means adding an implementation of this class and
selecting it in the runtime; no runtime or engine change should be needed beyond that.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from ..contract import NormalizedEvent


class ProtocolExtractor(ABC):
    #: Human-readable protocol label; also used as ``NormalizedEvent.protocol``.
    name = "GENERIC"

    @abstractmethod
    def tshark_fields(self) -> List[str]:
        """The ``-e`` field list this protocol needs from tshark."""

    @abstractmethod
    def capture_filter(self) -> str:
        """The capture filter (``-f``) for this protocol."""

    @abstractmethod
    def parse_line(self, tsv_fields: List[str]) -> Optional[NormalizedEvent]:
        """Turn one split TSV line into a ``NormalizedEvent``, or ``None`` to drop it."""

    @abstractmethod
    def security_mode(self, tsv_fields: List[str]) -> str:
        """Security posture of this exchange (Modbus: always ``"clear"``)."""

    @abstractmethod
    def extract_state_signal(self, evt: NormalizedEvent) -> Optional[float]:
        """Reconstruct the process-variable sample this event carries, if any; else ``None``."""

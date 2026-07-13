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

    def occurrence(self) -> str:
        """tshark ``-E occurrence=`` mode for this protocol.

        Default ``"f"`` (first occurrence only) — the historical Modbus behaviour, which
        must stay byte-identical. Protocols whose fields are legitimately multi-valued
        (e.g. OPC UA NodeId / value lists) override this to ``"a"`` (all occurrences).
        """
        return "f"

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
    def extract_state_samples(self, evt: NormalizedEvent) -> List[float]:
        """The process-variable sample(s) this event carries, in wire order; else ``[]``.

        This is the interface the observer drives (it feeds every sample, in order, to the
        PhaseTracker). A single event may carry one sample or several (e.g. a multi-register
        Modbus read, or a batched OPC UA publish). An extractor may still expose a scalar
        ``extract_state_signal`` convenience internally, but only this method is required.
        """

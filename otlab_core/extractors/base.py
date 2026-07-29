"""The ProtocolExtractor interface (Phase 1B).

All protocol-specific knowledge for passive dissection sits behind this interface, so the
capture runtime (``scripts/tshark_runtime.py``) and the analysis engine (``otlab_core.engine``)
can stay protocol-neutral. Adding a protocol means adding an implementation of this class and
selecting it in the runtime; no runtime or engine change should be needed beyond that.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

from ..contract import NormalizedEvent


class ProtocolExtractor(ABC):
    #: Human-readable protocol label; also used as ``NormalizedEvent.protocol``.
    name = "GENERIC"

    #: The ``frame.protocols`` dissector substring this extractor consumes (e.g. "s7comm"). Single
    #: source of truth for both the ``parse_line`` gate and the protocol probe's reverse lookup, so
    #: the two can never drift. A subclass that leaves this ``None`` declares no wire layer and is
    #: simply never claimed by the probe. NOT ``name`` (a display label that matches only by chance).
    wire_layer = None

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
    def extract_state_samples(self, evt: NormalizedEvent, state_key: Optional[str] = None) -> List[float]:
        """The process-variable sample(s) this event carries, in wire order; else ``[]``.

        This is the interface the observer drives (it feeds every sample, in order, to the
        PhaseTracker). A single event may carry one sample or several (e.g. a multi-register
        Modbus read, or a batched OPC UA publish). An extractor may still expose a scalar
        ``extract_state_signal`` convenience internally, but only this method is required.

        ``state_key`` is the discovered state flow key (as ``group_flows`` produces it), passed by
        the live observer so an extractor whose transport batches several variables in one message
        can attribute values to the DISCOVERED signal and feed the tracker ONLY that signal's
        samples. It is an optional hint: an extractor whose transport carries one variable per
        message (a polled read) needs no per-item routing and MAY ignore it, with identical
        behaviour. ``None`` means no routing hint (the discovery pass, or a single-variable feed).
        """

    def opaque_endpoint(self, tsv_fields: List[str]) -> Optional[Tuple[str, str]]:
        """Report a captured-but-unreadable endpoint, or ``None``.

        Called ONLY on a frame that arrived on this extractor's capture filter and that
        ``parse_line`` declined. Return ``(endpoint, reason)`` when the frame carries this
        protocol's transport shape but a payload this extractor cannot read (e.g. an encrypted
        variant on the same service port); ``None`` otherwise.

        ``None`` means ALL of: the frame is transport plumbing (a bare ACK, a handshake, a mirror
        retransmission); OR it is actually readable and ``parse_line`` declined for another reason;
        OR the extractor cannot tell. Returning ``None`` when unsure is correct -- a false
        "unreadable" claim is worse than silence.

        Default is ``None``: an extractor that does not implement this is simply never asked to
        report opaque traffic, so the observer stays protocol-neutral (it only asks, null-safe).
        """
        return None

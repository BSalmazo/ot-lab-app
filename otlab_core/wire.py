"""Wire-layer classification shared by the probe and the observer.

Pure, protocol-neutral. Given a tshark ``frame.protocols`` dissector chain, decide which application
layer a frame carries (if any) and which known extractor claims it. These functions were script-local
in ``scripts/liscere_probe.py``; they now live here so the observer (which must run the extractors the
probe claims) and the probe (which reports them) share one implementation and cannot drift.

The transport token is TCP: everything the chain lists AFTER the last ``tcp`` is the application
layer; a chain that stops at it is transport plumbing (a bare ACK, a handshake, an undissected
retransmission). No protocol name is hardcoded beyond the transport all these extractors bind to.
"""

from typing import List, Optional

from otlab_core.extractors import EXTRACTOR_CLASSES

#: The transport layer these extractors ride on. The application layer is whatever the frame.protocols
#: chain lists after it; a chain ending at it carries no application payload.
TRANSPORT_TOKEN = "tcp"


def wire_layers() -> List[str]:
    """The frame.protocols substrings the known extractors declare (single source of truth)."""
    return [c.wire_layer for c in EXTRACTOR_CLASSES if c.wire_layer]


def app_layer(protocols: str, transport: str = TRANSPORT_TOKEN) -> str:
    """The application-layer portion of a frame.protocols chain, or "" if there is none.

    Structural, not name-based: split the colon-separated chain and return everything after the
    transport token. A frame whose chain ends at the transport layer -- a bare ACK, a handshake
    segment, or a retransmission tshark did not dissect -- has nothing after it and returns "". That
    is TCP plumbing, not application traffic. The LAST transport occurrence is used so a tunnelled
    transport-over-transport chain still resolves to its innermost application layers.
    """
    tokens = str(protocols).lower().split(":")
    if transport not in tokens:
        return ""
    idx = len(tokens) - 1 - tokens[::-1].index(transport)
    return ":".join(tokens[idx + 1:])


def match_layer(protocols: str, layers: List[str]) -> Optional[str]:
    """The wire layer an extractor claims in this frame's protocols chain, or None."""
    p = str(protocols).lower()
    return next((layer for layer in layers if layer in p), None)


def endpoint(ip: str, port: str) -> Optional[str]:
    """An "ip:port" endpoint string, or None when either half is missing."""
    return f"{ip}:{port}" if ip and port else None


def port_of(ep: str) -> float:
    """The port of an "ip:port" endpoint, or +inf so an unparseable one never wins the server rule."""
    tail = str(ep).rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else float("inf")


def server_of(endpoint_a: str, endpoint_b: str) -> str:
    """SERVER-ENDPOINT HEURISTIC: of the two endpoints in a conversation, guess the server is the one
    with the LOWER TCP port.

    This is a HEURISTIC, not a fact derived from the traffic. It holds when a service binds a fixed,
    well-known (low) port while the initiating client uses a higher ephemeral port -- true on the
    bench and for typical OT. It FAILS when a service listens on a port higher than the client's
    ephemeral port (e.g. a Modbus device on 15020). No specific port number is assumed -- only the
    two ports present are compared -- but the low-port assumption can still be wrong.

    Used ONLY to label pre-pipeline, human-readable reports (the probe's UNCLAIMED, the observer's
    "traffic no extractor reads"). It is NOT the authority for silo identity: once an extractor runs,
    the server endpoint comes from evt.server, derived from protocol request/response semantics.
    """
    return endpoint_a if port_of(endpoint_a) <= port_of(endpoint_b) else endpoint_b

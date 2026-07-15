#!/usr/bin/env python3
"""Liscere protocol probe (phase 1: discovery only).

Captures briefly on an interface with NO capture filter and reports which protocol silos are
present, keyed by (wire layer, server endpoint) -- including traffic no extractor can read. It
changes nothing about the observer/pipeline: it only reads frame.protocols and endpoints and matches
them against each extractor's declared ``wire_layer``.

By construction this file names no protocol and no port: the protocols come from the extractor
``wire_layer`` declarations, and the server rule compares only ports observed in the traffic.

The "server endpoint" in the CLAIMED report is a HEURISTIC guess: of the two endpoints, the one with
the lower TCP port is assumed to be the server. That holds when a service binds a well-known low port
and clients use high ephemeral ports (the bench, typical OT), but it FAILS when a service listens on
a high port -- this repo's own seed data has a Modbus/TCP device on 15020, and a client ephemeral
port below it would invert the guess. The heuristic exists only to make this text report legible; it
is NOT the authority for silo identity. The real pipeline takes the server from evt.server, which
each extractor derives from protocol request/response semantics, not from port ordering.

    sudo python3 scripts/liscere_probe.py --iface eth0 --duration 30
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections import Counter, defaultdict

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from otlab_core.extractors import EXTRACTOR_CLASSES

# tshark -e fields, in the order parse below indexes them. Endpoints + the dissector chain only.
_FIELDS = ["frame.protocols", "ip.src", "tcp.srcport", "ip.dst", "tcp.dstport"]

# The transport-layer token in a frame.protocols chain, derived from the port field we already query
# (index 2 -> "tcp.srcport" -> "tcp") rather than hardcoded. Everything the chain lists AFTER this
# token is the application layer; a chain that stops at it carries no application payload.
_TRANSPORT = _FIELDS[2].split(".", 1)[0]


def _wire_layers():
    """The frame.protocols substrings the known extractors declare (single source of truth)."""
    return [c.wire_layer for c in EXTRACTOR_CLASSES if c.wire_layer]


def _app_chain(protocols):
    """The application-layer portion of a frame.protocols chain, or "" if there is none.

    Structural, not name-based: split the colon-separated chain and return everything after the
    transport token. A frame whose chain ends at the transport layer -- a bare ACK, a handshake
    segment, or a retransmission tshark did not dissect -- has nothing after it and returns "".
    That is TCP plumbing, not application traffic. The last transport occurrence is used so a
    tunnelled transport-over-transport chain still resolves to its innermost application layers.
    """
    tokens = protocols.lower().split(":")
    if _TRANSPORT not in tokens:
        return ""
    idx = len(tokens) - 1 - tokens[::-1].index(_TRANSPORT)
    return ":".join(tokens[idx + 1:])


def _match_layer(protocols, layers):
    """The wire layer an extractor claims in this frame's protocols chain, or None."""
    p = protocols.lower()
    return next((layer for layer in layers if layer in p), None)


def _endpoint(ip, port):
    return f"{ip}:{port}" if ip and port else None


def _port_of(endpoint):
    """The port of an "ip:port" endpoint, or +inf so an unparseable one never wins the server rule."""
    tail = endpoint.rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else float("inf")


def _server_of(endpoint_a, endpoint_b):
    """SERVER-ENDPOINT HEURISTIC (stated so it survives without this docstring): of the two endpoints
    in a conversation, guess the server is the one with the LOWER TCP port.

    This is a HEURISTIC, not a fact derived from the traffic. It holds when a service binds a fixed,
    well-known (low) port while the initiating client uses a higher ephemeral port -- true on the
    bench and for typical OT. It FAILS when a service listens on a port higher than the client's
    ephemeral port: e.g. this repo's own seed data has a Modbus/TCP device on port 15020
    (scripts/v2_seed/backups/.../Tank.json); a client ephemeral port below 15020 would invert the
    guess and label the client as the server. No specific port number is assumed here -- only the two
    ports present in the conversation are compared -- but the low-port assumption can still be wrong.

    Used ONLY to make the probe's human-readable report legible. It is NOT the authority for silo
    identity. When the pipeline actually runs an extractor, the server endpoint comes from evt.server,
    which each extractor derives from protocol request/response semantics (who answers a read), not
    from port ordering.
    """
    return endpoint_a if _port_of(endpoint_a) <= _port_of(endpoint_b) else endpoint_b


def capture(iface, duration):
    """Yield (protocols, src_endpoint, dst_endpoint) per captured frame.

    Uses tshark's own ``-a duration:N`` to stop, NOT an external timeout: a SIGTERM'd tshark loses
    its stdout buffer and the probe returns nothing (measured, not theoretical). No -f capture
    filter: every frame is seen, so unreadable traffic is reported rather than filtered away.
    """
    cmd = [
        "tshark", "-l", "-n", "-Q", "-i", iface,
        "-T", "fields", "-E", "separator=\t", "-E", "quote=n",
        "-a", f"duration:{int(duration)}",
    ]
    for field in _FIELDS:
        cmd += ["-e", field]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        for line in proc.stdout:
            cols = line.rstrip("\n").split("\t")
            protocols = cols[0] if cols else ""
            src = _endpoint(cols[1] if len(cols) > 1 else "", cols[2] if len(cols) > 2 else "")
            dst = _endpoint(cols[3] if len(cols) > 3 else "", cols[4] if len(cols) > 4 else "")
            if protocols and src and dst:   # a TCP frame with both endpoints; app/plumbing split in main
                yield protocols, src, dst
    finally:
        proc.wait()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Probe which protocol silos are present on an interface.")
    ap.add_argument("--iface", required=True, help="capture interface (mirror port)")
    ap.add_argument("--duration", type=int, default=30, help="capture seconds (tshark -a duration)")
    args = ap.parse_args(argv)

    layers = _wire_layers()

    # CLAIMED: application frames a wire layer matched, per (layer, unordered endpoint pair).
    claimed = defaultdict(Counter)   # layer -> Counter[frozenset({ep_a, ep_b})]
    # UNCLAIMED: application frames no wire layer matched, per (pair, application-layer chain).
    unclaimed = Counter()            # (frozenset({ep_a, ep_b}), app_chain) -> count
    app_total = 0        # frames carrying an application layer (the ones the header counts)
    plumbing = 0         # transport-only frames (ACK/handshake/retransmit): seen, not shown

    print(f"probing {args.iface} for {args.duration}s (no filter)…", file=sys.stderr)
    for protocols, src, dst in capture(args.iface, args.duration):
        app = _app_chain(protocols)
        if not app:                  # chain stops at the transport layer -> plumbing, not traffic
            plumbing += 1
            continue
        app_total += 1
        pair = frozenset((src, dst))
        layer = _match_layer(protocols, layers)
        if layer is not None:
            claimed[layer][pair] += 1
        else:
            unclaimed[(pair, app)] += 1

    _report(claimed, unclaimed, app_total, plumbing)
    return 0


def _servers_of(pairs):
    """Collapse {frozenset(pair): count} to {server_endpoint: {count, peers}} via _server_of."""
    by_server = defaultdict(lambda: {"count": 0, "peers": set()})
    for pair, count in pairs.items():
        eps = tuple(pair)
        a, b = (eps[0], eps[1]) if len(eps) == 2 else (eps[0], eps[0])
        server = _server_of(a, b)
        peer = b if server == a else a
        entry = by_server[server]
        entry["count"] += count
        entry["peers"].add(peer)
    return by_server


def _peer_ips(peers, cap=4):
    """Distinct peer HOSTS as a display string. A peer is a host, not a session: several TCP
    sessions from the same host (different ephemeral ports) collapse to one IP. Capped for
    legibility -- beyond `cap`, the extras are summarised as a count rather than listed.
    """
    ips = sorted({p.rsplit(":", 1)[0] for p in peers})
    if len(ips) <= cap:
        return ", ".join(ips)
    return ", ".join(ips[:cap]) + f", +{len(ips) - cap} more"


def _report(claimed, unclaimed, app_total, plumbing):
    print()
    print(f"PROTOCOL PROBE  ·  {app_total} application frames")
    print()

    # CLAIMED, aggregated per (layer, server endpoint), server picked by _server_of.
    print("CLAIMED  ·  a declared wire layer matched")
    rows = []
    for layer, pairs in claimed.items():
        for server, entry in _servers_of(pairs).items():
            rows.append((layer, server, entry["count"], sorted(entry["peers"])))
    if not rows:
        print("    (none)")
    for layer, server, count, peers in sorted(rows, key=lambda r: (r[0], r[1])):
        print(f"    {layer:<8} {server:<22} {count:>7} frames   peer {_peer_ips(peers)}")

    print()
    # UNCLAIMED, aggregated per server endpoint with the SAME server rule as CLAIMED, so one service
    # is one line even when its layer chain varies frame to frame. Only application frames reach here;
    # the chain is the part after the transport layer, shown verbatim and not interpreted. Where an
    # endpoint shows several chains, the most frequent is shown and the rest noted as a variant count.
    print("UNCLAIMED  ·  application traffic no extractor reads (layer chain verbatim, not interpreted)")
    urows = []
    for server, entry in _unclaimed_by_server(unclaimed).items():
        chains = entry["chains"]
        dominant, _ = chains.most_common(1)[0]
        variants = len(chains) - 1
        urows.append((server, entry["count"], sorted(entry["peers"]), dominant, variants))
    if not urows:
        print("    (none)")
    for server, count, peers, dominant, variants in sorted(urows, key=lambda r: (-r[1], r[0])):
        chain_disp = dominant if not variants else f"{dominant}  (+{variants} more)"
        print(f"    {server:<22} {count:>7} frames   {chain_disp:<28} peer {_peer_ips(peers)}")
    if plumbing:
        print(f"    (+ {plumbing} transport-only frames -- ACKs, handshakes, retransmissions -- not shown)")
    print()


def _unclaimed_by_server(unclaimed):
    """Collapse the (pair, app_chain)->count Counter to {server: {count, peers, chains}} via the
    server rule, so an endpoint is one entry regardless of how its layer chain varies. `chains` is a
    Counter of the endpoint's observed application chains, so the dominant one and how many others
    exist are both recoverable without a line per chain.
    """
    by_server = defaultdict(lambda: {"count": 0, "peers": set(), "chains": Counter()})
    for (pair, app_chain), count in unclaimed.items():
        eps = tuple(pair)
        a, b = (eps[0], eps[1]) if len(eps) == 2 else (eps[0], eps[0])
        server = _server_of(a, b)
        peer = b if server == a else a
        entry = by_server[server]
        entry["count"] += count
        entry["peers"].add(peer)
        entry["chains"][app_chain] += count
    return by_server


if __name__ == "__main__":
    raise SystemExit(main())

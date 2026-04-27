#!/usr/bin/env python3
import argparse
import socket
import struct
import time


def recv_exact(sock: socket.socket, n: int) -> bytes:
    out = b""
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise RuntimeError("socket closed")
        out += chunk
    return out


def send_fc03(host: str, port: int, tx_id: int, unit_id: int, start_addr: int, quantity: int) -> None:
    pdu = bytes([3]) + struct.pack(">HH", start_addr, quantity)
    mbap = struct.pack(">HHHB", tx_id, 0, len(pdu) + 1, unit_id)
    req = mbap + pdu

    with socket.create_connection((host, port), timeout=2.0) as sock:
        sock.settimeout(2.0)
        sock.sendall(req)
        header = recv_exact(sock, 7)
        rx_tx, proto, length = struct.unpack(">HHH", header[:6])
        _unit = header[6]
        body = recv_exact(sock, length - 1)
        if rx_tx != tx_id or proto != 0:
            raise RuntimeError("invalid MBAP response")
        fc = body[0]
        if fc & 0x80:
            raise RuntimeError(f"modbus exception code={body[1]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate FC03 Modbus/TCP load.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=15020)
    parser.add_argument("--unit-id", type=int, default=1)
    parser.add_argument("--start-addr", type=int, default=0)
    parser.add_argument("--quantity", type=int, default=4)
    parser.add_argument("--rate", type=float, default=8.0, help="Requests per second.")
    parser.add_argument("--duration", type=float, default=300.0, help="Seconds.")
    args = parser.parse_args()

    interval = 1.0 / max(0.1, float(args.rate))
    end_at = time.time() + float(args.duration)

    ok = 0
    err = 0
    tx = 1

    print(
        f"[load] host={args.host}:{args.port} rate={args.rate}rps duration={args.duration}s "
        f"start={args.start_addr} qty={args.quantity}"
    )

    while time.time() < end_at:
        started = time.time()
        try:
            send_fc03(
                host=args.host,
                port=int(args.port),
                tx_id=tx & 0xFFFF,
                unit_id=int(args.unit_id),
                start_addr=int(args.start_addr),
                quantity=int(args.quantity),
            )
            ok += 1
        except Exception as exc:
            err += 1
            print(f"[load] request failed: {exc}")

        tx += 1
        sleep_for = interval - (time.time() - started)
        if sleep_for > 0:
            time.sleep(sleep_for)

    total = ok + err
    print(f"[load] finished total={total} ok={ok} err={err}")


if __name__ == "__main__":
    main()


import select
import socket
import threading
import time

from .modbus_parser import decode_modbus, tx_key


def _extract_frames(buffer: bytearray):
    frames = []
    offset = 0
    total_len = len(buffer)
    while offset + 7 <= total_len:
        protocol_id = int.from_bytes(buffer[offset + 2:offset + 4], "big")
        if protocol_id != 0:
            break
        length_field = int.from_bytes(buffer[offset + 4:offset + 6], "big")
        frame_len = 6 + length_field
        if frame_len < 8:
            break
        if offset + frame_len > total_len:
            break
        frame = bytes(buffer[offset:offset + frame_len])
        frames.append(frame)
        offset += frame_len
    if offset > 0:
        del buffer[:offset]
    return frames


class _ProxyDecodeContext:
    def __init__(self, session_id: str, agent_id: str):
        self.session_id = session_id
        self.agent_id = agent_id
        self.state = {"pending_transactions": {}}


class ModbusTcpProxy:
    def __init__(
        self,
        listen_host: str,
        listen_port: int,
        upstream_host: str,
        upstream_port: int,
        session_id: str,
        agent_id: str,
        on_event=None,
    ):
        self.listen_host = str(listen_host or "0.0.0.0")
        self.listen_port = int(listen_port)
        self.upstream_host = str(upstream_host or "openplc")
        self.upstream_port = int(upstream_port)
        self.session_id = session_id
        self.agent_id = agent_id
        self.on_event = on_event
        self._thread = None
        self._stop = threading.Event()
        self._server = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, name="modbus-proxy", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        try:
            if self._server:
                self._server.close()
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        self._server = None

    def _serve(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.listen_host, self.listen_port))
        srv.listen(32)
        srv.settimeout(0.5)
        self._server = srv
        print(
            f"[proxy] listening on {self.listen_host}:{self.listen_port} "
            f"-> {self.upstream_host}:{self.upstream_port}"
        )
        while not self._stop.is_set():
            try:
                client, client_addr = srv.accept()
            except socket.timeout:
                continue
            except Exception:
                if not self._stop.is_set():
                    time.sleep(0.1)
                continue
            worker = threading.Thread(
                target=self._handle_client,
                args=(client, client_addr),
                daemon=True,
            )
            worker.start()

    def _emit(self, event):
        if not event or not callable(self.on_event):
            return
        try:
            self.on_event(event)
        except Exception:
            pass

    def _emit_link_event(self, kind: str, client_addr, summary: str):
        ts = time.time()
        try:
            upstream_ip = socket.gethostbyname(self.upstream_host)
        except Exception:
            upstream_ip = self.upstream_host
        event = {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "timestamp": ts,
            "src_ip": str(client_addr[0]),
            "src_port": int(client_addr[1]),
            "dst_ip": str(upstream_ip),
            "dst_port": int(self.upstream_port),
            "client": f"{client_addr[0]}:{int(client_addr[1])}",
            "server": f"{upstream_ip}:{int(self.upstream_port)}",
            "direction": "request",
            "transaction_id": None,
            "function_code": None,
            "unit_id": None,
            "length": 0,
            "protocol": "MODBUS/TCP",
            "type": kind,
            "summary": summary,
            "iface": "proxy",
        }
        self._emit(event)

    def _handle_client(self, client_sock: socket.socket, client_addr):
        upstream = None
        try:
            self._emit_link_event(
                "LINK_OPEN",
                client_addr,
                f"Proxy connection open {client_addr[0]}:{int(client_addr[1])} -> {self.upstream_host}:{int(self.upstream_port)}",
            )
            upstream = socket.create_connection((self.upstream_host, self.upstream_port), timeout=2.0)
            client_sock.setblocking(False)
            upstream.setblocking(False)

            req_buf = bytearray()
            rsp_buf = bytearray()
            decode_ctx = _ProxyDecodeContext(self.session_id, self.agent_id)

            sockets = [client_sock, upstream]
            while not self._stop.is_set():
                rlist, _, _ = select.select(sockets, [], [], 0.25)
                if not rlist:
                    continue
                for sock_in in rlist:
                    try:
                        chunk = sock_in.recv(65535)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        return
                    if sock_in is client_sock:
                        upstream.sendall(chunk)
                        req_buf.extend(chunk)
                        for frame in _extract_frames(req_buf):
                            ts = time.time()
                            src_ip, src_port = client_addr[0], int(client_addr[1])
                            try:
                                dst_ip = socket.gethostbyname(self.upstream_host)
                            except Exception:
                                dst_ip = self.upstream_host
                            dst_port = int(self.upstream_port)
                            if len(frame) >= 2:
                                txid = int.from_bytes(frame[0:2], "big")
                                decode_ctx.state["pending_transactions"][
                                    tx_key(txid, src_ip, src_port, dst_ip, dst_port)
                                ] = ts
                            decoded = decode_modbus(frame, src_ip, src_port, dst_ip, dst_port, ts, decode_ctx)
                            if decoded:
                                self._emit(decoded)
                    else:
                        client_sock.sendall(chunk)
                        rsp_buf.extend(chunk)
                        for frame in _extract_frames(rsp_buf):
                            ts = time.time()
                            try:
                                src_ip = socket.gethostbyname(self.upstream_host)
                            except Exception:
                                src_ip = self.upstream_host
                            src_port = int(self.upstream_port)
                            dst_ip, dst_port = client_addr[0], int(client_addr[1])
                            decoded = decode_modbus(frame, src_ip, src_port, dst_ip, dst_port, ts, decode_ctx)
                            if decoded:
                                self._emit(decoded)
        except Exception as exc:
            self._emit_link_event(
                "LINK_ERROR",
                client_addr,
                f"Proxy connection error {client_addr[0]}:{int(client_addr[1])} -> {self.upstream_host}:{int(self.upstream_port)} | {exc}",
            )
            print(f"[proxy] client handling error: {exc}")
        finally:
            self._emit_link_event(
                "LINK_CLOSE",
                client_addr,
                f"Proxy connection closed {client_addr[0]}:{int(client_addr[1])} -> {self.upstream_host}:{int(self.upstream_port)}",
            )
            try:
                client_sock.close()
            except Exception:
                pass
            if upstream:
                try:
                    upstream.close()
                except Exception:
                    pass

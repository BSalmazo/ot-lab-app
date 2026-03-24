import sys
import subprocess

REQUIRED_PACKAGES = ["requests", "scapy"]

def ensure_dependencies():
    for pkg in REQUIRED_PACKAGES:
        try:
            __import__(pkg)
        except ImportError:
            print(f"[agent] installing missing dependency: {pkg}")
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])

ensure_dependencies()

import argparse
import json
import socket
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from statistics import mean

import requests
from scapy.all import AsyncSniffer, IP, TCP, get_if_list


MODBUS_KNOWN_FUNCTION_CODES = {1, 2, 3, 4, 5, 6, 15, 16}
DEFAULT_SERVER_URL = "https://web-production-56599.up.railway.app/"
DEFAULT_SESSION_ID = "dev-local-session"
DEFAULT_MODE = "MONITORING"
DEFAULT_IFACE = "ALL"

CONFIG_DIR = Path.home() / ".ot_lab_agent"
IDENTITY_FILE = CONFIG_DIR / "identity.json"
INSTALLED_CONFIG_FILE = CONFIG_DIR / "agent_config.json"


def get_executable_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


LOCAL_BUNDLED_CONFIG = get_executable_dir() / "agent-config.json"


def load_or_create_local_identity():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if IDENTITY_FILE.exists():
        try:
            data = json.loads(IDENTITY_FILE.read_text(encoding="utf-8"))
            if data.get("agent_id"):
                return data
        except Exception:
            pass

    identity = {
        "agent_id": str(uuid.uuid4()),
        "created_at": time.time(),
    }
    IDENTITY_FILE.write_text(json.dumps(identity, indent=2), encoding="utf-8")
    return identity


def load_agent_config():
    candidates = [
        LOCAL_BUNDLED_CONFIG,
        INSTALLED_CONFIG_FILE,
    ]

    for path in candidates:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    print(f"[agent] config encontrada em: {path}")
                    print(f"[agent] config lida: {data}")
                    return data
            except Exception as e:
                print(f"[agent] falha ao ler config {path}: {e}")

    print("[agent] nenhuma config encontrada")
    return {}


def recv_exact(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("socket closed while receiving")
        data += chunk
    return data


class SimpleModbusServer:
    def __init__(self, host="127.0.0.1", port=5020, register_count=200):
        self.host = host
        self.port = int(port)
        self.register_count = register_count

        self._thread = None
        self._stop_event = threading.Event()
        self._server_socket = None
        self._lock = threading.Lock()

        self.holding_registers = [0] * register_count
        self._seed_demo_data()

    def _seed_demo_data(self):
        # valores iniciais só para facilitar testes visuais
        if self.register_count >= 8:
            self.holding_registers[0] = 10
            self.holding_registers[1] = 20
            self.holding_registers[2] = 30
            self.holding_registers[3] = 40
            self.holding_registers[4] = 100
            self.holding_registers[5] = 200
            self.holding_registers[6] = 300
            self.holding_registers[7] = 400

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return True

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._serve_loop, daemon=True)
        self._thread.start()
        time.sleep(0.2)
        return self.running

    def stop(self):
        self._stop_event.set()

        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass

        if self._thread:
            self._thread.join(timeout=2)

        self._thread = None
        self._server_socket = None

    def _serve_loop(self):
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((self.host, self.port))
            srv.listen(5)
            srv.settimeout(1.0)
            self._server_socket = srv

            print(f"[modbus-server] listening on {self.host}:{self.port}")

            while not self._stop_event.is_set():
                try:
                    conn, addr = srv.accept()
                    conn.settimeout(2.0)
                    threading.Thread(
                        target=self._handle_client,
                        args=(conn, addr),
                        daemon=True
                    ).start()
                except socket.timeout:
                    continue
                except OSError:
                    break
                except Exception as e:
                    print(f"[modbus-server] accept error: {e}")

        except Exception as e:
            print(f"[modbus-server] failed to start: {e}")
        finally:
            if self._server_socket:
                try:
                    self._server_socket.close()
                except Exception:
                    pass
            self._server_socket = None
            print("[modbus-server] stopped")

    def _handle_client(self, conn: socket.socket, addr):
        try:
            while not self._stop_event.is_set():
                header = conn.recv(7)
                if not header:
                    break
                if len(header) < 7:
                    break

                tx_id = int.from_bytes(header[0:2], "big")
                proto_id = int.from_bytes(header[2:4], "big")
                length = int.from_bytes(header[4:6], "big")
                unit_id = header[6]

                if proto_id != 0 or length < 2:
                    break

                pdu = recv_exact(conn, length - 1)
                function_code = pdu[0]
                data = pdu[1:]

                response_pdu = self._process_request(function_code, data)
                response_mbap = (
                    tx_id.to_bytes(2, "big") +
                    (0).to_bytes(2, "big") +
                    (len(response_pdu) + 1).to_bytes(2, "big") +
                    bytes([unit_id])
                )
                conn.sendall(response_mbap + response_pdu)

        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _exception_response(self, function_code: int, exc_code: int) -> bytes:
        return bytes([function_code | 0x80, exc_code])

    def _process_request(self, function_code: int, data: bytes) -> bytes:
        if function_code == 3:
            if len(data) != 4:
                return self._exception_response(function_code, 3)

            start_addr = int.from_bytes(data[0:2], "big")
            quantity = int.from_bytes(data[2:4], "big")

            if quantity <= 0 or quantity > 125:
                return self._exception_response(function_code, 3)

            end_addr = start_addr + quantity
            if start_addr < 0 or end_addr > len(self.holding_registers):
                return self._exception_response(function_code, 2)

            with self._lock:
                regs = self.holding_registers[start_addr:end_addr]

            payload = b"".join(v.to_bytes(2, "big") for v in regs)
            return bytes([function_code, len(payload)]) + payload

        if function_code == 6:
            if len(data) != 4:
                return self._exception_response(function_code, 3)

            register = int.from_bytes(data[0:2], "big")
            value = int.from_bytes(data[2:4], "big")

            if register < 0 or register >= len(self.holding_registers):
                return self._exception_response(function_code, 2)

            with self._lock:
                self.holding_registers[register] = value

            return bytes([function_code]) + data

        return self._exception_response(function_code, 1)


class SimpleModbusClient:
    def __init__(self, host="127.0.0.1", port=5020, poll_interval=1.0, poll_start=0, poll_quantity=4):
        self.host = host
        self.port = int(port)
        self.poll_interval = float(poll_interval)
        self.poll_start = int(poll_start)
        self.poll_quantity = int(poll_quantity)

        self._thread = None
        self._stop_event = threading.Event()
        self._tx_id = 1
        self.last_values = []
        self.last_error = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return True

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        time.sleep(0.2)
        return self.running

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None

    def update_config(self, host, port, poll_interval, poll_start, poll_quantity):
        self.host = host
        self.port = int(port)
        self.poll_interval = float(poll_interval)
        self.poll_start = int(poll_start)
        self.poll_quantity = int(poll_quantity)

    def _next_tx_id(self):
        tx = self._tx_id
        self._tx_id = (self._tx_id + 1) % 65536
        if self._tx_id == 0:
            self._tx_id = 1
        return tx

    def _poll_loop(self):
        print(
            f"[modbus-client] polling {self.host}:{self.port} "
            f"start={self.poll_start} qty={self.poll_quantity} every {self.poll_interval}s"
        )

        while not self._stop_event.is_set():
            try:
                values = self._read_holding_registers(
                    host=self.host,
                    port=self.port,
                    start_addr=self.poll_start,
                    quantity=self.poll_quantity,
                )
                self.last_values = values
                self.last_error = None
            except Exception as e:
                self.last_error = str(e)
                print(f"[modbus-client] poll error: {e}")

            sleep_step = 0.1
            waited = 0.0
            while waited < self.poll_interval and not self._stop_event.is_set():
                time.sleep(sleep_step)
                waited += sleep_step

        print("[modbus-client] stopped")

    def _read_holding_registers(self, host, port, start_addr, quantity):
        tx_id = self._next_tx_id()
        unit_id = 1
        function_code = 3

        pdu = bytes([function_code]) + start_addr.to_bytes(2, "big") + quantity.to_bytes(2, "big")
        mbap = (
            tx_id.to_bytes(2, "big") +
            (0).to_bytes(2, "big") +
            (len(pdu) + 1).to_bytes(2, "big") +
            bytes([unit_id])
        )

        with socket.create_connection((host, port), timeout=2.0) as sock:
            sock.sendall(mbap + pdu)

            resp_header = recv_exact(sock, 7)
            resp_tx_id = int.from_bytes(resp_header[0:2], "big")
            resp_proto_id = int.from_bytes(resp_header[2:4], "big")
            resp_len = int.from_bytes(resp_header[4:6], "big")
            _resp_unit_id = resp_header[6]

            if resp_tx_id != tx_id or resp_proto_id != 0:
                raise ValueError("invalid Modbus response header")

            resp_pdu = recv_exact(sock, resp_len - 1)
            fc = resp_pdu[0]

            if fc & 0x80:
                exc_code = resp_pdu[1] if len(resp_pdu) > 1 else -1
                raise ValueError(f"modbus exception code={exc_code}")

            if fc != function_code:
                raise ValueError(f"unexpected function code: {fc}")

            byte_count = resp_pdu[1]
            data = resp_pdu[2:2 + byte_count]

            values = []
            for i in range(0, len(data), 2):
                if i + 1 < len(data):
                    values.append(int.from_bytes(data[i:i + 2], "big"))

            return values


class AgentMonitor:
    def __init__(
        self,
        iface=DEFAULT_IFACE,
        mode=DEFAULT_MODE,
        server_url=DEFAULT_SERVER_URL,
        session_id=DEFAULT_SESSION_ID,
    ):
        self.iface = iface
        self.mode = mode
        self.server_url = server_url.rstrip("/")
        self.session_id = session_id
        self.sniffer = None

        self.identity = load_or_create_local_identity()
        self.agent_id = self.identity["agent_id"]
        self.hostname = socket.gethostname()

        self.min_samples = 3
        self.period_deviation_threshold = 0.20
        self.max_timestamps = 20

        self.state = self._empty_state()
        self.runtime_lock = threading.Lock()

        self.modbus_server = None
        self.modbus_client = None

        self.server_runtime = {
            "running": False,
            "host": "127.0.0.1",
            "port": 5020,
        }
        self.client_runtime = {
            "running": False,
            "host": "127.0.0.1",
            "port": 5020,
            "poll_interval": 1.0,
            "poll_start": 0,
            "poll_quantity": 4,
        }

        self.last_applied_config = {
            "iface": iface,
            "mode": mode,
        }

    def _empty_state(self):
        return {
            "function_codes_seen": set(),
            "initiators_seen": set(),
            "responders_seen": set(),
            "read_patterns": {},
            "write_registers": {},
            "pending_transactions": {},
            "event_counts": defaultdict(int),
        }

    def fetch_remote_config(self):
        try:
            response = requests.get(
                f"{self.server_url}/api/agent/config",
                params={"session_id": self.session_id},
                timeout=2,
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                return None
            return data.get("config")
        except Exception:
            return None

    def fetch_pending_commands(self):
        try:
            response = requests.get(
                f"{self.server_url}/api/agent/commands",
                params={"session_id": self.session_id},
                timeout=2,
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                return []
            return data.get("commands", [])
        except Exception as e:
            print(f"[agent] failed to fetch commands: {e}")
            return []

    def process_pending_commands(self):
        commands = self.fetch_pending_commands()
        for cmd in commands:
            cmd_type = cmd.get("type")
            payload = cmd.get("payload", {}) or {}
            print(f"[agent] processing command {cmd_type} {payload}")

            try:
                if cmd_type == "START_SERVER":
                    self.start_modbus_server(
                        host=payload.get("host", self.server_runtime["host"]),
                        port=int(payload.get("port", self.server_runtime["port"])),
                    )
                elif cmd_type == "STOP_SERVER":
                    self.stop_modbus_server()
                elif cmd_type == "START_CLIENT":
                    self.start_modbus_client(
                        host=payload.get("host", self.client_runtime["host"]),
                        port=int(payload.get("port", self.client_runtime["port"])),
                        poll_interval=float(payload.get("poll_interval", self.client_runtime["poll_interval"])),
                        poll_start=int(payload.get("poll_start", self.client_runtime["poll_start"])),
                        poll_quantity=int(payload.get("poll_quantity", self.client_runtime["poll_quantity"])),
                    )
                elif cmd_type == "STOP_CLIENT":
                    self.stop_modbus_client()
            except Exception as e:
                print(f"[agent] command {cmd_type} failed: {e}")

        if commands:
            self.send_runtime_update()

    def start_modbus_server(self, host, port):
        self.stop_modbus_server()

        server = SimpleModbusServer(host=host, port=port)
        started = server.start()

        with self.runtime_lock:
            self.modbus_server = server if started else None
            self.server_runtime["host"] = host
            self.server_runtime["port"] = int(port)
            self.server_runtime["running"] = bool(started)

        print(f"[agent] modbus server start result: running={started} host={host} port={port}")

    def stop_modbus_server(self):
        with self.runtime_lock:
            server = self.modbus_server
            self.modbus_server = None

        if server:
            server.stop()

        with self.runtime_lock:
            self.server_runtime["running"] = False

        print("[agent] modbus server stopped")

    def start_modbus_client(self, host, port, poll_interval, poll_start, poll_quantity):
        self.stop_modbus_client()

        client = SimpleModbusClient(
            host=host,
            port=port,
            poll_interval=poll_interval,
            poll_start=poll_start,
            poll_quantity=poll_quantity,
        )
        started = client.start()

        with self.runtime_lock:
            self.modbus_client = client if started else None
            self.client_runtime["host"] = host
            self.client_runtime["port"] = int(port)
            self.client_runtime["poll_interval"] = float(poll_interval)
            self.client_runtime["poll_start"] = int(poll_start)
            self.client_runtime["poll_quantity"] = int(poll_quantity)
            self.client_runtime["running"] = bool(started)

        print(
            f"[agent] modbus client start result: running={started} "
            f"host={host} port={port} poll_interval={poll_interval}"
        )

    def stop_modbus_client(self):
        with self.runtime_lock:
            client = self.modbus_client
            self.modbus_client = None

        if client:
            client.stop()

        with self.runtime_lock:
            self.client_runtime["running"] = False

        print("[agent] modbus client stopped")

    def send_runtime_update(self):
        with self.runtime_lock:
            payload = {
                "session_id": self.session_id,
                "server": dict(self.server_runtime),
                "client": dict(self.client_runtime),
            }
        self._post("/api/agent/runtime", payload)

    def apply_config_if_needed(self, config: dict):
        if not config:
            return

        new_iface = config.get("iface", self.iface)
        new_mode = config.get("mode", self.mode)

        changed = (
            new_iface != self.iface or
            new_mode != self.mode
        )

        if not changed:
            return

        available = self.get_available_interfaces()
        if new_iface != "ALL" and new_iface not in available:
            print(f"[agent] ignoring invalid remote iface '{new_iface}'. available={available}")
            return

        was_running = self.sniffer is not None

        if was_running:
            self.stop()

        self.iface = new_iface
        self.mode = new_mode
        self.last_applied_config = {
            "iface": self.iface,
            "mode": self.mode,
        }

        self.reset_state()

        if was_running:
            started = self.start()
            if not started:
                print(f"[agent] could not restart sniffer on iface={self.iface}")

        print(f"[agent] applied remote config iface={self.iface} mode={self.mode}")

    def reset_state(self):
        self.state = self._empty_state()

    def snapshot(self):
        read_patterns = []
        for (server_ip, server_port, start, qty), profile in self.state["read_patterns"].items():
            read_patterns.append({
                "server": f"{server_ip}:{server_port}",
                "start": start,
                "quantity": qty,
                "count": profile["count"],
                "avg_period": profile["avg_period"],
            })

        write_registers = []
        for reg, profile in self.state["write_registers"].items():
            write_registers.append({
                "register": reg,
                "count": profile["count"],
                "last_value": profile["last_value"],
                "values_seen": sorted(profile["values_seen"]),
            })

        return {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "mode": self.mode,
            "iface": self.iface,
            "hostname": self.hostname,
            "function_codes_seen": sorted(self.state["function_codes_seen"]),
            "initiators_seen": sorted(self.state["initiators_seen"]),
            "responders_seen": sorted(self.state["responders_seen"]),
            "read_patterns": read_patterns,
            "write_registers": write_registers,
            "event_counts": dict(self.state["event_counts"]),
            "traffic_overview": {
                "clients_identified": len(self.state["initiators_seen"]),
                "servers_identified": len(self.state["responders_seen"]),
                "function_codes_identified": sorted(self.state["function_codes_seen"]),
                "read_pattern_count": len(read_patterns),
                "write_register_count": len(write_registers),
            },
            "timestamp": time.time(),
            "available_ifaces": self.get_available_interfaces(),
        }

    def register(self):
        payload = {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "hostname": self.hostname,
            "iface": self.iface,
            "mode": self.mode,
            "running": False,
            "timestamp": time.time(),
            "available_ifaces": self.get_available_interfaces(),
        }
        self._post("/api/agent/register", payload)

    def start(self):
        sniff_ifaces = self.get_sniff_interfaces()

        if not sniff_ifaces:
            print(f"[agent] invalid iface '{self.iface}'. available={self.get_available_interfaces()}")
            self.sniffer = None
            return False

        blocked_prefixes = ("anpi", "ap", "awdl", "llw", "utun", "bridge", "gif", "stf")
        sniff_ifaces = [
            iface for iface in sniff_ifaces
            if not iface.startswith(blocked_prefixes)
        ]

        if not sniff_ifaces:
            print("[agent] no usable interfaces after filtering")
            self.sniffer = None
            return False

        try:
            self.sniffer = AsyncSniffer(
                iface=sniff_ifaces if len(sniff_ifaces) > 1 else sniff_ifaces[0],
                filter="tcp",
                prn=self._handle_packet,
                store=False,
                promisc=False,
            )
            self.sniffer.start()
            print(f"[agent] sniffing on interfaces: {sniff_ifaces}")
            return True
        except Exception as e:
            print(f"[agent] failed to start sniffer on iface={self.iface}: {e}")
            self.sniffer = None
            return False

    def stop(self):
        if self.sniffer is not None:
            try:
                self.sniffer.stop()
            except Exception as e:
                print(f"[agent] warning while stopping sniffer: {e}")
            self.sniffer = None

    def get_sniff_interfaces(self):
        available = self.get_available_interfaces()

        if self.iface == "ALL":
            return available

        if self.iface in available:
            return [self.iface]

        return []

    def send_heartbeat(self):
        with self.runtime_lock:
            self.server_runtime["running"] = bool(self.modbus_server and self.modbus_server.running)
            self.client_runtime["running"] = bool(self.modbus_client and self.modbus_client.running)

        payload = {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "hostname": self.hostname,
            "iface": self.iface,
            "mode": self.mode,
            "running": self.sniffer is not None,
            "timestamp": time.time(),
            "available_ifaces": self.get_available_interfaces(),
        }
        self._post("/api/agent/heartbeat", payload)
        self.send_runtime_update()

    def send_snapshot(self):
        self._post("/api/agent/snapshot", self.snapshot())

    def _post(self, path: str, payload: dict):
        try:
            requests.post(f"{self.server_url}{path}", json=payload, timeout=2)
        except Exception:
            pass

    def _looks_like_modbus_tcp(self, payload: bytes) -> bool:
        if len(payload) < 8:
            return False

        protocol_id = int.from_bytes(payload[2:4], "big")
        length_field = int.from_bytes(payload[4:6], "big")
        function_code = payload[7]

        if protocol_id != 0:
            return False
        if length_field < 2:
            return False

        expected_total_length = 6 + length_field
        if expected_total_length != len(payload):
            return False

        if function_code not in MODBUS_KNOWN_FUNCTION_CODES and (function_code & 0x80) == 0:
            return False

        return True

    def _tx_key(self, tx_id, src_ip, src_port, dst_ip, dst_port):
        return (tx_id, src_ip, src_port, dst_ip, dst_port)

    def _reverse_tx_key(self, tx_id, src_ip, src_port, dst_ip, dst_port):
        return (tx_id, dst_ip, dst_port, src_ip, src_port)

    def _build_event_summary(self, event: dict) -> str:
        event_type = event.get("type")
        client = event.get("client") or f"{event.get('src_ip')}:{event.get('src_port')}"
        server = event.get("server") or f"{event.get('dst_ip')}:{event.get('dst_port')}"

        if event_type == "READ_REQUEST":
            return (
                f"FC{event.get('function_code')} read request from {client} "
                f"to {server} | start={event.get('start_addr')} qty={event.get('quantity')}"
            )

        if event_type == "READ_RESPONSE":
            return (
                f"FC{event.get('function_code')} read response from {server} "
                f"to {client} | values={event.get('register_values', [])} | rtt={event.get('rtt')}"
            )

        if event_type == "WRITE_REQUEST":
            return (
                f"FC{event.get('function_code')} write request from {client} "
                f"to {server} | register={event.get('register')} value={event.get('value')}"
            )

        if event_type == "WRITE_RESPONSE":
            return (
                f"FC{event.get('function_code')} write response from {server} "
                f"to {client} | register={event.get('register')} value={event.get('value')} | rtt={event.get('rtt')}"
            )

        return (
            f"Modbus transaction detected | "
            f"{event.get('src_ip')}:{event.get('src_port')} -> {event.get('dst_ip')}:{event.get('dst_port')}"
        )

    def _decode_modbus(self, payload: bytes, src_ip: str, src_port: int, dst_ip: str, dst_port: int, timestamp: float):
        tx_id = int.from_bytes(payload[0:2], "big")
        protocol_id = int.from_bytes(payload[2:4], "big")
        length = int.from_bytes(payload[4:6], "big")
        unit_id = payload[6]
        function_code = payload[7]

        if protocol_id != 0:
            return None

        reverse_key = self._reverse_tx_key(tx_id, src_ip, src_port, dst_ip, dst_port)
        is_response = reverse_key in self.state["pending_transactions"]

        decoded = {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "timestamp": timestamp,
            "src_ip": src_ip,
            "src_port": src_port,
            "dst_ip": dst_ip,
            "dst_port": dst_port,
            "client": f"{src_ip}:{src_port}" if not is_response else f"{dst_ip}:{dst_port}",
            "server": f"{dst_ip}:{dst_port}" if not is_response else f"{src_ip}:{src_port}",
            "direction": "response" if is_response else "request",
            "transaction_id": tx_id,
            "function_code": function_code,
            "unit_id": unit_id,
            "length": length,
            "protocol": "MODBUS/TCP",
        }

        if function_code == 3:
            if len(payload) == 12 and not is_response:
                start_addr = int.from_bytes(payload[8:10], "big")
                quantity = int.from_bytes(payload[10:12], "big")
                decoded.update({
                    "type": "READ_REQUEST",
                    "start_addr": start_addr,
                    "quantity": quantity,
                })
                return decoded

            if len(payload) >= 9:
                byte_count = payload[8]
                data_bytes = payload[9:9 + byte_count]
                regs = []
                for i in range(0, len(data_bytes), 2):
                    if i + 1 < len(data_bytes):
                        regs.append(int.from_bytes(data_bytes[i:i + 2], "big"))
                decoded.update({
                    "type": "READ_RESPONSE",
                    "register_values": regs,
                })
                return decoded

        if function_code == 6 and len(payload) == 12:
            register = int.from_bytes(payload[8:10], "big")
            value = int.from_bytes(payload[10:12], "big")
            decoded.update({
                "register": register,
                "value": value,
                "type": "WRITE_RESPONSE" if is_response else "WRITE_REQUEST",
            })
            return decoded

        return None

    def _get_or_create_read_pattern(self, key):
        if key not in self.state["read_patterns"]:
            self.state["read_patterns"][key] = {
                "count": 0,
                "timestamps": deque(maxlen=self.max_timestamps),
                "avg_period": None,
            }
        return self.state["read_patterns"][key]

    def _get_or_create_write_register(self, register: int):
        if register not in self.state["write_registers"]:
            self.state["write_registers"][register] = {
                "count": 0,
                "values_seen": set(),
                "last_value": None,
            }
        return self.state["write_registers"][register]
    
    def _get_avg_polling_for_event(self, decoded: dict):
        if decoded.get("type") != "READ_REQUEST":
            return None

        key = (
            decoded["dst_ip"],
            decoded["dst_port"],
            decoded["start_addr"],
            decoded["quantity"],
        )

        profile = self.state["read_patterns"].get(key)
        if not profile:
            return None

        avg_period = profile.get("avg_period")
        if avg_period is None:
            return None

        try:
            return round(float(avg_period), 2)
        except (TypeError, ValueError):
            return None

    def _should_emit_alert(self, event: dict, reasons: list, score: int) -> bool:
        event_type = event.get("type")
        function_code = event.get("function_code")

        if event_type in ("WRITE_REQUEST", "WRITE_RESPONSE"):
            return True

        if function_code not in MODBUS_KNOWN_FUNCTION_CODES:
            return True

        if score >= 8:
            return True

        return False

    def _emit_alert(self, event: dict, reasons: list, score: int):
        if not self._should_emit_alert(event, reasons, score):
            return

        if score >= 8:
            severity = "CRITICAL"
        elif score >= 5:
            severity = "ALERT"
        elif score >= 3:
            severity = "NOTICE"
        else:
            severity = "INFO"

        alert = {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "timestamp": event["timestamp"],
            "severity": severity,
            "score": score,
            "reasons": reasons,
            "event_type": event["type"],
            "src": f"{event['src_ip']}:{event['src_port']}",
            "dst": f"{event['dst_ip']}:{event['dst_port']}",
            "summary": self._build_event_summary(event),
        }
        self._post("/api/agent/alert", alert)

    def _handle_packet(self, pkt):
        if IP not in pkt or TCP not in pkt:
            return

        ip = pkt[IP]
        tcp = pkt[TCP]
        payload = bytes(tcp.payload)

        if not payload or not self._looks_like_modbus_tcp(payload):
            return

        decoded = self._decode_modbus(
            payload=payload,
            src_ip=ip.src,
            src_port=tcp.sport,
            dst_ip=ip.dst,
            dst_port=tcp.dport,
            timestamp=float(pkt.time),
        )
        if not decoded:
            return

        event = decoded.copy()
        score = 0
        reasons = []

        if decoded["type"] in ("READ_REQUEST", "WRITE_REQUEST"):
            initiator = f"{decoded['src_ip']}:{decoded['src_port']}"
            responder = f"{decoded['dst_ip']}:{decoded['dst_port']}"

            self.state["initiators_seen"].add(initiator)
            self.state["responders_seen"].add(responder)
            self.state["function_codes_seen"].add(decoded["function_code"])

            if decoded["type"] == "READ_REQUEST":
                key = (
                    decoded["dst_ip"],
                    decoded["dst_port"],
                    decoded["start_addr"],
                    decoded["quantity"],
                )
                profile = self._get_or_create_read_pattern(key)
                profile["count"] += 1
                profile["timestamps"].append(decoded["timestamp"])

                if len(profile["timestamps"]) >= self.min_samples:
                    deltas = [
                        profile["timestamps"][i] - profile["timestamps"][i - 1]
                        for i in range(1, len(profile["timestamps"]))
                    ]
                    profile["avg_period"] = mean(deltas)

                self.state["pending_transactions"][
                    self._tx_key(
                        decoded["transaction_id"],
                        decoded["src_ip"],
                        decoded["src_port"],
                        decoded["dst_ip"],
                        decoded["dst_port"],
                    )
                ] = {"timestamp": decoded["timestamp"]}

            elif decoded["type"] == "WRITE_REQUEST":
                reg_profile = self._get_or_create_write_register(decoded["register"])
                reg_profile["count"] += 1
                reg_profile["values_seen"].add(decoded["value"])
                reg_profile["last_value"] = decoded["value"]

                reasons.append(
                    f"Write detected on register {decoded['register']} with value {decoded['value']}"
                )
                score = 5

                self.state["pending_transactions"][
                    self._tx_key(
                        decoded["transaction_id"],
                        decoded["src_ip"],
                        decoded["src_port"],
                        decoded["dst_ip"],
                        decoded["dst_port"],
                    )
                ] = {"timestamp": decoded["timestamp"]}

        elif decoded["type"] in ("READ_RESPONSE", "WRITE_RESPONSE"):
            reverse_key = self._reverse_tx_key(
                decoded["transaction_id"],
                decoded["src_ip"],
                decoded["src_port"],
                decoded["dst_ip"],
                decoded["dst_port"],
            )
            matched = self.state["pending_transactions"].pop(reverse_key, None)
            event["rtt"] = round(decoded["timestamp"] - matched["timestamp"], 6) if matched else None

            if decoded["type"] == "WRITE_RESPONSE":
                reasons.append(
                    f"Write confirmation received for register {decoded.get('register')} value {decoded.get('value')}"
                )
                score = 4

        event["iface"] = self.iface
        event["avg_polling_s"] = self._get_avg_polling_for_event(decoded)
        event["summary"] = self._build_event_summary(event)

        self.state["event_counts"][decoded["type"]] += 1
        self._post("/api/agent/event", event)

        if self.mode == "MONITORING":
            self._emit_alert(event, reasons, score)

        self.send_snapshot()

    def get_available_interfaces(self):
        try:
            ifaces = sorted(set(get_if_list()))
            return [iface for iface in ifaces if iface]
        except Exception:
            return []


def main():
    bundled_config = load_agent_config()

    parser = argparse.ArgumentParser(description="OT Lab Agent")
    parser.add_argument("--server", default=bundled_config.get("server_url") or DEFAULT_SERVER_URL)
    parser.add_argument("--session-id", default=bundled_config.get("session_id") or DEFAULT_SESSION_ID)
    parser.add_argument("--iface", default=bundled_config.get("iface") or DEFAULT_IFACE)
    parser.add_argument("--mode", default=bundled_config.get("mode") or DEFAULT_MODE, choices=["LEARNING", "MONITORING"])
    args = parser.parse_args()

    print("\n=== OT LAB AGENT ===")
    print("Interfaces disponíveis:", ", ".join(sorted(set(get_if_list()))))
    print(f"Interface pedida: {args.iface}")

    if bundled_config:
        print("[agent] config carregada de ficheiro")

    agent = AgentMonitor(
        iface=args.iface,
        mode=args.mode,
        server_url=args.server,
        session_id=args.session_id,
    )

    if agent.iface != "ALL" and agent.iface not in agent.get_available_interfaces():
        available = agent.get_available_interfaces()
        if available:
            agent.iface = available[0]
            print(f"[agent] iface inválida no arranque. fallback automático para: {agent.iface}")

    agent.register()
    started = agent.start()

    if not started:
        print(f"[agent] started without active sniffing. iface={agent.iface}")

    agent.send_runtime_update()

    print(
        f"[agent] session={agent.session_id} id={agent.agent_id} "
        f"iface={agent.iface} mode={agent.mode} -> {args.server}"
    )

    try:
        while True:
            remote_config = agent.fetch_remote_config()
            agent.apply_config_if_needed(remote_config)
            agent.process_pending_commands()
            agent.send_heartbeat()
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[agent] stopping...")
    finally:
        agent.stop_modbus_client()
        agent.stop_modbus_server()
        agent.stop()
        agent.send_runtime_update()


if __name__ == "__main__":
    main()
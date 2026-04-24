import socket
import threading
import time
from collections import defaultdict, deque

from scapy.all import get_if_list

from .config import (
    DEFAULT_CUSTOM_PORTS,
    DEFAULT_IFACE,
    DEFAULT_MODE,
    DEFAULT_PORT_MODE,
    DEFAULT_SERVER_URL,
    DEFAULT_SESSION_ID,
    build_arg_parser,
    load_agent_config,
    ensure_npcap_installed,
)
from .http_client import HttpClientMixin
from .identity import load_or_create_local_identity
from .protocols.modbus.modbus_builder import build_modbus_tcp_request
from .protocols.modbus.modbus_definitions import get_modbus_function_label
from .protocols.modbus.modbus_validators import (
    ValidationError as ModbusValidationError,
    validate_modbus_action_payload,
)
from .runtime import SimpleModbusClient, SimpleModbusServer
from .sniffer import SnifferMixin


class AgentMonitor(HttpClientMixin, SnifferMixin):
    def __init__(
        self,
        iface=DEFAULT_IFACE,
        mode=DEFAULT_MODE,
        port_mode=DEFAULT_PORT_MODE,
        custom_ports=None,
        server_url=DEFAULT_SERVER_URL,
        session_id=DEFAULT_SESSION_ID,
    ):
        # Garante que NPCAP/libpcap está instalado antes de iniciar
        ensure_npcap_installed()
        
        self.iface = iface
        self.mode = mode
        self.port_mode = port_mode
        self.custom_ports = self.parse_custom_ports(custom_ports)
        self.server_url = server_url.rstrip("/")
        self.session_id = session_id
        self.sniffer = None

        self.identity = load_or_create_local_identity()
        self.agent_id = self.identity["agent_id"]
        self.hostname = socket.gethostname()

        self.min_samples = 3
        self.period_deviation_threshold = 0.20
        self.max_timestamps = 20
        self.snapshot_interval_s = 1.0
        self._last_snapshot_sent_at = 0.0

        self.state = self._empty_state()
        self.runtime_lock = threading.Lock()

        self.modbus_server = None
        self.modbus_client = None
        self.process_modbus_server = None
        self.process_modbus_client = None

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
        self.process_sim_runtime = {
            "running": False,
            "process_type": "tank_v1",
            "server": {
                "running": False,
                "host": "127.0.0.1",
                "port": 15020,
                "registers_preview": {"start": 0, "quantity": 0, "values": []},
            },
            "client": {
                "running": False,
                "host": "127.0.0.1",
                "port": 15020,
                "poll_interval": 0.5,
                "poll_start": 0,
                "poll_quantity": 16,
                "last_values": [],
                "last_error": None,
                "last_poll_at": None,
                "last_success_at": None,
            },
        }

        self.last_applied_config = {
            "iface": iface,
            "mode": mode,
            "port_mode": self.port_mode,
            "custom_ports": list(self.custom_ports),
        }
        self.capabilities = [
            "modbus_actions_v1",
            "run_modbus_action_command",
            "process_sim_v1",
        ]

    @staticmethod
    def parse_custom_ports(value):
        if value is None:
            return list(DEFAULT_CUSTOM_PORTS)

        if isinstance(value, str):
            items = value.replace(";", ",").split(",")
        elif isinstance(value, list):
            items = value
        else:
            items = [value]

        ports = []
        seen = set()
        for raw in items:
            token = str(raw).strip()
            if not token:
                continue
            try:
                port = int(token)
            except (TypeError, ValueError):
                continue
            if port < 1 or port > 65535:
                continue
            if port in seen:
                continue
            seen.add(port)
            ports.append(port)
        return ports

    def make_timestamps_deque(self):
        return deque(maxlen=self.max_timestamps)

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

    def process_pending_commands(self):
        commands = self.fetch_pending_commands()
        for cmd in commands:
            cmd_id = cmd.get("id")
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
                elif cmd_type == "RUN_MODBUS_ACTION":
                    result_msg = self.execute_modbus_action(payload)
                    if cmd_id:
                        self.send_command_result(cmd_id, "done", result_msg)
                elif cmd_type == "START_PROCESS_SIM":
                    self.start_process_sim(
                        host=payload.get("host", self.process_sim_runtime["server"]["host"]),
                        port=int(payload.get("port", self.process_sim_runtime["server"]["port"])),
                        poll_interval=float(payload.get("poll_interval", self.process_sim_runtime["client"]["poll_interval"])),
                        poll_start=int(payload.get("poll_start", self.process_sim_runtime["client"]["poll_start"])),
                        poll_quantity=int(payload.get("poll_quantity", self.process_sim_runtime["client"]["poll_quantity"])),
                        process_type=payload.get("process_type", self.process_sim_runtime["process_type"]),
                    )
                elif cmd_type == "STOP_PROCESS_SIM":
                    self.stop_process_sim()
                elif cmd_type == "WRITE_PROCESS_SIM":
                    self.write_process_register(
                        address=int(payload.get("address")),
                        value=int(payload.get("value")),
                        unit_id=int(payload.get("unit_id", 1)),
                    )
                else:
                    raise RuntimeError(f"unknown command: {cmd_type}")
                if cmd_type != "RUN_MODBUS_ACTION" and cmd_id:
                    self.send_command_result(cmd_id, "done", f"{cmd_type} executed")
            except Exception as e:
                print(f"[agent] command {cmd_type} failed: {e}")
                if cmd_id:
                    self.send_command_result(cmd_id, "error", str(e))

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

    @staticmethod
    def _recv_exact(sock: socket.socket, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError("socket closed while receiving")
            data += chunk
        return data

    def _modbus_write_single_register(
        self, host: str, port: int, register: int, value: int, unit_id: int = 1, timeout_s: float = 2.0
    ):
        tx_id = int(time.time() * 1000) & 0xFFFF
        function_code = 6
        pdu = bytes([function_code]) + int(register).to_bytes(2, "big") + int(value).to_bytes(2, "big")
        mbap = (
            tx_id.to_bytes(2, "big")
            + (0).to_bytes(2, "big")
            + (len(pdu) + 1).to_bytes(2, "big")
            + int(unit_id).to_bytes(1, "big")
        )

        with socket.create_connection((host, int(port)), timeout=timeout_s) as conn:
            conn.settimeout(timeout_s)
            conn.sendall(mbap + pdu)
            resp_header = self._recv_exact(conn, 7)
            resp_tx_id = int.from_bytes(resp_header[0:2], "big")
            resp_proto_id = int.from_bytes(resp_header[2:4], "big")
            resp_len = int.from_bytes(resp_header[4:6], "big")
            if resp_tx_id != tx_id or resp_proto_id != 0:
                raise RuntimeError("invalid Modbus response header")
            resp_pdu = self._recv_exact(conn, resp_len - 1)
            if len(resp_pdu) < 2:
                raise RuntimeError("short Modbus response")
            fc = resp_pdu[0]
            if fc & 0x80:
                exc_code = resp_pdu[1]
                raise RuntimeError(f"modbus exception code={exc_code}")
            if fc != function_code:
                raise RuntimeError(f"unexpected function code in response: {fc}")

    def get_process_sim_snapshot(self):
        with self.runtime_lock:
            server_ref = self.process_modbus_server
            client_ref = self.process_modbus_client
            runtime = dict(self.process_sim_runtime)
            runtime["server"] = dict(self.process_sim_runtime.get("server") or {})
            runtime["client"] = dict(self.process_sim_runtime.get("client") or {})

        server_running = bool(server_ref and server_ref.running)
        client_running = bool(client_ref and client_ref.running)
        runtime["server"]["running"] = server_running
        runtime["client"]["running"] = client_running
        runtime["running"] = server_running and client_running

        if server_running:
            try:
                runtime["server"]["registers_preview"] = server_ref.get_registers_preview(start=0, quantity=16)
            except Exception:
                pass
        if client_running:
            try:
                snap = client_ref.get_snapshot()
                runtime["client"]["last_values"] = list(snap.get("last_values") or [])
                runtime["client"]["last_error"] = snap.get("last_error")
                runtime["client"]["last_poll_at"] = snap.get("last_poll_at")
                runtime["client"]["last_success_at"] = snap.get("last_success_at")
            except Exception:
                pass
        return runtime

    def start_process_sim(self, host, port, poll_interval, poll_start, poll_quantity, process_type):
        process_type = str(process_type or "tank_v1").strip() or "tank_v1"
        if process_type != "tank_v1":
            raise RuntimeError("unsupported process_type")

        self.stop_process_sim()

        server = SimpleModbusServer(host=host, port=port)
        server_started = server.start()
        if not server_started:
            try:
                server.stop()
            except Exception:
                pass
            raise RuntimeError("failed to start process simulation server")

        client = SimpleModbusClient(
            host=host,
            port=port,
            poll_interval=poll_interval,
            poll_start=poll_start,
            poll_quantity=poll_quantity,
        )
        client_started = client.start()
        if not client_started:
            try:
                client.stop()
            except Exception:
                pass
            try:
                server.stop()
            except Exception:
                pass
            raise RuntimeError("failed to start process simulation client")

        with self.runtime_lock:
            self.process_modbus_server = server
            self.process_modbus_client = client
            self.process_sim_runtime["process_type"] = process_type
            self.process_sim_runtime["server"].update(
                {
                    "running": True,
                    "host": str(host),
                    "port": int(port),
                }
            )
            self.process_sim_runtime["client"].update(
                {
                    "running": True,
                    "host": str(host),
                    "port": int(port),
                    "poll_interval": float(poll_interval),
                    "poll_start": int(poll_start),
                    "poll_quantity": int(poll_quantity),
                }
            )
            self.process_sim_runtime["running"] = True

        print(
            "[agent] process simulation started "
            f"host={host} port={port} poll={poll_interval}s start={poll_start} qty={poll_quantity}"
        )

    def stop_process_sim(self):
        with self.runtime_lock:
            server = self.process_modbus_server
            client = self.process_modbus_client
            self.process_modbus_server = None
            self.process_modbus_client = None

        if client:
            try:
                client.stop()
            except Exception:
                pass
        if server:
            try:
                server.stop()
            except Exception:
                pass

        with self.runtime_lock:
            self.process_sim_runtime["running"] = False
            self.process_sim_runtime["server"]["running"] = False
            self.process_sim_runtime["client"]["running"] = False

        print("[agent] process simulation stopped")

    def write_process_register(self, address: int, value: int, unit_id: int = 1):
        snapshot = self.get_process_sim_snapshot()
        if not snapshot.get("running"):
            raise RuntimeError("process simulation is not running")
        host = str(snapshot["server"]["host"])
        port = int(snapshot["server"]["port"])
        self._modbus_write_single_register(host=host, port=port, register=int(address), value=int(value), unit_id=int(unit_id))
        print(f"[agent] process write HR{address}={value} (unit={unit_id})")

    def execute_modbus_action(self, payload: dict):
        try:
            function_def, normalized = validate_modbus_action_payload(payload)
        except ModbusValidationError as exc:
            raise RuntimeError(f"invalid modbus action payload: {exc}") from exc

        built = build_modbus_tcp_request(
            function_def,
            normalized,
            transaction_id=int(time.time() * 1000) & 0xFFFF,
        )

        host = normalized["host"]
        port = normalized["port"]
        timeout_s = 2.0

        print(
            f"[agent] executing action {function_def['code_label']} {function_def['name']} "
            f"to {host}:{port} unit={built['unit_id']}"
        )

        try:
            with socket.create_connection((host, port), timeout=timeout_s) as conn:
                conn.settimeout(timeout_s)
                conn.sendall(built["request_bytes"])
                response = conn.recv(512)
        except Exception as exc:
            raise RuntimeError(f"modbus action send failed: {type(exc).__name__}: {exc}") from exc

        if not response:
            return "Request sent, no response received"

        if len(response) >= 8:
            function_code = response[7]
            if function_code & 0x80 and len(response) >= 9:
                response_message = (
                    f"Exception response on FC{function_code & 0x7F} (code={response[8]})"
                )
                print(f"[agent] {response_message} raw={response.hex(' ').upper()}")
                return response_message
            else:
                response_message = f"Response received for FC{function_code}"
                print(f"[agent] {response_message} raw={response.hex(' ').upper()}")
                return response_message
        else:
            return "Short response received"

    def apply_config_if_needed(self, config: dict):
        if not config:
            return

        new_iface = config.get("iface", self.iface)
        new_mode = config.get("mode", self.mode)
        new_port_mode = config.get("port_mode", self.port_mode)
        new_custom_ports = self.parse_custom_ports(config.get("custom_ports", self.custom_ports))

        if new_port_mode not in {"ALL_PORTS", "MODBUS_PORTS", "CUSTOM"}:
            print(f"[agent] ignoring invalid remote port_mode '{new_port_mode}'")
            return
        if new_port_mode == "CUSTOM" and not new_custom_ports:
            print("[agent] ignoring remote CUSTOM port_mode without custom_ports")
            return

        changed = (
            new_iface != self.iface or
            new_mode != self.mode or
            new_port_mode != self.port_mode or
            new_custom_ports != self.custom_ports
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
        self.port_mode = new_port_mode
        self.custom_ports = new_custom_ports
        self.last_applied_config = {
            "iface": self.iface,
            "mode": self.mode,
            "port_mode": self.port_mode,
            "custom_ports": list(self.custom_ports),
        }

        self.reset_state()

        if was_running:
            started = self.start()
            if not started:
                print(f"[agent] could not restart sniffer on iface={self.iface}")

        print(
            "[agent] applied remote config "
            f"iface={self.iface} mode={self.mode} "
            f"port_mode={self.port_mode} custom_ports={self.custom_ports}"
        )

    def reset_state(self):
        self.state = self._empty_state()

    def snapshot(self):
        iface_classification = self.get_interface_classification_snapshot()
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
            "port_mode": self.port_mode,
            "custom_ports": list(self.custom_ports),
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
            "available_monitored_ifaces": iface_classification.get("monitored", []),
            "available_unmonitored_ifaces": iface_classification.get("skipped", []),
        }

    def _build_event_summary(self, event: dict) -> str:
        event_type = event.get("type")
        client = event.get("client") or f"{event.get('src_ip')}:{event.get('src_port')}"
        server = event.get("server") or f"{event.get('dst_ip')}:{event.get('dst_port')}"
        function_label = get_modbus_function_label(event.get("function_code"), event)

        if event_type == "READ_REQUEST":
            return (
                f"FC{event.get('function_code')} read request from {client} "
                f"to {server} | {function_label} | start={event.get('start_addr')} qty={event.get('quantity')}"
            )

        if event_type == "READ_RESPONSE":
            return (
                f"FC{event.get('function_code')} read response from {server} "
                f"to {client} | {function_label} | values={event.get('register_values', [])} | rtt={event.get('rtt')}"
            )

        if event_type == "WRITE_REQUEST":
            if event.get("values") is not None:
                return (
                    f"FC{event.get('function_code')} write request from {client} "
                    f"to {server} | {function_label} | start={event.get('start_addr')} qty={event.get('quantity')} "
                    f"values={event.get('values')}"
                )
            return (
                f"FC{event.get('function_code')} write request from {client} "
                f"to {server} | {function_label} | register={event.get('register')} value={event.get('value')}"
            )

        if event_type == "WRITE_RESPONSE":
            return (
                f"FC{event.get('function_code')} write response from {server} "
                f"to {client} | {function_label} | register={event.get('register')} value={event.get('value')} | rtt={event.get('rtt')}"
            )

        if event_type == "EXCEPTION_RESPONSE":
            return (
                f"FC{event.get('function_code')} exception response from {server} "
                f"to {client} | {function_label} | exception_code={event.get('exception_code')} | rtt={event.get('rtt')}"
            )

        return (
            f"Modbus transaction detected | "
            f"{event.get('src_ip')}:{event.get('src_port')} -> {event.get('dst_ip')}:{event.get('dst_port')}"
        )


def main():
    # Verifica e garante que NPCAP/libpcap está instalado antes de tudo
    ensure_npcap_installed()
    
    bundled_config = load_agent_config()

    parser = build_arg_parser(bundled_config)
    args = parser.parse_args()

    print("\n=== OT LAB AGENT ===")
    print("Interfaces disponíveis:", ", ".join(sorted(set(get_if_list()))))
    print(f"Interface pedida: {args.iface}")
    print(f"Port mode pedido: {args.port_mode}")
    print(f"Custom ports pedidos: {args.custom_ports}")

    if bundled_config:
        print("[agent] config carregada de ficheiro")

    agent = AgentMonitor(
        iface=args.iface,
        mode=args.mode,
        port_mode=args.port_mode,
        custom_ports=args.custom_ports,
        server_url=args.server,
        session_id=args.session_id,
    )

    print(
        f"[agent] using iface: {agent.iface} "
        f"port_mode={agent.port_mode} custom_ports={agent.custom_ports}"
    )

    agent.register()
    remote_cfg = agent.fetch_remote_config()
    if remote_cfg:
        print("[agent] connectivity check ok: remote config fetched")
    else:
        print("[agent] connectivity check failed: could not fetch remote config")
    started = agent.start()

    if not started:
        print(f"[agent] started without active sniffing. iface={agent.iface}")

    agent.send_runtime_update()

    print(
        f"[agent] session={agent.session_id} id={agent.agent_id} "
        f"iface={agent.iface} mode={agent.mode} "
        f"port_mode={agent.port_mode} custom_ports={agent.custom_ports} -> {args.server}"
    )

    stop_event = threading.Event()

    def control_plane_loop():
        last_config_poll = 0.0
        last_heartbeat = 0.0
        last_diag = 0.0
        while not stop_event.is_set():
            try:
                # Prioritize command processing to reduce UI-to-execution latency.
                agent.process_pending_commands()

                now = time.time()
                if now - last_config_poll >= 2.0:
                    remote_config = agent.fetch_remote_config()
                    agent.apply_config_if_needed(remote_config)
                    last_config_poll = now

                if now - last_heartbeat >= 1.0:
                    agent.send_heartbeat()
                    last_heartbeat = now

                if now - last_diag >= 8.0:
                    print(
                        "[agent] control loop alive "
                        f"session={agent.session_id} "
                        f"instance={getattr(agent, '_control_plane_instance', '-')}"
                    )
                    last_diag = now
            except Exception as e:
                print(f"[agent] error in control loop: {e}")
                stop_event.wait(0.2)
                continue

            stop_event.wait(0.25)

    control_thread = threading.Thread(
        target=control_plane_loop,
        name="agent-control-plane",
        daemon=True,
    )
    control_thread.start()

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[agent] stopping...")
    finally:
        stop_event.set()
        try:
            control_thread.join(timeout=1.5)
        except Exception:
            pass
        agent.stop_process_sim()
        agent.stop_modbus_client()
        agent.stop_modbus_server()
        agent.stop()
        agent.send_runtime_update()

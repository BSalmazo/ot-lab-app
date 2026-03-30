import socket
import threading
import time
from collections import defaultdict, deque

from scapy.all import get_if_list

from .config import DEFAULT_MODE, DEFAULT_SERVER_URL, DEFAULT_SESSION_ID, DEFAULT_IFACE, build_arg_parser, load_agent_config, ensure_npcap_installed
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
        server_url=DEFAULT_SERVER_URL,
        session_id=DEFAULT_SESSION_ID,
    ):
        # Garante que NPCAP/libpcap está instalado antes de iniciar
        ensure_npcap_installed()
        
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
                    self.execute_modbus_action(payload)
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

    def execute_modbus_action(self, payload: dict):
        try:
            function_def, normalized = validate_modbus_action_payload(payload)
        except ModbusValidationError as exc:
            print(f"[agent] invalid modbus action payload: {exc}")
            return

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
            print(f"[agent] modbus action send failed: {type(exc).__name__}: {exc}")
            return

        if not response:
            print("[agent] modbus action completed without response")
            return

        if len(response) >= 8:
            function_code = response[7]
            if function_code & 0x80 and len(response) >= 9:
                print(
                    f"[agent] modbus action response exception fc={function_code & 0x7F} "
                    f"code={response[8]} raw={response.hex(' ').upper()}"
                )
            else:
                print(
                    f"[agent] modbus action response ok fc={function_code} "
                    f"raw={response.hex(' ').upper()}"
                )
        else:
            print(f"[agent] short modbus response raw={response.hex(' ').upper()}")

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

    if bundled_config:
        print("[agent] config carregada de ficheiro")

    agent = AgentMonitor(
        iface=args.iface,
        mode=args.mode,
        server_url=args.server,
        session_id=args.session_id,
    )

    print(f"[agent] using iface: {agent.iface}")

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
            try:
                remote_config = agent.fetch_remote_config()
                agent.apply_config_if_needed(remote_config)
                agent.process_pending_commands()
                agent.send_heartbeat()
            except Exception as e:
                print(f"[agent] error in main loop: {e}")
                time.sleep(0.5)
                continue
            
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[agent] stopping...")
    finally:
        agent.stop_modbus_client()
        agent.stop_modbus_server()
        agent.stop()
        agent.send_runtime_update()

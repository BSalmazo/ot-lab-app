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
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from statistics import mean

import requests
from scapy.all import AsyncSniffer, IP, TCP, get_if_list


MODBUS_KNOWN_FUNCTION_CODES = {1, 2, 3, 4, 5, 6, 15, 16}
DEFAULT_SERVER_URL = "https://web-production-56599.up.railway.app/"
DEFAULT_SESSION_ID = "__SESSION_ID__"
DEFAULT_IFACE = "ALL"
DEFAULT_MODE = "__MODE__"

CONFIG_DIR = Path.home() / ".ot_lab_agent"
CONFIG_FILE = CONFIG_DIR / "agent_config.json"


def load_or_create_local_identity():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if data.get("agent_id"):
                return data
        except Exception:
            pass

    identity = {
        "agent_id": str(uuid.uuid4()),
        "created_at": time.time(),
    }
    CONFIG_FILE.write_text(json.dumps(identity, indent=2), encoding="utf-8")
    return identity


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

        self.last_applied_config = {
            "iface": iface,
            "mode": mode,
        }

    def get_sniff_interfaces(self):
        available = self.get_available_interfaces()

        if self.iface == "ALL":
            return available

        if self.iface in available:
            return [self.iface]

        return []

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

        # Evita interfaces problemáticas no macOS
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

        if function_code not in MODBUS_KNOWN_FUNCTION_CODES:
            return False

        return True

    def _tx_key(self, tx_id, src_ip, src_port, dst_ip, dst_port):
        return (tx_id, src_ip, src_port, dst_ip, dst_port)

    def _reverse_tx_key(self, tx_id, src_ip, src_port, dst_ip, dst_port):
        return (tx_id, dst_ip, dst_port, src_ip, src_port)

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
            "transaction_id": tx_id,
            "function_code": function_code,
            "unit_id": unit_id,
            "length": length,
            "protocol": "MODBUS_TCP",
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

    def _emit_alert(self, event: dict, reasons: list[str], score: int):
        if not reasons or score <= 0:
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

            if initiator not in self.state["initiators_seen"]:
                score += 2
                reasons.append(f"novo initiator: {initiator}")

            if responder not in self.state["responders_seen"]:
                score += 2
                reasons.append(f"novo responder: {responder}")

            if decoded["function_code"] not in self.state["function_codes_seen"]:
                score += 3
                reasons.append(f"novo function code: {decoded['function_code']}")

            if decoded["type"] == "READ_REQUEST":
                key = (
                    decoded["dst_ip"],
                    decoded["dst_port"],
                    decoded["start_addr"],
                    decoded["quantity"],
                )
                profile = self._get_or_create_read_pattern(key)

                if profile["count"] == 0:
                    score += 2
                    reasons.append(
                        f"novo padrao de leitura: {decoded['dst_ip']}:{decoded['dst_port']} "
                        f"start={decoded['start_addr']} qty={decoded['quantity']}"
                    )

                if profile["avg_period"] and len(profile["timestamps"]) > 0:
                    last_ts = profile["timestamps"][-1]
                    observed = decoded["timestamp"] - last_ts
                    deviation = abs(observed - profile["avg_period"]) / profile["avg_period"]
                    if deviation > self.period_deviation_threshold:
                        score += 3
                        reasons.append(
                            f"desvio de periodicidade: esperado≈{profile['avg_period']:.3f}s "
                            f"observado={observed:.3f}s"
                        )

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

                if reg_profile["count"] == 0:
                    score += 3
                    reasons.append(f"novo registrador escrito: {decoded['register']}")

                if decoded["value"] not in reg_profile["values_seen"]:
                    score += 2
                    reasons.append(
                        f"novo valor no registrador {decoded['register']}: {decoded['value']}"
                    )

                reg_profile["count"] += 1
                reg_profile["values_seen"].add(decoded["value"])
                reg_profile["last_value"] = decoded["value"]

                self.state["pending_transactions"][
                    self._tx_key(
                        decoded["transaction_id"],
                        decoded["src_ip"],
                        decoded["src_port"],
                        decoded["dst_ip"],
                        decoded["dst_port"],
                    )
                ] = {"timestamp": decoded["timestamp"]}

            self.state["initiators_seen"].add(initiator)
            self.state["responders_seen"].add(responder)
            self.state["function_codes_seen"].add(decoded["function_code"])

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
    parser = argparse.ArgumentParser(description="OT Lab Agent")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL, help="URL da dashboard/backend")
    parser.add_argument("--session-id", default=DEFAULT_SESSION_ID, help="Session ID")
    parser.add_argument("--iface", default=DEFAULT_IFACE, help="Interface de rede inicial")
    parser.add_argument("--mode", default=DEFAULT_MODE, choices=["LEARNING", "MONITORING"])
    args = parser.parse_args()

    print("\n=== OT LAB AGENT ===")
    print("Interfaces disponíveis:", ", ".join(sorted(set(get_if_list()))))
    print(f"Interface pedida: {args.iface}")

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

    print(
        f"[agent] session={agent.session_id} id={agent.agent_id} "
        f"iface={agent.iface} mode={agent.mode} -> {args.server}"
    )

    try:
        while True:
            remote_config = agent.fetch_remote_config()
            agent.apply_config_if_needed(remote_config)
            agent.send_heartbeat()
            time.sleep(3)
    except KeyboardInterrupt:
        print("\n[agent] stopping...")
        agent.stop()


if __name__ == "__main__":
    main()
import platform
import time
from statistics import mean

from scapy.all import AsyncSniffer, IP, TCP, get_if_list

from .config import DEFAULT_IFACE
from .modbus_parser import (
    MODBUS_KNOWN_FUNCTION_CODES,
    decode_modbus,
    extract_modbus_frames,
    looks_like_modbus_tcp,
    reverse_tx_key,
    tx_key,
)
from .protocols.modbus.modbus_definitions import get_modbus_function_label


class SnifferMixin:
    def start(self):
        sniff_ifaces = self.get_sniff_interfaces()

        if not sniff_ifaces:
            print(f"[agent] invalid iface '{self.iface}'. available={self.get_available_interfaces()}")
            self.sniffer = None
            return False

        # Filter out loopback and virtual interfaces (macOS/Linux)
        blocked_prefixes = ("anpi", "ap", "awdl", "llw", "utun", "bridge", "gif", "stf")
        filtered_ifaces = [
            iface for iface in sniff_ifaces
            if not iface.startswith(blocked_prefixes) and iface.lower() != "lo"
        ]
        
        # On Windows, loopback is usually "lo" or similar, but use what's available
        sniff_ifaces = filtered_ifaces if filtered_ifaces else sniff_ifaces

        if not sniff_ifaces:
            print("[agent] no usable interfaces after filtering")
            self.sniffer = None
            return False

        try:
            iface_arg = sniff_ifaces if len(sniff_ifaces) > 1 else sniff_ifaces[0]
            print(f"[agent] attempting to start sniffer on: {iface_arg}")
            
            self.sniffer = AsyncSniffer(
                iface=iface_arg,
                filter="tcp",
                prn=self._handle_packet,
                store=False,
                promisc=False,
            )
            self.sniffer.start()
            print(f"[agent] sniffing successfully started on interfaces: {sniff_ifaces}")
            return True
        except Exception as e:
            print(f"[agent] ERROR - failed to start sniffer on iface={self.iface}")
            print(f"[agent] Exception details: {type(e).__name__}: {e}")
            print(f"[agent] Available interfaces: {self.get_available_interfaces()}")
            print(f"[agent] sniffer startup diagnostics: AsyncSniffer requires a working libpcap/Npcap runtime.")
            if platform.system() == "Windows":
                print(
                    "[agent] WINDOWS DIAGNOSIS:\n"
                    "  1. Verify Npcap is installed: https://nmap.org/npcap/\n"
                    "  2. Check Npcap driver service is running: services.msc -> look for 'Npcap'\n"
                    "  3. Ensure admin privileges if using promiscuous mode\n"
                    "  4. Restart your machine after Npcap installation\n"
                    "  5. Try running with a specific interface name instead of 'ALL'"
                )
            elif platform.system() == "Darwin":
                print(
                    "[agent] MACOS DIAGNOSIS:\n"
                    "  1. Ensure ChmodBPF is installed for non-root capture\n"
                    "  2. Try: sudo python agent_script.py (if needed)\n"
                    "  3. Check system integrity: gatekeeper might block unsigned drivers"
                )
            else:
                print(
                    "[agent] LINUX DIAGNOSIS:\n"
                    "  1. Install libpcap: sudo apt-get install libpcap-dev\n"
                    "  2. Grant permissions: sudo setcap cap_net_raw,cap_net_admin=eip /usr/bin/python3\n"
                    "  3. Or run with sudo: sudo python agent_script.py"
                )
            self.sniffer = None
            return False

    def stop(self):
        if self.sniffer is not None:
            try:
                self.sniffer.stop()
            except Exception as e:
                print(f"[agent] warning while stopping sniffer: {e}")
            self.sniffer = None

    def get_available_interfaces(self):
        now = float(time.time())
        cache_ttl_s = 15.0
        cached_ifaces = getattr(self, "_cached_ifaces", None)
        cached_at = getattr(self, "_cached_ifaces_at", 0.0)
        if cached_ifaces is not None and (now - float(cached_at)) < cache_ttl_s:
            return list(cached_ifaces)
        try:
            ifaces = sorted(set(get_if_list()))
            cleaned = [iface for iface in ifaces if iface]
            self._cached_ifaces = cleaned
            self._cached_ifaces_at = now
            return cleaned
        except Exception:
            return []

    def get_sniff_interfaces(self):
        available = self.get_available_interfaces()

        if self.iface == DEFAULT_IFACE:
            return available

        if self.iface in available:
            return [self.iface]

        return []

    def _get_or_create_read_pattern(self, key):
        if key not in self.state["read_patterns"]:
            self.state["read_patterns"][key] = {
                "count": 0,
                "timestamps": self.make_timestamps_deque(),
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
            "function_code": event.get("function_code"),
            "raw_function_code": event.get("raw_function_code"),
            "function_label": get_modbus_function_label(event.get("function_code"), event),
            "exception_code": event.get("exception_code"),
            "register": event.get("register"),
            "address": event.get("address"),
            "start_addr": event.get("start_addr"),
            "quantity": event.get("quantity"),
            "read_start": event.get("read_start"),
            "read_quantity": event.get("read_quantity"),
            "value": event.get("value"),
            "values": event.get("values"),
            "unit_id": event.get("unit_id"),
            "transaction_id": event.get("transaction_id"),
            "rtt": event.get("rtt"),
            "client": event.get("client"),
            "server": event.get("server"),
        }
        self.send_alert(alert)

    def _handle_packet(self, pkt):
        try:
            if IP not in pkt or TCP not in pkt:
                return

            ip = pkt[IP]
            tcp = pkt[TCP]
            payload = bytes(tcp.payload)

            if not payload:
                return

            if not looks_like_modbus_tcp(payload):
                return

            frames = extract_modbus_frames(payload)
            if not frames:
                return

            for frame in frames:
                decoded = decode_modbus(
                    payload=frame,
                    src_ip=ip.src,
                    src_port=tcp.sport,
                    dst_ip=ip.dst,
                    dst_port=tcp.dport,
                    timestamp=float(pkt.time),
                    context=self,
                )
                if not decoded:
                    continue

                event = decoded.copy()
                score = 0
                reasons = []

                if decoded["type"] in ("READ_REQUEST", "WRITE_REQUEST", "GENERIC_REQUEST", "UNKNOWN_REQUEST"):
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

                    if decoded["type"] == "WRITE_REQUEST":
                        register_ref = decoded.get("register", decoded.get("start_addr"))
                        if register_ref is not None:
                            reg_profile = self._get_or_create_write_register(register_ref)
                            reg_profile["count"] += 1

                            if decoded.get("values") is not None:
                                for v in decoded["values"]:
                                    reg_profile["values_seen"].add(v)
                                reg_profile["last_value"] = decoded["values"][-1] if decoded["values"] else None
                                reasons.append(
                                    f"Write detected starting at register {register_ref} with values {decoded.get('values')}"
                                )
                            else:
                                if decoded.get("value") is not None:
                                    reg_profile["values_seen"].add(decoded["value"])
                                    reg_profile["last_value"] = decoded["value"]
                                reasons.append(
                                    f"Write detected on register {register_ref} with value {decoded.get('value')}"
                                )

                            score = 5

                    self.state["pending_transactions"][
                        tx_key(
                            decoded["transaction_id"],
                            decoded["src_ip"],
                            decoded["src_port"],
                            decoded["dst_ip"],
                            decoded["dst_port"],
                        )
                    ] = {"timestamp": decoded["timestamp"]}

                elif decoded["type"] in ("READ_RESPONSE", "WRITE_RESPONSE", "GENERIC_RESPONSE", "EXCEPTION_RESPONSE"):
                    reverse_key_value = reverse_tx_key(
                        decoded["transaction_id"],
                        decoded["src_ip"],
                        decoded["src_port"],
                        decoded["dst_ip"],
                        decoded["dst_port"],
                    )
                    matched = self.state["pending_transactions"].pop(reverse_key_value, None)
                    event["rtt"] = round(decoded["timestamp"] - matched["timestamp"], 6) if matched else None

                    if decoded["type"] == "WRITE_RESPONSE":
                        reasons.append(
                            f"Write confirmation received for register {decoded.get('register')} value {decoded.get('value')}"
                        )
                        score = 4

                    if decoded["type"] == "EXCEPTION_RESPONSE":
                        reasons.append(
                            f"Exception response detected with code {decoded.get('exception_code')}"
                        )
                        score = max(score, 6)

                event["iface"] = self.iface
                event["avg_polling_s"] = self._get_avg_polling_for_event(decoded)
                event["summary"] = self._build_event_summary(event)

                self.state["event_counts"][decoded["type"]] += 1
                self.send_event(event)

                if self.mode == "MONITORING":
                    self._emit_alert(event, reasons, score)

            now = time.time()
            if (now - float(getattr(self, "_last_snapshot_sent_at", 0.0))) >= float(getattr(self, "snapshot_interval_s", 1.0)):
                self.send_snapshot()
                self._last_snapshot_sent_at = now

        except Exception as e:
            print(f"[agent] packet handling error: {type(e).__name__}: {e}")

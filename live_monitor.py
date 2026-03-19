from collections import defaultdict, deque
from statistics import mean

from scapy.all import AsyncSniffer, TCP, IP


MODBUS_KNOWN_FUNCTION_CODES = {1, 2, 3, 4, 5, 6, 15, 16}


class LiveMonitor:
    def __init__(self, iface="lo0", mode="LEARNING", on_event=None, on_alert=None):
        self.iface = iface
        self.mode = mode
        self.on_event = on_event or (lambda event: None)
        self.on_alert = on_alert or (lambda alert: None)

        self.sniffer = None
        self.min_samples = 3
        self.period_deviation_threshold = 0.20
        self.max_timestamps = 20

        self.state = {
            "function_codes_seen": set(),
            "initiators_seen": set(),
            "responders_seen": set(),
            "read_patterns": {},     # key=(server_ip, server_port, start, qty)
            "write_registers": {},   # reg -> profile
            "pending_transactions": {},
            "event_counts": defaultdict(int),
        }

    def reset_state(self):
        self.state = {
            "function_codes_seen": set(),
            "initiators_seen": set(),
            "responders_seen": set(),
            "read_patterns": {},
            "write_registers": {},
            "pending_transactions": {},
            "event_counts": defaultdict(int),
    }

    @property
    def running(self) -> bool:
        return self.sniffer is not None

    def start(self, iface=None, mode=None):
        if self.running:
            return

        if iface:
            self.iface = iface
        if mode:
            self.mode = mode

        self.sniffer = AsyncSniffer(
            iface=self.iface,
            filter="tcp",
            prn=self._handle_packet,
            store=False,
        )
        self.sniffer.start()

    def stop(self):
        if self.sniffer is not None:
            self.sniffer.stop()
            self.sniffer = None

    def snapshot(self) -> dict:
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
            "mode": self.mode,
            "function_codes_seen": sorted(self.state["function_codes_seen"]),
            "initiators_seen": sorted(self.state["initiators_seen"]),
            "responders_seen": sorted(self.state["responders_seen"]),
            "read_patterns": read_patterns,
            "write_registers": write_registers,
            "event_counts": dict(self.state["event_counts"]),
        }

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
            "timestamp": event["timestamp"],
            "severity": severity,
            "score": score,
            "reasons": reasons,
            "event_type": event["type"],
            "src": f"{event['src_ip']}:{event['src_port']}",
            "dst": f"{event['dst_ip']}:{event['dst_port']}",
        }
        self.on_alert(alert)

    def _handle_packet(self, pkt):
        if IP not in pkt or TCP not in pkt:
            return

        ip = pkt[IP]
        tcp = pkt[TCP]
        payload = bytes(tcp.payload)

        if not payload:
            return
        if not self._looks_like_modbus_tcp(payload):
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
            if matched:
                event["rtt"] = round(decoded["timestamp"] - matched["timestamp"], 6)
            else:
                event["rtt"] = None

        self.state["event_counts"][decoded["type"]] += 1
        self.on_event(event)

        if self.mode == "MONITORING":
            self._emit_alert(event, reasons, score)
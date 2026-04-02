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
    def _monitored_modbus_ports(self):
        ports = {502, 5020, 15020}
        try:
            server_port = int((getattr(self, "server_runtime", {}) or {}).get("port", 0))
            if 1 <= server_port <= 65535:
                ports.add(server_port)
        except Exception:
            pass
        try:
            client_port = int((getattr(self, "client_runtime", {}) or {}).get("port", 0))
            if 1 <= client_port <= 65535:
                ports.add(client_port)
        except Exception:
            pass
        return ports

    def _is_likely_monitored_modbus_traffic(self, sport: int, dport: int):
        ports = self._monitored_modbus_ports()
        return sport in ports or dport in ports

    def _should_emit_event(self, event: dict) -> bool:
        """
        Throttle high-frequency read traffic to keep UI near real-time under fast polling
        (e.g., 300ms), while preserving full fidelity for writes/exceptions.
        """
        event_type = str(event.get("type") or "").upper()

        if event_type in {"WRITE_REQUEST", "WRITE_RESPONSE", "EXCEPTION_RESPONSE", "UNKNOWN_REQUEST"}:
            return True

        # READ_RESPONSE is usually redundant for liveness; keep only sampled READ_REQUEST.
        if event_type == "READ_RESPONSE":
            return False

        if event_type != "READ_REQUEST":
            return True

        now_ts = event.get("timestamp")
        try:
            now_ts = float(now_ts)
        except (TypeError, ValueError):
            now_ts = time.time()

        flow_key = (
            event.get("server"),
            event.get("function_code"),
            event.get("start_addr"),
            event.get("quantity"),
            event.get("unit_id"),
        )

        cache = getattr(self, "_read_emit_last_ts", None)
        if cache is None:
            cache = {}
            self._read_emit_last_ts = cache

        prev = cache.get(flow_key)
        cache[flow_key] = now_ts

        # Keep at most one event every 0.4s per read flow.
        if prev is not None and (now_ts - float(prev)) < 0.4:
            return False

        # Opportunistic cleanup
        if len(cache) > 4000:
            cutoff = now_ts - 60.0
            old_keys = [k for k, v in cache.items() if float(v) < cutoff]
            for k in old_keys[:2000]:
                cache.pop(k, None)

        return True

    def _event_identity(self, event: dict):
        tx_id = event.get("transaction_id")
        # For Modbus/TCP events transaction id is the best dedupe anchor.
        # If absent, return None and fallback to temporal fingerprint logic.
        if tx_id is None:
            return None
        return (
            event.get("type"),
            tx_id,
            event.get("function_code"),
            event.get("src_ip"),
            event.get("src_port"),
            event.get("dst_ip"),
            event.get("dst_port"),
            event.get("unit_id"),
            event.get("register"),
            event.get("start_addr"),
            event.get("quantity"),
            event.get("value"),
        )

    def _is_duplicate_event(self, event: dict) -> bool:
        # Windows ALL-mode may surface duplicate packet notifications across adapters.
        # Keep a tiny temporal cache to suppress near-identical duplicates.
        fp_cache = getattr(self, "_recent_event_fingerprints", None)
        if fp_cache is None:
            fp_cache = {}
            self._recent_event_fingerprints = fp_cache

        id_cache = getattr(self, "_recent_event_ids", None)
        if id_cache is None:
            id_cache = {}
            self._recent_event_ids = id_cache

        ts = event.get("timestamp")
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            ts = time.time()

        # Primary dedupe path: if we already saw this event identity recently, drop it.
        event_id = self._event_identity(event)
        if event_id is not None:
            prev_ts = id_cache.get(event_id)
            id_cache[event_id] = ts

            if len(id_cache) > 6000:
                cutoff = ts - 90.0
                old_keys = [k for k, v in id_cache.items() if v < cutoff]
                for k in old_keys[:3000]:
                    id_cache.pop(k, None)

            if prev_ts is not None and (ts - float(prev_ts)) <= 90.0:
                return True

        fp = (
            event.get("type"),
            event.get("transaction_id"),
            event.get("function_code"),
            event.get("src_ip"),
            event.get("src_port"),
            event.get("dst_ip"),
            event.get("dst_port"),
            event.get("register"),
            event.get("start_addr"),
            event.get("quantity"),
            event.get("value"),
        )

        prev_ts = fp_cache.get(fp)
        fp_cache[fp] = ts

        # Cleanup old fingerprints opportunistically.
        if len(fp_cache) > 3000:
            cutoff = ts - 8.0
            keys = [k for k, v in fp_cache.items() if v < cutoff]
            for k in keys[:1500]:
                fp_cache.pop(k, None)

        if prev_ts is None:
            return False

        return (ts - float(prev_ts)) <= 0.35

    def start(self):
        sniff_ifaces = self.get_sniff_interfaces()

        if not sniff_ifaces:
            print(f"[agent] invalid iface '{self.iface}'. available={self.get_available_interfaces()}")
            self.sniffer = None
            return False

        # Filter out loopback and virtual interfaces (macOS/Linux).
        # On Windows we keep loopback aliases available because Npcap naming differs.
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
            os_name = platform.system()
            # Windows stability: start one sniffer per interface when running in ALL mode.
            # Multi-interface list capture on Windows can be flaky depending on Npcap/adapter mix.
            if os_name == "Windows" and self.iface == DEFAULT_IFACE and len(sniff_ifaces) > 1:
                started_sniffers = []
                started_ifaces = []
                failed_ifaces = []

                print(f"[agent] attempting to start per-interface sniffers on Windows: {sniff_ifaces}")
                for iface in sniff_ifaces:
                    try:
                        snf = AsyncSniffer(
                            iface=iface,
                            filter="tcp",
                            prn=self._handle_packet,
                            store=False,
                            promisc=False,
                        )
                        snf.start()
                        started_sniffers.append(snf)
                        started_ifaces.append(iface)
                    except Exception as ie:
                        failed_ifaces.append((iface, f"{type(ie).__name__}: {ie}"))

                if started_sniffers:
                    self.sniffer = started_sniffers
                    print(f"[agent] sniffing successfully started on interfaces: {started_ifaces}")
                    if failed_ifaces:
                        print(f"[agent] warning: failed interfaces: {failed_ifaces}")
                    return True

                self.sniffer = None
                print(f"[agent] ERROR - failed to start sniffer on all candidate interfaces: {failed_ifaces}")
                return False

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
                if isinstance(self.sniffer, list):
                    for snf in self.sniffer:
                        try:
                            snf.stop()
                        except Exception as ie:
                            print(f"[agent] warning while stopping one sniffer: {ie}")
                else:
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

        # Consolidate HMI->PLC action into a single alert card:
        # - accepted writes => WRITE_RESPONSE
        # - rejected writes => EXCEPTION_RESPONSE (request context is carried over)
        if event_type == "WRITE_RESPONSE":
            return True
        if event_type == "WRITE_REQUEST":
            return False
        if event_type == "EXCEPTION_RESPONSE":
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
            if not self._is_likely_monitored_modbus_traffic(int(tcp.sport), int(tcp.dport)):
                return
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
                    ] = {
                        "timestamp": decoded["timestamp"],
                        "request": {
                            "function_code": decoded.get("function_code"),
                            "unit_id": decoded.get("unit_id"),
                            "register": decoded.get("register"),
                            "address": decoded.get("address"),
                            "start_addr": decoded.get("start_addr"),
                            "quantity": decoded.get("quantity"),
                            "read_start": decoded.get("read_start"),
                            "read_quantity": decoded.get("read_quantity"),
                            "value": decoded.get("value"),
                            "values": decoded.get("values"),
                        },
                    }

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
                    request_ctx = (matched or {}).get("request") or {}

                    # Carry request-side context into responses/exceptions so alerts can show
                    # register/value even when the response frame does not include them.
                    for key in (
                        "function_code",
                        "unit_id",
                        "register",
                        "address",
                        "start_addr",
                        "quantity",
                        "read_start",
                        "read_quantity",
                    ):
                        if event.get(key) is None and request_ctx.get(key) is not None:
                            event[key] = request_ctx.get(key)

                    if event.get("value") is None and request_ctx.get("value") is not None:
                        event["value"] = request_ctx.get("value")
                    if (not event.get("values")) and request_ctx.get("values"):
                        event["values"] = request_ctx.get("values")

                    if decoded["type"] == "WRITE_RESPONSE":
                        reasons.append(
                            f"HMI request confirmed by PLC (register={decoded.get('register')}, value={decoded.get('value')})"
                        )
                        score = 4

                    if decoded["type"] == "EXCEPTION_RESPONSE":
                        reasons.append(
                            f"HMI request rejected by PLC (exception_code={decoded.get('exception_code')})"
                        )
                        score = max(score, 6)

                sniffed_iface = getattr(pkt, "sniffed_on", None)
                event["iface"] = sniffed_iface or self.iface
                event["avg_polling_s"] = self._get_avg_polling_for_event(decoded)
                event["summary"] = self._build_event_summary(event)

                if self._is_duplicate_event(event):
                    continue

                if not self._should_emit_event(event):
                    continue

                self.state["event_counts"][decoded["type"]] += 1
                event_type = str(event.get("type") or "").upper()
                critical_event = event_type in {
                    "WRITE_REQUEST",
                    "WRITE_RESPONSE",
                    "EXCEPTION_RESPONSE",
                    "UNKNOWN_REQUEST",
                    "GENERIC_REQUEST",
                }
                self.send_event(event, critical=critical_event)

                if self.mode == "MONITORING":
                    self._emit_alert(event, reasons, score)

            now = time.time()
            if (now - float(getattr(self, "_last_snapshot_sent_at", 0.0))) >= float(getattr(self, "snapshot_interval_s", 1.0)):
                self.send_snapshot()
                self._last_snapshot_sent_at = now

        except Exception as e:
            print(f"[agent] packet handling error: {type(e).__name__}: {e}")

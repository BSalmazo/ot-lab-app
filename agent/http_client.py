import time
import threading
from queue import Empty, Full, Queue

import requests


class HttpClientMixin:
    def _observe_control_plane_instance(self, source: str, instance_id):
        inst = str(instance_id or "").strip()
        if not inst:
            return

        prev = getattr(self, "_control_plane_instance", None)
        if prev and prev != inst:
            print(f"[agent] control-plane instance switched: {prev} -> {inst} ({source})")
        elif not prev:
            print(f"[agent] control-plane instance: {inst} ({source})")
        self._control_plane_instance = inst

    def _ensure_control_session(self):
        session = getattr(self, "_control_session", None)
        if session is None:
            session = requests.Session()
            self._control_session = session
        return session

    def _ensure_async_post_worker(self):
        if getattr(self, "_normal_post_queue", None) is not None:
            return

        # Split telemetry queues so critical control-plane traffic (heartbeat, command result)
        # is never starved by high-rate event streams.
        self._critical_post_queue = Queue(maxsize=200)
        # Keep normal telemetry queue intentionally small to avoid long stale backlogs.
        self._normal_post_queue = Queue(maxsize=200)
        self._post_session = requests.Session()
        self._event_batch_lock = threading.Lock()
        self._event_batch = []
        self._event_batch_max = 120
        self._event_batch_hard_limit = 1200
        self._event_flush_interval_s = 0.12
        self._event_batch_last_flush = 0.0
        self._http_error_log_last = {}

        def _should_log_http_error(path: str) -> bool:
            now = time.time()
            last = float(self._http_error_log_last.get(path, 0.0))
            if (now - last) >= 5.0:
                self._http_error_log_last[path] = now
                return True
            return False

        def worker():
            while True:
                item = None
                try:
                    item = self._critical_post_queue.get_nowait()
                except Empty:
                    try:
                        item = self._normal_post_queue.get(timeout=0.25)
                    except Empty:
                        continue
                    except Exception:
                        continue
                except Exception:
                    continue

                if not item:
                    continue

                path, payload, timeout = item
                try:
                    resp = self._post_session.post(
                        f"{self.server_url}{path}",
                        json=payload,
                        timeout=timeout,
                    )
                    if resp.status_code >= 400 and _should_log_http_error(path):
                        print(
                            f"[agent] HTTP error on {path}: "
                            f"status={resp.status_code} body={resp.text[:200]!r}"
                        )
                    elif path in {"/api/agent/register", "/api/agent/heartbeat"}:
                        try:
                            data = resp.json()
                        except Exception:
                            data = {}
                        self._observe_control_plane_instance(path, data.get("instance_id"))
                except Exception as e:
                    if _should_log_http_error(path):
                        print(f"[agent] HTTP post failed on {path}: {type(e).__name__}: {e}")

        thread = threading.Thread(target=worker, name="agent-http-post-worker", daemon=True)
        thread.start()
        self._post_worker = thread

        def event_flusher():
            while True:
                try:
                    self._flush_event_batch(force=False)
                except Exception:
                    pass
                time.sleep(0.05)

        flush_thread = threading.Thread(target=event_flusher, name="agent-event-batch-flusher", daemon=True)
        flush_thread.start()
        self._event_flusher = flush_thread

    def _should_log_http_error(self, path: str) -> bool:
        now = time.time()
        errors = getattr(self, "_http_error_log_last", None)
        if errors is None:
            errors = {}
            self._http_error_log_last = errors
        last = float(errors.get(path, 0.0))
        if (now - last) >= 5.0:
            errors[path] = now
            return True
        return False

    def _post_sync(self, path: str, payload: dict, timeout=2):
        try:
            session = self._ensure_control_session()
            resp = session.post(
                f"{self.server_url}{path}",
                json=payload,
                timeout=timeout,
            )
            if resp.status_code >= 400:
                if self._should_log_http_error(path):
                    print(
                        f"[agent] HTTP error on {path}: "
                        f"status={resp.status_code} body={resp.text[:200]!r}"
                    )
                return None

            try:
                data = resp.json()
            except Exception:
                data = {}
            self._observe_control_plane_instance(path, data.get("instance_id"))
            return data
        except Exception as e:
            if self._should_log_http_error(path):
                print(f"[agent] HTTP post failed on {path}: {type(e).__name__}: {e}")
            return None

    def fetch_remote_config(self):
        try:
            session = self._ensure_control_session()
            response = session.get(
                f"{self.server_url}/api/agent/config",
                params={"session_id": self.session_id},
                timeout=2,
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                return None
            self._observe_control_plane_instance("/api/agent/config", data.get("instance_id"))
            return data.get("config")
        except Exception as e:
            print(f"[agent] failed to fetch remote config: {type(e).__name__}: {e}")
            return None

    def fetch_pending_commands(self):
        try:
            session = self._ensure_control_session()
            response = session.get(
                f"{self.server_url}/api/agent/commands",
                params={"session_id": self.session_id},
                timeout=2,
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                return []
            self._observe_control_plane_instance("/api/agent/commands", data.get("instance_id"))
            commands = data.get("commands", [])
            pending_before_drain = data.get("pending_before_drain", len(commands))
            if commands:
                print(
                    "[agent] command poll "
                    f"instance={getattr(self, '_control_plane_instance', '-')}"
                    f" pending={pending_before_drain} received={len(commands)}"
                )
                self._last_command_poll_diag = now
            return commands
        except Exception as e:
            print(f"[agent] failed to fetch commands: {e}")
            return []

    def send_runtime_update(self):
        with self.runtime_lock:
            server_ref = self.modbus_server
            client_ref = self.modbus_client
            payload = {
                "session_id": self.session_id,
                "server": dict(self.server_runtime),
                "client": dict(self.client_runtime),
            }
        payload["process_sim"] = self.get_process_sim_snapshot()

        if server_ref and server_ref.running:
            try:
                payload["server"]["registers_preview"] = server_ref.get_registers_preview(start=0, quantity=16)
            except Exception:
                pass

        if client_ref and client_ref.running:
            try:
                payload["client"].update(client_ref.get_snapshot())
            except Exception:
                pass

        self._post("/api/agent/runtime", payload, timeout=(0.8, 1.5))

    def register(self):
        iface_classification = self.get_interface_classification_snapshot()
        payload = {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "hostname": self.hostname,
            "iface": self.iface,
            "mode": self.mode,
            "port_mode": self.port_mode,
            "custom_ports": list(self.custom_ports),
            "running": False,
            "timestamp": time.time(),
            "available_ifaces": self.get_available_interfaces(),
            "available_monitored_ifaces": iface_classification.get("monitored", []),
            "available_unmonitored_ifaces": iface_classification.get("skipped", []),
            "capabilities": list(getattr(self, "capabilities", [])),
        }
        self._post("/api/agent/register", payload, timeout=(1.0, 2.0), critical=True)

    def send_heartbeat(self):
        with self.runtime_lock:
            self.server_runtime["running"] = bool(self.modbus_server and self.modbus_server.running)
            self.client_runtime["running"] = bool(self.modbus_client and self.modbus_client.running)

        iface_classification = self.get_interface_classification_snapshot()
        payload = {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "hostname": self.hostname,
            "iface": self.iface,
            "mode": self.mode,
            "port_mode": self.port_mode,
            "custom_ports": list(self.custom_ports),
            "running": self.sniffer is not None,
            "timestamp": time.time(),
            "available_ifaces": self.get_available_interfaces(),
            "available_monitored_ifaces": iface_classification.get("monitored", []),
            "available_unmonitored_ifaces": iface_classification.get("skipped", []),
            "capabilities": list(getattr(self, "capabilities", [])),
        }
        # Heartbeat is critical for connection liveness in UI.
        self._post("/api/agent/heartbeat", payload, timeout=(0.8, 1.5), critical=True)
        self.send_runtime_update()

    def send_snapshot(self):
        self._post("/api/agent/snapshot", self.snapshot(), timeout=(0.8, 1.5))

    def send_alert(self, alert):
        self._post("/api/agent/alert", alert, timeout=(0.8, 1.5))

    def send_event(self, event, critical: bool = False):
        self._ensure_async_post_worker()

        if critical:
            # High-value events should bypass batching for lowest possible latency.
            self._post("/api/agent/event", event, timeout=(0.8, 1.5), critical=True)
            return

        should_force_flush = False
        with self._event_batch_lock:
            self._event_batch.append(event)

            if len(self._event_batch) > self._event_batch_hard_limit:
                # Keep freshest events when producer outruns transport.
                self._event_batch = self._event_batch[-self._event_batch_hard_limit:]

            if len(self._event_batch) >= self._event_batch_max:
                should_force_flush = True

        if should_force_flush:
            self._flush_event_batch(force=True)

    def send_command_result(self, command_id: str, status: str, message: str = ""):
        payload = {
            "session_id": self.session_id,
            "command_id": command_id,
            "status": status,
            "message": message,
        }
        for attempt in range(3):
            result = self._post_sync("/api/agent/command_result", payload, timeout=(2.0, 8.0))
            if result is not None:
                return
            if attempt < 2:
                time.sleep(1.0)

    def send_disconnect(self):
        self._post(
            "/api/agent/disconnect",
            {
                "session_id": self.session_id,
                "agent_id": self.agent_id,
                "timestamp": time.time(),
            },
            timeout=(0.8, 1.5),
            critical=True,
        )

    def _post(self, path: str, payload: dict, timeout=2, critical: bool = False):
        if critical:
            self._post_sync(path, payload, timeout=timeout)
            return

        self._ensure_async_post_worker()
        item = (path, payload, timeout)
        target_queue = self._normal_post_queue

        try:
            target_queue.put_nowait(item)
            return
        except Full:
            pass

        # Queue is full: drop one oldest queued item and keep the freshest telemetry.
        try:
            _ = target_queue.get_nowait()
        except Empty:
            pass

        try:
            target_queue.put_nowait(item)
        except Exception:
            pass

    def _flush_event_batch(self, force: bool = False):
        now = time.time()

        with self._event_batch_lock:
            batch = self._event_batch
            if not batch:
                return

            age = now - float(self._event_batch_last_flush or 0.0)
            if not force and len(batch) < self._event_batch_max and age < self._event_flush_interval_s:
                return

            take = min(len(batch), self._event_batch_max)
            to_send = batch[:take]
            del batch[:take]
            self._event_batch_last_flush = now

        self._post(
            "/api/agent/events_batch",
            {
                "session_id": self.session_id,
                "events": to_send,
            },
            timeout=(0.8, 2.0),
            critical=False,
        )

import time
import threading
from queue import Empty, Full, Queue

import requests


class HttpClientMixin:
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
                    self._post_session.post(
                        f"{self.server_url}{path}",
                        json=payload,
                        timeout=timeout,
                    )
                except Exception:
                    pass

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

    def send_runtime_update(self):
        with self.runtime_lock:
            payload = {
                "session_id": self.session_id,
                "server": dict(self.server_runtime),
                "client": dict(self.client_runtime),
            }
        self._post("/api/agent/runtime", payload, timeout=(0.8, 1.5))

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
            "capabilities": list(getattr(self, "capabilities", [])),
        }
        self._post("/api/agent/register", payload, timeout=(1.0, 2.0), critical=True)

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
        self._post(
            "/api/agent/command_result",
            {
                "session_id": self.session_id,
                "command_id": command_id,
                "status": status,
                "message": message,
            },
            timeout=(1.0, 2.0),
            critical=True,
        )

    def _post(self, path: str, payload: dict, timeout=2, critical: bool = False):
        self._ensure_async_post_worker()
        item = (path, payload, timeout)
        target_queue = self._critical_post_queue if critical else self._normal_post_queue

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

import time
import threading
from queue import Empty, Full, Queue

import requests


class HttpClientMixin:
    def _ensure_async_post_worker(self):
        if getattr(self, "_post_queue", None) is not None:
            return

        self._post_queue = Queue(maxsize=600)
        self._post_session = requests.Session()

        def worker():
            while True:
                try:
                    item = self._post_queue.get()
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
        self._post("/api/agent/heartbeat", payload, timeout=(0.8, 1.5))
        self.send_runtime_update()

    def send_snapshot(self):
        self._post("/api/agent/snapshot", self.snapshot(), timeout=(0.8, 1.5))

    def send_alert(self, alert):
        self._post("/api/agent/alert", alert, timeout=(0.8, 1.5))

    def send_event(self, event):
        self._post("/api/agent/event", event, timeout=(0.8, 1.5))

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

        try:
            self._post_queue.put_nowait(item)
            return
        except Full:
            pass

        if critical:
            try:
                self._post_queue.put(item, timeout=0.3)
                return
            except Full:
                pass

        # Queue is full: drop one oldest queued item and keep the freshest telemetry.
        try:
            _ = self._post_queue.get_nowait()
        except Empty:
            pass

        try:
            self._post_queue.put_nowait(item)
        except Exception:
            pass

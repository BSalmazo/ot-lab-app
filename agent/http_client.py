import time

import requests


class HttpClientMixin:
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
        self._post("/api/agent/runtime", payload)

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
        self._post("/api/agent/register", payload)

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
        self._post("/api/agent/heartbeat", payload)
        self.send_runtime_update()

    def send_snapshot(self):
        self._post("/api/agent/snapshot", self.snapshot())

    def send_alert(self, alert):
        self._post("/api/agent/alert", alert)

    def send_event(self, event):
        self._post("/api/agent/event", event)

    def send_command_result(self, command_id: str, status: str, message: str = ""):
        self._post(
            "/api/agent/command_result",
            {
                "session_id": self.session_id,
                "command_id": command_id,
                "status": status,
                "message": message,
            },
        )

    def _post(self, path: str, payload: dict):
        try:
            requests.post(f"{self.server_url}{path}", json=payload, timeout=2)
        except Exception:
            pass

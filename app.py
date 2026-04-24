import io
import json
import re
import socket
import time
import uuid
import zipfile
import requests

from collections import deque
from pathlib import Path
from threading import Lock

from fastapi import Body, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agent.protocols.modbus.modbus_builder import build_modbus_tcp_request
from agent.protocols.modbus.modbus_definitions import (
    get_modbus_function_definitions,
    get_modbus_write_function_codes,
)
from agent.protocols.modbus.modbus_validators import (
    ValidationError as ModbusValidationError,
    validate_modbus_action_payload,
)
from agent.runtime import SimpleModbusClient, SimpleModbusServer

app = FastAPI(title="OT Lab App")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
SESSION_COOKIE = "scada_session_id"

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/downloads", StaticFiles(directory=str(BASE_DIR / "downloads")), name="downloads")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# GitHub releases cache
GITHUB_REPO = "BSalmazo/ot-lab-app"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
releases_cache = {"data": None, "timestamp": 0}
RELEASES_CACHE_TTL = 3600  # 1 hour

lock = Lock()
agents_by_session = {}


def recv_exact(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("socket closed while receiving")
        data += chunk
    return data


def modbus_write_single_register(host: str, port: int, register: int, value: int, unit_id: int = 1, timeout_s: float = 2.0):
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

        resp_header = recv_exact(conn, 7)
        resp_tx_id = int.from_bytes(resp_header[0:2], "big")
        resp_proto_id = int.from_bytes(resp_header[2:4], "big")
        resp_len = int.from_bytes(resp_header[4:6], "big")
        if resp_tx_id != tx_id or resp_proto_id != 0:
            raise RuntimeError("invalid Modbus response header")

        resp_pdu = recv_exact(conn, resp_len - 1)
        if len(resp_pdu) < 2:
            raise RuntimeError("short Modbus response")

        fc = resp_pdu[0]
        if fc & 0x80:
            exc_code = resp_pdu[1]
            raise RuntimeError(f"modbus exception code={exc_code}")
        if fc != function_code:
            raise RuntimeError(f"unexpected function code in response: {fc}")

    return {"ok": True, "transaction_id": tx_id}


class ProcessSimulationManager:
    def __init__(self):
        self._lock = Lock()
        self._server = None
        self._client = None
        self._config = {
            "host": "127.0.0.1",
            "port": 15020,
            "poll_interval": 0.5,
            "poll_start": 0,
            "poll_quantity": 16,
            "process_type": "tank_v1",
        }

    def _snapshot_locked(self):
        server = self._server
        client = self._client
        server_running = bool(server and server.running)
        client_running = bool(client and client.running)

        server_preview = {"start": 0, "quantity": 0, "values": []}
        if server_running:
            try:
                server_preview = server.get_registers_preview(start=0, quantity=16)
            except Exception:
                pass

        client_snapshot = {
            "last_values": [],
            "last_error": None,
            "last_poll_at": None,
            "last_success_at": None,
        }
        if client_running:
            try:
                client_snapshot = client.get_snapshot()
            except Exception:
                pass

        return {
            "running": server_running and client_running,
            "process_type": self._config["process_type"],
            "server": {
                "running": server_running,
                "host": self._config["host"],
                "port": self._config["port"],
                "registers_preview": server_preview,
            },
            "client": {
                "running": client_running,
                "host": self._config["host"],
                "port": self._config["port"],
                "poll_interval": self._config["poll_interval"],
                "poll_start": self._config["poll_start"],
                "poll_quantity": self._config["poll_quantity"],
                "last_values": list(client_snapshot.get("last_values") or []),
                "last_error": client_snapshot.get("last_error"),
                "last_poll_at": client_snapshot.get("last_poll_at"),
                "last_success_at": client_snapshot.get("last_success_at"),
            },
        }

    def snapshot(self):
        with self._lock:
            return self._snapshot_locked()

    def stop(self):
        with self._lock:
            server = self._server
            client = self._client
            self._server = None
            self._client = None

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

        return self.snapshot()

    def start(self, host=None, port=None, poll_interval=None, poll_start=None, poll_quantity=None, process_type=None):
        host = str(host or self._config["host"]).strip() or "127.0.0.1"
        port = int(port if port is not None else self._config["port"])
        poll_interval = float(poll_interval if poll_interval is not None else self._config["poll_interval"])
        poll_start = int(poll_start if poll_start is not None else self._config["poll_start"])
        poll_quantity = int(poll_quantity if poll_quantity is not None else self._config["poll_quantity"])
        process_type = str(process_type or self._config["process_type"]).strip() or "tank_v1"

        if process_type != "tank_v1":
            raise ValueError("Unsupported process_type")

        if port < 1 or port > 65535:
            raise ValueError("Port must be between 1 and 65535")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be > 0")
        if poll_start < 0:
            raise ValueError("poll_start must be >= 0")
        if poll_quantity < 1 or poll_quantity > 125:
            raise ValueError("poll_quantity must be between 1 and 125")

        self.stop()

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

        with self._lock:
            self._config["host"] = host
            self._config["port"] = port
            self._config["poll_interval"] = poll_interval
            self._config["poll_start"] = poll_start
            self._config["poll_quantity"] = poll_quantity
            self._config["process_type"] = process_type
            self._server = server if server_started else None
            self._client = client if client_started else None
            return self._snapshot_locked()

    def write_register(self, address: int, value: int, unit_id: int = 1):
        with self._lock:
            host = self._config["host"]
            port = self._config["port"]
            running = bool(self._server and self._server.running)

        if not running:
            raise RuntimeError("process simulation is not running")

        if address < 0 or address > 65535:
            raise ValueError("address must be between 0 and 65535")
        if value < 0 or value > 65535:
            raise ValueError("value must be between 0 and 65535")
        if unit_id < 0 or unit_id > 255:
            raise ValueError("unit_id must be between 0 and 255")

        modbus_write_single_register(host=host, port=port, register=address, value=value, unit_id=unit_id)
        return self.snapshot()


process_sim = ProcessSimulationManager()


def get_github_releases(force_refresh: bool = False):
    """
    Fetch agent releases from GitHub, with caching.
    Returns a list of releases with download information.
    """
    global releases_cache
    
    current_time = time.time()
    
    # Return cached data if still valid
    if (
        not force_refresh
        and releases_cache["data"] is not None
        and (current_time - releases_cache["timestamp"]) < RELEASES_CACHE_TTL
    ):
        return releases_cache["data"]
    
    try:
        # Try to fetch from GitHub API
        response = requests.get(GITHUB_API_URL, timeout=5)
        response.raise_for_status()
        
        releases = response.json()
        
        def classify_platform(asset_name: str):
            lowered = asset_name.lower()
            if "windows" in lowered or lowered.endswith(".exe"):
                return "windows"
            if "macos" in lowered or "mac" in lowered:
                return "macos"
            if "linux" in lowered:
                return "linux"
            return None

        def asset_score(asset_name: str):
            """Prefer full package downloads over raw scripts."""
            lowered = asset_name.lower()

            # Best option: packaged zip with agent bundle.
            if lowered.endswith(".zip") and "agent" in lowered:
                return 300
            if lowered.endswith(".zip"):
                return 250

            # Prefer canonical platform binary names over legacy artifact names.
            if "otlab-agent-windows-amd64.exe" in lowered:
                return 240
            if "otlab-agent-macos-amd64" in lowered:
                return 230
            if "otlab-agent-linux-amd64" in lowered:
                return 230

            # Native executable can still work as fallback.
            if lowered.endswith(".exe"):
                return 180
            if lowered.endswith(".app") or lowered.endswith(".dmg"):
                return 170

            # Scripts are last-resort and should not be primary links.
            if lowered.endswith(".sh") or lowered.endswith(".bat") or "install" in lowered:
                return 10

            return 100

        # Process releases to extract download links
        processed_releases = []
        for release in releases:
            if release.get("prerelease") and release["tag_name"] != "dev-latest":
                continue  # Skip most prerelease builds, but keep dev-latest
            
            assets = {}
            best_meta_by_platform = {}
            for asset in release.get("assets", []):
                asset_name = asset["name"]

                platform = classify_platform(asset_name)
                if not platform:
                    continue

                score = asset_score(asset_name)
                updated_at = str(asset.get("updated_at") or "")
                best_meta = best_meta_by_platform.get(platform)
                if best_meta is not None:
                    best_score = int(best_meta.get("score", -1))
                    best_updated_at = str(best_meta.get("updated_at") or "")
                    # Keep better score, or when tied keep the most recently updated asset.
                    if score < best_score:
                        continue
                    if score == best_score and updated_at <= best_updated_at:
                        continue

                if score < 0:
                    continue

                best_meta_by_platform[platform] = {
                    "score": score,
                    "updated_at": updated_at,
                }
                assets[platform] = {
                    "name": asset_name,
                    "url": asset["browser_download_url"],
                    "size": asset["size"],
                }
            
            if assets:  # Only include releases that have assets
                processed_releases.append({
                    "tag": release["tag_name"],
                    "name": release["name"],
                    "published_at": release["published_at"],
                    "updated_at": release.get("updated_at") or release.get("published_at"),
                    "prerelease": release["prerelease"],
                    "assets": assets,
                })
        
        # Cache the results
        releases_cache["data"] = processed_releases
        releases_cache["timestamp"] = current_time
        
        return processed_releases
        
    except Exception as e:
        print(f"[app] Error fetching releases from GitHub: {e}")
        # Return last cached data even if expired
        if releases_cache["data"] is not None:
            return releases_cache["data"]
        return []


def default_agent_snapshot():
    return {
        "agent_id": None,
        "mode": None,
        "iface": None,
        "port_mode": None,
        "custom_ports": [],
        "hostname": None,
        "function_codes_seen": [],
        "initiators_seen": [],
        "responders_seen": [],
        "read_patterns": [],
        "write_registers": [],
        "event_counts": {},
        "traffic_overview": {
            "clients_identified": 0,
            "servers_identified": 0,
            "function_codes_identified": [],
            "read_pattern_count": 0,
            "write_register_count": 0,
        },
        "timestamp": None,
    }


def default_agent_info():
    return {
        "connected": False,
        "agent_id": None,
        "hostname": None,
        "iface": None,
        "mode": None,
        "port_mode": None,
        "custom_ports": [],
        "running": False,
        "last_seen": None,
        "available_ifaces": [],
        "available_monitored_ifaces": [],
        "available_unmonitored_ifaces": [],
        "capabilities": [],
    }


def default_agent_config():
    return {
        "iface": "ALL",
        "mode": "MONITORING",
        "port_mode": "MODBUS_PORTS",
        "custom_ports": [],
        "updated_at": None,
    }


def normalize_custom_ports(value):
    if value is None:
        return []

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
            raise ValueError(f"Invalid port '{token}'")
        if port < 1 or port > 65535:
            raise ValueError(f"Port out of range: {port}")
        if port in seen:
            continue
        seen.add(port)
        ports.append(port)

    return ports


def safe_normalize_custom_ports(value):
    try:
        return normalize_custom_ports(value)
    except Exception:
        return []


def default_remote_server():
    return {
        "running": False,
        "host": "127.0.0.1",
        "port": 5020,
        "registers_preview": {
            "start": 0,
            "quantity": 0,
            "values": [],
        },
        "updated_at": None,
    }


def default_remote_client():
    return {
        "running": False,
        "host": "127.0.0.1",
        "port": 5020,
        "poll_interval": 1.0,
        "poll_start": 0,
        "poll_quantity": 4,
        "last_values": [],
        "last_error": None,
        "last_poll_at": None,
        "last_success_at": None,
        "updated_at": None,
    }

def default_process_sim():
    return {
        "running": False,
        "process_type": "tank_v1",
        "server": {
            "running": False,
            "host": "127.0.0.1",
            "port": 15020,
            "registers_preview": {
                "start": 0,
                "quantity": 0,
                "values": [],
            },
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

def default_modbus_summary():
    return {
        "detected": False,
        "protocol": "Modbus/TCP",
        "interface": None,
        "port": None,
        "client_ip": None,
        "server_ip": None,
        "functions_seen": [],
        "exception_functions_seen": [],
        "avg_polling_s": None,
        "writes_detected": False,
        "state": "Inactive",
        # Packet timestamp (may differ from backend wall-clock)
        "last_seen": None,
        # Backend ingestion timestamp (authoritative for UI liveness)
        "ingest_last_seen": None,
    }


def ensure_session_state(session_id: str):
    with lock:
        if session_id not in agents_by_session:
            agents_by_session[session_id] = {
                "events": deque(maxlen=300),
                "alerts": deque(maxlen=100),
                "logs": deque(maxlen=100),
                "agent_info": default_agent_info(),
                "agent_snapshot": default_agent_snapshot(),
                "agent_config": default_agent_config(),
                "remote_server": default_remote_server(),
                "remote_client": default_remote_client(),
                "process_sim": default_process_sim(),
                "modbus_summary": default_modbus_summary(),
                "connection_history": deque(maxlen=80),
                "pending_commands": [],
                "action_commands": deque(maxlen=80),
                "event_log_signatures": deque(maxlen=600),
                "recent_event_signatures": {},
                "recent_alert_signatures": {},
            }
        return agents_by_session[session_id]


def get_or_create_session_id(request: Request):
    session_id = request.cookies.get(SESSION_COOKIE)
    if not session_id:
        session_id = f"sess_{uuid.uuid4().hex}"
    return session_id


def get_session_state_from_request(request: Request):
    session_id = get_or_create_session_id(request)
    return session_id, ensure_session_state(session_id)


def set_session_cookie_if_needed(request: Request, response: Response, session_id: str):
    current = request.cookies.get(SESSION_COOKIE)
    if current != session_id:
        response.set_cookie(
            key=SESSION_COOKIE,
            value=session_id,
            httponly=False,
            samesite="lax",
        )


def _cleanup_recent_signature_cache(cache: dict, now_ts: float, ttl_s: float, max_items: int):
    if len(cache) <= max_items:
        return
    cutoff = now_ts - ttl_s
    stale_keys = [k for k, v in cache.items() if float(v) < cutoff]
    for key in stale_keys[: max_items]:
        cache.pop(key, None)


def _event_signature(event: dict):
    return (
        normalize_event_type(event.get("type")),
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
        tuple(event.get("values") or []),
    )


def _alert_signature(alert: dict):
    summary = str(alert.get("summary") or "")
    summary = re.sub(r"\s*\|\s*rtt=[0-9.]+", "", summary, flags=re.IGNORECASE).strip()
    return (
        normalize_event_type(alert.get("event_type")),
        alert.get("function_code"),
        alert.get("src"),
        alert.get("dst"),
        alert.get("register"),
        alert.get("start_addr"),
        alert.get("quantity"),
        alert.get("value"),
        alert.get("exception_code"),
        summary,
    )


def push_event(state: dict, event: dict):
    now_ts = time.time()
    signature = _event_signature(event)
    with lock:
        cache = state.get("recent_event_signatures")
        if cache is None:
            cache = {}
            state["recent_event_signatures"] = cache
        prev_ts = cache.get(signature)
        if prev_ts is not None and (now_ts - float(prev_ts)) <= 4.0:
            return
        cache[signature] = now_ts
        _cleanup_recent_signature_cache(cache, now_ts=now_ts, ttl_s=15.0, max_items=2000)
        state["events"].append(event)


def push_alert(state: dict, alert: dict):
    now_ts = time.time()
    signature = _alert_signature(alert)
    with lock:
        cache = state.get("recent_alert_signatures")
        if cache is None:
            cache = {}
            state["recent_alert_signatures"] = cache
        prev_ts = cache.get(signature)
        if prev_ts is not None and (now_ts - float(prev_ts)) <= 30.0:
            return
        cache[signature] = now_ts
        _cleanup_recent_signature_cache(cache, now_ts=now_ts, ttl_s=120.0, max_items=1200)
        state["alerts"].append(alert)


def push_log_for_session(session_id: str, message: str):
    print(message)
    state = ensure_session_state(session_id)
    with lock:
        state["logs"].append(message)

MODBUS_WRITE_FUNCTIONS = get_modbus_write_function_codes()
MODBUS_ACTIVE_WINDOW_SECONDS = 2.0
MAX_EVENT_CLOCK_DRIFT_SECONDS = 30.0


def normalize_event_type(event_type: str):
    return str(event_type or "").upper().strip()


def extract_event_client_server(payload: dict):
    event_type = normalize_event_type(payload.get("type"))

    src_ip = payload.get("src_ip")
    dst_ip = payload.get("dst_ip")
    src_port = payload.get("src_port")
    dst_port = payload.get("dst_port")

    client = payload.get("client")
    server = payload.get("server")

    if event_type in {"READ_REQUEST", "WRITE_REQUEST"}:
        client_ip = client or src_ip
        server_ip = server or dst_ip
        port = dst_port
    elif event_type in {"READ_RESPONSE", "WRITE_RESPONSE"}:
        client_ip = client or dst_ip
        server_ip = server or src_ip
        port = src_port
    else:
        client_ip = client or src_ip
        server_ip = server or dst_ip
        port = dst_port

    return client_ip, server_ip, port


def extract_avg_polling_from_snapshot(snapshot: dict, server_ip: str):
    if not snapshot:
        return None

    read_patterns = snapshot.get("read_patterns") or []
    if not isinstance(read_patterns, list):
        return None

    for pattern in read_patterns:
        if not isinstance(pattern, dict):
            continue

        pattern_server = pattern.get("server")
        avg_period = pattern.get("avg_period")

        if server_ip and pattern_server and pattern_server != server_ip:
            continue

        if avg_period is None:
            continue

        try:
            return round(float(avg_period), 2)
        except (TypeError, ValueError):
            continue

    return None


def resolve_event_time(payload: dict) -> float:
    """
    Resolve a safe event timestamp for state/liveness calculations.
    Uses packet timestamp when it is reasonably close to backend wall-clock.
    Falls back to current backend time when agent/system clocks are skewed.
    """
    now = time.time()
    raw_ts = payload.get("timestamp")
    try:
        event_ts = float(raw_ts) if raw_ts is not None else now
    except (TypeError, ValueError):
        event_ts = now

    if abs(event_ts - now) > MAX_EVENT_CLOCK_DRIFT_SECONDS:
        return now
    return event_ts


def update_modbus_summary_from_event(state: dict, payload: dict):
    summary = state["modbus_summary"]

    event_type = normalize_event_type(payload.get("type"))
    function_code = payload.get("function_code")

    client_ip, server_ip, port = extract_event_client_server(payload)
    iface = (
        payload.get("iface")
        or state["agent_info"].get("iface")
        or state["agent_config"].get("iface")
    )
    event_ts = resolve_event_time(payload)

    if function_code is None:
        return

    try:
        function_code = int(function_code)
    except (TypeError, ValueError):
        return

    is_exception = event_type == "EXCEPTION_RESPONSE"
    if function_code > 127:
        base_fc = function_code & 0x7F
    else:
        base_fc = function_code

    summary["detected"] = True
    summary["protocol"] = "Modbus/TCP"
    summary["interface"] = iface
    summary["port"] = port
    summary["client_ip"] = client_ip
    summary["server_ip"] = server_ip
    # Use packet timestamp to avoid delayed-queue artifacts.
    summary["last_seen"] = event_ts
    summary["ingest_last_seen"] = time.time()
    summary["state"] = "Active"

    existing_fc = set(summary.get("functions_seen") or [])
    exception_fc = set(summary.get("exception_functions_seen") or [])

    if is_exception:
        exception_fc.add(base_fc)
    else:
        existing_fc.add(base_fc)

    summary["functions_seen"] = sorted(existing_fc)
    summary["exception_functions_seen"] = sorted(exception_fc)

    if base_fc in MODBUS_WRITE_FUNCTIONS or event_type in {"WRITE_REQUEST", "WRITE_RESPONSE"}:
        summary["writes_detected"] = True

    avg_polling = payload.get("avg_polling_s")

    if avg_polling is None:
        avg_polling = extract_avg_polling_from_snapshot(
            state.get("agent_snapshot") or {},
            server_ip
        )

    if avg_polling is not None:
        try:
            summary["avg_polling_s"] = round(float(avg_polling), 2)
        except (TypeError, ValueError):
            pass

    update_connection_history_from_event(
        state=state,
        iface=iface,
        client_ip=client_ip,
        server_ip=server_ip,
        port=port,
        function_code=base_fc,
        is_exception=is_exception,
        is_write=(base_fc in MODBUS_WRITE_FUNCTIONS or event_type in {"WRITE_REQUEST", "WRITE_RESPONSE"}),
        event_ts=event_ts,
    )


def update_connection_history_from_event(
    state: dict,
    iface: str,
    client_ip: str,
    server_ip: str,
    port,
    function_code: int,
    is_exception: bool,
    is_write: bool,
    event_ts: float,
):
    history = state.get("connection_history")
    if history is None:
        history = deque(maxlen=80)
        state["connection_history"] = history

    def endpoint_host(endpoint: str):
        value = str(endpoint or "").strip()
        if not value:
            return value
        if value.count(":") == 1 and "." in value:
            return value.rsplit(":", 1)[0]
        return value

    client_host = endpoint_host(client_ip)
    server_host = endpoint_host(server_ip)
    key = f"{iface}|{client_host}|{server_host}|{port}"
    now = float(event_ts) if event_ts is not None else time.time()
    target = None

    for item in history:
        if item.get("key") == key:
            target = item
            break

    if target is None:
        stable_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"modbus|{key}"))
        target = {
            "id": str(uuid.uuid4()),
            "connection_id": stable_id,
            "key": key,
            "protocol": "Modbus/TCP",
            "interface": iface,
            "client_ip": client_ip,
            "client_host": client_host,
            "server_ip": server_ip,
            "server_host": server_host,
            "port": port,
            "first_seen": now,
            "last_seen": now,
            "event_count": 0,
            "functions_seen": [],
            "exception_functions_seen": [],
            "writes_detected": False,
            "reconnect_count": 0,
            "instance_id": 1,
        }
        history.appendleft(target)
    else:
        last_seen_prev = target.get("last_seen")
        if last_seen_prev is not None and (now - float(last_seen_prev)) > (MODBUS_ACTIVE_WINDOW_SECONDS * 1.5):
            target["reconnect_count"] = int(target.get("reconnect_count") or 0) + 1
            target["instance_id"] = int(target.get("instance_id") or 1) + 1
            target["first_seen"] = now
            target["event_count"] = 0
            target["functions_seen"] = []
            target["exception_functions_seen"] = []
            target["writes_detected"] = False
        target["last_seen"] = now
        target["client_ip"] = client_ip
        target["server_ip"] = server_ip
        target["client_host"] = client_host
        target["server_host"] = server_host

    target["event_count"] = int(target.get("event_count") or 0) + 1
    fc = set(target.get("functions_seen") or [])
    exc = set(target.get("exception_functions_seen") or [])
    if is_exception:
        exc.add(function_code)
    else:
        fc.add(function_code)
    target["functions_seen"] = sorted(fc)
    target["exception_functions_seen"] = sorted(exc)
    if is_write:
        target["writes_detected"] = True


def build_modbus_summary(state: dict):
    summary = dict(state.get("modbus_summary") or default_modbus_summary())

    if not summary.get("detected"):
        return {"detected": False}

    # Prefer backend ingestion time for liveness in UI to avoid packet-time skew effects.
    last_seen = summary.get("ingest_last_seen")
    if last_seen is None:
        last_seen = summary.get("last_seen")
    if last_seen is not None and (time.time() - last_seen > MODBUS_ACTIVE_WINDOW_SECONDS):
        summary["state"] = "Inactive"
    else:
        summary["state"] = "Active"

    return {
        "detected": bool(summary.get("detected")),
        "protocol": summary.get("protocol") or "Modbus/TCP",
        "interface": summary.get("interface"),
        "port": summary.get("port"),
        "client_ip": summary.get("client_ip"),
        "server_ip": summary.get("server_ip"),
        "functions_seen": summary.get("functions_seen") or [],
        "exception_functions_seen": summary.get("exception_functions_seen") or [],
        "avg_polling_s": summary.get("avg_polling_s"),
        "writes_detected": bool(summary.get("writes_detected")),
        "state": summary.get("state") or "Inactive",
    }


def build_connection_history(state: dict):
    now = time.time()
    rows = []
    for item in list(state.get("connection_history") or []):
        last_seen = item.get("last_seen")
        first_seen = item.get("first_seen")
        active = bool(last_seen is not None and (now - float(last_seen)) <= MODBUS_ACTIVE_WINDOW_SECONDS)
        duration_s = None
        if first_seen is not None and last_seen is not None:
            duration_s = round(max(0.0, float(last_seen) - float(first_seen)), 3)

        rows.append({
            "id": item.get("id"),
            "connection_id": item.get("connection_id"),
            "protocol": item.get("protocol") or "Modbus/TCP",
            "interface": item.get("interface"),
            "client_ip": item.get("client_ip"),
            "client_host": item.get("client_host"),
            "server_ip": item.get("server_ip"),
            "server_host": item.get("server_host"),
            "port": item.get("port"),
            "first_seen": first_seen,
            "last_seen": last_seen,
            "active": active,
            "age_s": round(max(0.0, now - float(last_seen)), 3) if last_seen is not None else None,
            "duration_s": duration_s,
            "event_count": item.get("event_count") or 0,
            "functions_seen": item.get("functions_seen") or [],
            "exception_functions_seen": item.get("exception_functions_seen") or [],
            "writes_detected": bool(item.get("writes_detected")),
            "reconnect_count": int(item.get("reconnect_count") or 0),
            "instance_id": int(item.get("instance_id") or 1),
        })
    return list(reversed(rows[:60]))


def build_command_log_message(command_type: str, payload: dict):
    if command_type == "START_SERVER":
        return f"Modbus server start requested ({payload.get('host', '-') }:{payload.get('port', '-')})"
    if command_type == "STOP_SERVER":
        return "Modbus server stop requested"
    if command_type == "START_CLIENT":
        return (
            f"Modbus client start requested "
            f"({payload.get('host', '-') }:{payload.get('port', '-')}, "
            f"poll={payload.get('poll_interval', '-') }s, "
            f"start={payload.get('poll_start', '-') }, qty={payload.get('poll_quantity', '-')})"
        )
    if command_type == "STOP_CLIENT":
        return "Modbus client stop requested"
    if command_type == "RUN_MODBUS_ACTION":
        return (
            f"Modbus action queued "
            f"({payload.get('function_id', '-')}, {payload.get('host', '-') }:{payload.get('port', '-')})"
        )
    if command_type == "START_PROCESS_SIM":
        return (
            "Process simulation start requested "
            f"({payload.get('host', '-') }:{payload.get('port', '-')}, "
            f"poll={payload.get('poll_interval', '-') }s, "
            f"start={payload.get('poll_start', '-') }, qty={payload.get('poll_quantity', '-')})"
        )
    if command_type == "STOP_PROCESS_SIM":
        return "Process simulation stop requested"
    if command_type == "WRITE_PROCESS_SIM":
        return (
            "Process simulation write requested "
            f"(HR{payload.get('address', '-') }={payload.get('value', '-')}, unit={payload.get('unit_id', 1)})"
        )
    return f"Command queued: {command_type}"


def queue_command(session_id: str, command_type: str, payload: dict):
    state = ensure_session_state(session_id)
    cmd = {
        "id": str(uuid.uuid4()),
        "type": command_type,
        "payload": payload,
        "created_at": time.time(),
    }
    with lock:
        if "action_commands" not in state:
            state["action_commands"] = deque(maxlen=80)
        state["pending_commands"].append(cmd)
        if command_type == "RUN_MODBUS_ACTION":
            state["action_commands"].appendleft({
                "id": cmd["id"],
                "status": "queued",
                "protocol": "modbus",
                "function_id": payload.get("function_id"),
                "function_name": payload.get("function_name"),
                "code_label": payload.get("code_label"),
                "created_at": cmd["created_at"],
                "updated_at": cmd["created_at"],
                "message": "Queued for agent execution",
            })

    push_log_for_session(session_id, build_command_log_message(command_type, payload))
    return cmd


def update_action_command_status(
    state: dict,
    command_id: str,
    status: str,
    message: str = "",
):
    history = state.get("action_commands") or []
    now = time.time()
    for entry in history:
        if entry.get("id") != command_id:
            continue
        entry["status"] = status
        entry["updated_at"] = now
        if message:
            entry["message"] = message
        return entry
    return None


def build_agent_config(request: Request, session_id: str, state: dict):
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")

    scheme = forwarded_proto or request.url.scheme
    host = forwarded_host or request.url.netloc
    server_url = f"{scheme}://{host}".rstrip("/")

    return {
        "server_url": server_url,
        "session_id": session_id,
        "iface": state["agent_config"].get("iface") or "ALL",
        "mode": state["agent_config"].get("mode") or "MONITORING",
        "port_mode": state["agent_config"].get("port_mode") or "MODBUS_PORTS",
        "custom_ports": safe_normalize_custom_ports(state["agent_config"].get("custom_ports")),
    }


def is_agent_connected(state: dict, now_ts: float | None = None) -> bool:
    if now_ts is None:
        now_ts = time.time()
    agent_info = state.get("agent_info") or {}
    last_seen = agent_info.get("last_seen")
    return bool(last_seen is not None and (float(now_ts) - float(last_seen) <= 20))


def should_log_agent_event(state: dict, payload: dict) -> bool:
    event_type = normalize_event_type(payload.get("type"))
    function_code = payload.get("function_code")
    try:
        function_code = int(function_code) if function_code is not None else None
    except (TypeError, ValueError):
        function_code = None

    # Always log high-value events.
    if event_type in {"WRITE_REQUEST", "EXCEPTION_RESPONSE", "UNKNOWN_REQUEST"}:
        return True

    # Ignore routine traffic noise.
    if event_type in {"WRITE_RESPONSE", "READ_RESPONSE", "GENERIC_RESPONSE"}:
        return False

    # For read requests, only log when pattern/function changes (new/different read).
    if event_type == "READ_REQUEST":
        signature = (
            event_type,
            function_code,
            payload.get("server"),
            payload.get("start_addr"),
            payload.get("quantity"),
        )
        seen = state.get("event_log_signatures")
        if seen is None:
            seen = deque(maxlen=600)
            state["event_log_signatures"] = seen
        if signature in seen:
            return False
        seen.append(signature)
        return True

    # Keep generic/other requests only when function changes.
    if event_type in {"GENERIC_REQUEST"}:
        signature = (event_type, function_code, payload.get("server"))
        seen = state.get("event_log_signatures")
        if seen is None:
            seen = deque(maxlen=600)
            state["event_log_signatures"] = seen
        if signature in seen:
            return False
        seen.append(signature)
        return True

    return False


def ingest_agent_event_payload(state: dict, session_id: str, payload: dict):
    agent_info = state["agent_info"]
    agent_info["connected"] = True
    agent_info["last_seen"] = time.time()

    push_event(state, payload)
    update_modbus_summary_from_event(state, payload)

    if not should_log_agent_event(state, payload):
        return

    summary = payload.get("summary")
    if summary:
        push_log_for_session(session_id, summary)
    else:
        push_log_for_session(
            session_id,
            f"Modbus event detected: {payload.get('type', 'UNKNOWN')} "
            f"({payload.get('src_ip')}:{payload.get('src_port')} -> "
            f"{payload.get('dst_ip')}:{payload.get('dst_port')})"
        )


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    session_id, _state = get_session_state_from_request(request)
    response = templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
    )
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.get("/downloads", response_class=HTMLResponse)
def downloads_page(request: Request):
    """Serve the downloads page with installation instructions"""
    session_id, _state = get_session_state_from_request(request)
    response = templates.TemplateResponse(
        request=request,
        name="downloads.html",
        context={}
    )
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.get("/downloads/agent/{platform}")
def download_agent_file(platform: str, request: Request):
    """
    Downloads a simplified bundle ZIP (agent + install script + agent-config.json).
    Platform: "windows", "macos", "linux"
    """
    session_id, state = get_session_state_from_request(request)
    
    platform_config = {
        "windows": {
            "agent_path": BASE_DIR / "downloads" / "agent" / "windows" / "otlab-agent.exe",
            "agent_name": "otlab-agent-windows-amd64.exe",
            "script_path": BASE_DIR / "scripts" / "install-windows.bat",
            "script_name": "install-windows.bat",
            "zip_name": "otlab-agent-windows.zip"
        },
        "macos": {
            "agent_path": BASE_DIR / "downloads" / "agent" / "mac" / "otlab-agent-mac",
            "agent_name": "otlab-agent-macos-amd64",
            "script_path": BASE_DIR / "scripts" / "install-macos.sh",
            "script_name": "install-macos.sh",
            "zip_name": "otlab-agent-macos.zip"
        },
        "linux": {
            "agent_path": BASE_DIR / "downloads" / "agent" / "linux" / "otlab-agent-linux",
            "agent_name": "otlab-agent-linux-amd64",
            "script_path": BASE_DIR / "scripts" / "install-linux.sh",
            "script_name": "install-linux.sh",
            "zip_name": "otlab-agent-linux.zip"
        },
    }
    
    if platform not in platform_config:
        return JSONResponse({"error": "Invalid platform"}, status_code=400)
    
    config = platform_config[platform]
    script_path = config["script_path"]
    runtime_config = build_agent_config(request, session_id, state)

    # Try to fetch the newest agent binary from GitHub releases first.
    # Fallback to local bundled copy if unavailable.
    agent_bytes = None
    agent_source = "local"

    try:
        releases = get_github_releases(force_refresh=True) or []
        for release in releases:
            assets = release.get("assets") or {}
            asset = assets.get(platform)
            if not asset:
                continue

            asset_url = asset.get("url")
            if not asset_url:
                continue

            fetch_resp = requests.get(asset_url, timeout=20)
            fetch_resp.raise_for_status()
            agent_bytes = fetch_resp.content
            agent_source = f"github:{release.get('tag', 'unknown')}"
            break
    except Exception as e:
        print(f"[app] failed to fetch agent from GitHub releases ({platform}): {e}")

    if agent_bytes is None:
        agent_path = config["agent_path"]
        if not agent_path.exists():
            return JSONResponse({"error": "Agent file not found (release + local fallback unavailable)"}, status_code=404)
        with open(agent_path, "rb") as f:
            agent_bytes = f.read()
        agent_source = "local-fallback"

    if not script_path.exists():
        return JSONResponse({"error": "Installation script not found"}, status_code=404)
    
    # Create ZIP with only the files needed for one-click install/run.
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(config["agent_name"], agent_bytes)
        zf.write(script_path, arcname=config["script_name"])
        zf.writestr("agent-config.json", json.dumps(runtime_config, indent=2))

    push_log_for_session(session_id, f"Agent bundle generated for {platform} (binary source={agent_source})")
    
    zip_buffer.seek(0)
    
    response = Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={config['zip_name']}"}
    )
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.get("/downloads/script/{platform}")
def download_install_script(platform: str, request: Request):
    """
    Downloads the installation script for the platform.
    Platform: "windows", "macos", "linux"
    """
    session_id, state = get_session_state_from_request(request)
    
    script_map = {
        "windows": ("scripts/install-windows.bat", "install-windows.bat"),
        "macos": ("scripts/install-macos.sh", "install-macos.sh"),
        "linux": ("scripts/install-linux.sh", "install-linux.sh"),
    }
    
    if platform not in script_map:
        return JSONResponse({"error": "Invalid platform"}, status_code=400)
    
    script_path_rel, filename = script_map[platform]
    script_path = BASE_DIR / script_path_rel
    
    if not script_path.exists():
        return JSONResponse({"error": "Script file not found"}, status_code=404)
    
    with open(script_path, "r") as f:
        content = f.read()
    
    media_type = "text/x-shellscript" if platform != "windows" else "text/plain"
    response = Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.get("/downloads/docs")
def download_installation_guide(request: Request):
    """Download the complete installation guide (INSTALLATION.md)"""
    session_id, state = get_session_state_from_request(request)
    
    guide_path = BASE_DIR / "scripts" / "INSTALLATION.md"
    
    if not guide_path.exists():
        return JSONResponse({"error": "Documentation file not found"}, status_code=404)
    
    with open(guide_path, "r") as f:
        content = f.read()
    
    response = Response(
        content=content,
        media_type="text/markdown",
        headers={"Content-Disposition": "attachment; filename=INSTALLATION.md"}
    )
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.get("/api/status")
def api_status(request: Request):
    session_id, state = get_session_state_from_request(request)
    now = time.time()

    agent_info = state["agent_info"]
    connected = is_agent_connected(state, now)
    agent_info["connected"] = connected

    response = JSONResponse({
        "agent": agent_info,
        "monitor": {
            "running": connected and agent_info["running"],
            "iface": agent_info["iface"] or "-",
            "mode": agent_info["mode"] or "-",
            "snapshot": state["agent_snapshot"],
        },
        "server": state["remote_server"],
        "client": state["remote_client"],
        "process_sim": state.get("process_sim") or default_process_sim(),
        "agent_config": state["agent_config"],
        "session_id": session_id,
    })

    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.get("/api/events")
def api_events(request: Request):
    session_id, state = get_session_state_from_request(request)
    response = JSONResponse({
        "events": list(state["events"]),
        "alerts": list(state["alerts"]),
        "logs": list(state["logs"]),
        "modbus_summary": build_modbus_summary(state),
        "connection_history": build_connection_history(state),
        "session_id": session_id,
    })
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.get("/api/process-sim/status")
def api_process_sim_status(request: Request):
    session_id, state = get_session_state_from_request(request)
    response = JSONResponse({"ok": True, "process_sim": state.get("process_sim") or default_process_sim(), "session_id": session_id})
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.post("/api/process-sim/start")
async def api_process_sim_start(request: Request):
    session_id, state = get_session_state_from_request(request)
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}

    if not is_agent_connected(state):
        response = JSONResponse(
            {"ok": False, "error": "Agent local desconectado. Inicie o agente para rodar o simulador local."},
            status_code=400,
        )
        set_session_cookie_if_needed(request, response, session_id)
        return response

    host = str(payload.get("host") or "127.0.0.1")
    port = int(payload.get("port") or 15020)
    poll_interval = float(payload.get("poll_interval") or 0.5)
    poll_start = int(payload.get("poll_start") or 0)
    poll_quantity = int(payload.get("poll_quantity") or 16)
    process_type = str(payload.get("process_type") or "tank_v1")

    queue_command(
        session_id,
        "START_PROCESS_SIM",
        {
            "host": host,
            "port": port,
            "poll_interval": poll_interval,
            "poll_start": poll_start,
            "poll_quantity": poll_quantity,
            "process_type": process_type,
        },
    )
    response = JSONResponse({"ok": True, "queued": True, "process_sim": state.get("process_sim") or default_process_sim()})
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.post("/api/process-sim/stop")
def api_process_sim_stop(request: Request):
    session_id, state = get_session_state_from_request(request)
    if not is_agent_connected(state):
        response = JSONResponse(
            {"ok": False, "error": "Agent local desconectado. Inicie o agente para controlar o simulador."},
            status_code=400,
        )
        set_session_cookie_if_needed(request, response, session_id)
        return response

    queue_command(session_id, "STOP_PROCESS_SIM", {})
    response = JSONResponse({"ok": True, "queued": True, "process_sim": state.get("process_sim") or default_process_sim()})
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.post("/api/process-sim/write")
async def api_process_sim_write(request: Request):
    session_id, state = get_session_state_from_request(request)
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    try:
        address = int(payload.get("address"))
        value = int(payload.get("value"))
        unit_id = int(payload.get("unit_id", 1))
    except Exception:
        response = JSONResponse({"ok": False, "error": "Invalid address/value"}, status_code=400)
        set_session_cookie_if_needed(request, response, session_id)
        return response

    if not is_agent_connected(state):
        response = JSONResponse(
            {"ok": False, "error": "Agent local desconectado. Inicie o agente para escrever no simulador."},
            status_code=400,
        )
        set_session_cookie_if_needed(request, response, session_id)
        return response

    queue_command(
        session_id,
        "WRITE_PROCESS_SIM",
        {"address": address, "value": value, "unit_id": unit_id},
    )
    response = JSONResponse({"ok": True, "queued": True, "process_sim": state.get("process_sim") or default_process_sim()})
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.get("/api/actions/definitions")
def api_actions_definitions(request: Request):
    session_id, _state = get_session_state_from_request(request)
    response = JSONResponse({
        "ok": True,
        "protocols": [
            {
                "id": "modbus",
                "name": "Modbus",
                "functions": get_modbus_function_definitions(),
            }
        ],
    })
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.post("/api/actions/modbus/execute")
def api_execute_modbus_action(request: Request, payload: dict = Body(default={})):
    session_id, state = get_session_state_from_request(request)
    agent_info = state.get("agent_info") or {}
    if not agent_info.get("connected"):
        response = JSONResponse(
            {"ok": False, "error": "Agent is not connected. Connect the local agent first."},
            status_code=409,
        )
        set_session_cookie_if_needed(request, response, session_id)
        return response

    capabilities = set(agent_info.get("capabilities") or [])

    if "modbus_actions_v1" not in capabilities:
        response = JSONResponse(
            {
                "ok": False,
                "error": (
                    "Connected agent does not support Modbus Actions execution yet. "
                    "Please update/restart the local agent and reconnect."
                ),
                "required_capability": "modbus_actions_v1",
                "agent_capabilities": sorted(capabilities),
            },
            status_code=409,
        )
        set_session_cookie_if_needed(request, response, session_id)
        return response

    try:
        function_def, normalized = validate_modbus_action_payload(payload)
    except ModbusValidationError as exc:
        response = JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        set_session_cookie_if_needed(request, response, session_id)
        return response

    transaction_id = int(time.time() * 1000) & 0xFFFF
    built = build_modbus_tcp_request(function_def, normalized, transaction_id=transaction_id)

    cmd_payload = {
        "host": normalized["host"],
        "port": normalized["port"],
        "function_id": normalized["function_id"],
        "function_name": function_def["name"],
        "code_label": function_def.get("code_label"),
        "values": normalized,
        "request_hex": built["request_hex"],
    }
    queued = queue_command(session_id, "RUN_MODBUS_ACTION", cmd_payload)

    response = JSONResponse({
        "ok": True,
        "command_id": queued["id"],
        "function": {
            "id": function_def["id"],
            "code": function_def["code"],
            "code_label": function_def.get("code_label"),
            "name": function_def["name"],
        },
        "preview": {
            "host": normalized["host"],
            "port": normalized["port"],
            "unit_id": built["unit_id"],
            "transaction_id": built["transaction_id"],
            "request_hex": built["request_hex"],
            "pdu_hex": built["pdu_hex"],
        },
    })
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.get("/api/actions/modbus/commands")
def api_modbus_action_commands(request: Request):
    session_id, state = get_session_state_from_request(request)
    history = list(state.get("action_commands") or [])
    response = JSONResponse({"ok": True, "commands": history, "session_id": session_id})
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.get("/api/agent/interfaces")
def get_agent_interfaces(request: Request):
    session_id, state = get_session_state_from_request(request)
    agent_info = state["agent_info"]

    response = JSONResponse({
        "ok": True,
        "connected": agent_info["connected"],
        "interfaces": agent_info.get("available_ifaces", []),
        "monitored_interfaces": agent_info.get("available_monitored_ifaces", []),
        "unmonitored_interfaces": agent_info.get("available_unmonitored_ifaces", []),
        "current": agent_info.get("iface"),
        "session_id": session_id,
    })
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.get("/api/downloads/agent/windows")
def download_agent_windows(request: Request):
    return download_agent_file("windows", request)


@app.get("/api/downloads/agent/mac")
def download_agent_mac(request: Request):
    return download_agent_file("macos", request)


@app.get("/api/downloads/agent/linux")
def download_agent_linux(request: Request):
    return download_agent_file("linux", request)


@app.get("/api/agent/config/download")
def download_agent_config(request: Request):
    session_id, state = get_session_state_from_request(request)
    config = build_agent_config(request, session_id, state)

    response = Response(
        content=json.dumps(config, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=agent-config.json"}
    )
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.get("/api/releases/agent")
def get_agent_releases(request: Request):
    """
    Get available agent releases from GitHub.
    Returns the latest release and development builds.
    """
    session_id, state = get_session_state_from_request(request)
    
    refresh_param = str(request.query_params.get("refresh", "")).lower().strip()
    force_refresh = refresh_param in {"1", "true", "yes", "y"}
    releases = get_github_releases(force_refresh=force_refresh)
    
    if not releases:
        return JSONResponse({
            "ok": False,
            "error": "No releases available",
            "releases": []
        }, status_code=503)
    
    # Find the latest stable release and dev-latest using update time.
    # This avoids showing old "published_at" dates for long-lived tags.
    def _release_sort_key(rel: dict):
        return str(rel.get("updated_at") or rel.get("published_at") or "")

    latest_stable = None
    stable_releases = [r for r in releases if not r.get("prerelease")]
    if stable_releases:
        latest_stable = max(stable_releases, key=_release_sort_key)

    dev_latest = None
    dev_releases = [r for r in releases if r.get("tag") == "dev-latest"]
    if dev_releases:
        dev_latest = max(dev_releases, key=_release_sort_key)
    
    # Prepare response
    available_releases = []
    if latest_stable:
        available_releases.append({"type": "stable", **latest_stable})
    if dev_latest:
        available_releases.append({"type": "development", **dev_latest})
    
    response = JSONResponse({
        "ok": True,
        "releases": available_releases
    })
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.get("/api/agent/config")
def get_agent_config(request: Request, session_id: str = None):
    effective_session_id = session_id or get_or_create_session_id(request)
    state = ensure_session_state(effective_session_id)

    response = JSONResponse({
        "ok": True,
        "config": state["agent_config"],
    })

    if session_id is None:
        set_session_cookie_if_needed(request, response, effective_session_id)

    return response


@app.post("/api/agent/config")
async def set_agent_config(request: Request):
    session_id, state = get_session_state_from_request(request)
    data = await request.json()

    iface = data.get("iface", state["agent_config"]["iface"])
    mode = data.get("mode", state["agent_config"]["mode"])
    port_mode = data.get("port_mode", state["agent_config"].get("port_mode", "MODBUS_PORTS"))

    if mode not in ["LEARNING", "MONITORING"]:
        return JSONResponse({"ok": False, "error": "Invalid mode"}, status_code=400)
    if port_mode not in ["ALL_PORTS", "MODBUS_PORTS", "CUSTOM"]:
        return JSONResponse({"ok": False, "error": "Invalid port_mode"}, status_code=400)

    try:
        custom_ports = normalize_custom_ports(data.get("custom_ports", state["agent_config"].get("custom_ports", [])))
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    if port_mode == "CUSTOM" and not custom_ports:
        return JSONResponse({"ok": False, "error": "CUSTOM port_mode requires at least one custom port"}, status_code=400)

    old_iface = state["agent_config"]["iface"]
    old_mode = state["agent_config"]["mode"]
    old_port_mode = state["agent_config"].get("port_mode", "MODBUS_PORTS")
    old_custom_ports = safe_normalize_custom_ports(state["agent_config"].get("custom_ports", []))
    iface_changed = old_iface != iface
    mode_changed = old_mode != mode
    port_mode_changed = old_port_mode != port_mode
    custom_ports_changed = old_custom_ports != custom_ports

    state["agent_config"]["iface"] = iface
    state["agent_config"]["mode"] = mode
    state["agent_config"]["port_mode"] = port_mode
    state["agent_config"]["custom_ports"] = custom_ports
    state["agent_config"]["updated_at"] = time.time()

    detection_scope_changed = iface_changed or port_mode_changed or custom_ports_changed

    if detection_scope_changed:
        state["modbus_summary"] = default_modbus_summary()
        if "event_log_signatures" in state:
            state["event_log_signatures"].clear()
        if "recent_event_signatures" in state:
            state["recent_event_signatures"].clear()
        if "recent_alert_signatures" in state:
            state["recent_alert_signatures"].clear()
        push_log_for_session(
            session_id,
            (
                f"Detection scope changed (iface: {old_iface}->{iface}, "
                f"port_mode: {old_port_mode}->{port_mode}, "
                f"custom_ports: {old_custom_ports}->{custom_ports}) - resetting detection"
            ),
        )
    else:
        changed_keys = []
        if mode_changed:
            changed_keys.append(f"mode={mode}")
        if not changed_keys:
            changed_keys.append("no-op")
        push_log_for_session(session_id, f"Monitor configuration updated ({', '.join(changed_keys)})")

    response = JSONResponse({
        "ok": True,
        "config": state["agent_config"],
    })
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.post("/api/agent/server/configure")
async def configure_server(request: Request):
    session_id, state = get_session_state_from_request(request)
    data = await request.json()

    host = data.get("host", state["remote_server"]["host"])
    port = int(data.get("port", state["remote_server"]["port"]))

    state["remote_server"]["host"] = host
    state["remote_server"]["port"] = port
    state["remote_server"]["updated_at"] = time.time()

    push_log_for_session(session_id, f"Server configuration updated (host={host}, port={port})")

    response = JSONResponse({"ok": True, "server": state["remote_server"]})
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.post("/api/agent/client/configure")
async def configure_client(request: Request):
    session_id, state = get_session_state_from_request(request)
    data = await request.json()

    host = data.get("host", state["remote_client"]["host"])
    port = int(data.get("port", state["remote_client"]["port"]))
    poll_interval = float(data.get("poll_interval", state["remote_client"]["poll_interval"]))
    poll_start = int(data.get("poll_start", state["remote_client"]["poll_start"]))
    poll_quantity = int(data.get("poll_quantity", state["remote_client"]["poll_quantity"]))

    state["remote_client"]["host"] = host
    state["remote_client"]["port"] = port
    state["remote_client"]["poll_interval"] = poll_interval
    state["remote_client"]["poll_start"] = poll_start
    state["remote_client"]["poll_quantity"] = poll_quantity
    state["remote_client"]["updated_at"] = time.time()

    push_log_for_session(session_id, f"Client configuration updated (host={host}, port={port}, poll={poll_interval}s)")

    response = JSONResponse({"ok": True, "client": state["remote_client"]})
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.post("/api/agent/server/start")
async def agent_server_start(request: Request):
    session_id, state = get_session_state_from_request(request)
    data = await request.json()

    host = data.get("host", state["remote_server"]["host"])
    port = int(data.get("port", state["remote_server"]["port"]))

    state["remote_server"]["host"] = host
    state["remote_server"]["port"] = port
    state["remote_server"]["updated_at"] = time.time()
    state["remote_server"]["running"] = True

    queue_command(session_id, "START_SERVER", {"host": host, "port": port})

    response = JSONResponse({"ok": True, "server": state["remote_server"]})
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.post("/api/agent/server/stop")
def agent_server_stop(request: Request):
    session_id, state = get_session_state_from_request(request)
    state["remote_server"]["running"] = False
    state["remote_server"]["updated_at"] = time.time()

    queue_command(session_id, "STOP_SERVER", {})

    response = JSONResponse({"ok": True, "server": state["remote_server"]})
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.post("/api/agent/client/start")
async def agent_client_start(request: Request):
    session_id, state = get_session_state_from_request(request)
    data = await request.json()

    host = data.get("host", state["remote_client"]["host"])
    port = int(data.get("port", state["remote_client"]["port"]))
    poll_interval = float(data.get("poll_interval", state["remote_client"]["poll_interval"]))
    poll_start = int(data.get("poll_start", state["remote_client"]["poll_start"]))
    poll_quantity = int(data.get("poll_quantity", state["remote_client"]["poll_quantity"]))

    state["remote_client"]["host"] = host
    state["remote_client"]["port"] = port
    state["remote_client"]["poll_interval"] = poll_interval
    state["remote_client"]["poll_start"] = poll_start
    state["remote_client"]["poll_quantity"] = poll_quantity
    state["remote_client"]["updated_at"] = time.time()
    state["remote_client"]["running"] = True

    queue_command(session_id, "START_CLIENT", {
        "host": host,
        "port": port,
        "poll_interval": poll_interval,
        "poll_start": poll_start,
        "poll_quantity": poll_quantity,
    })

    response = JSONResponse({"ok": True, "client": state["remote_client"]})
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.post("/api/agent/client/stop")
def agent_client_stop(request: Request):
    session_id, state = get_session_state_from_request(request)
    state["remote_client"]["running"] = False
    state["remote_client"]["updated_at"] = time.time()

    queue_command(session_id, "STOP_CLIENT", {})

    response = JSONResponse({"ok": True, "client": state["remote_client"]})
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.get("/api/agent/commands")
def get_agent_commands(session_id: str):
    state = ensure_session_state(session_id)
    with lock:
        commands = list(state["pending_commands"])
        state["pending_commands"].clear()
        for cmd in commands:
            if cmd.get("type") == "RUN_MODBUS_ACTION":
                update_action_command_status(
                    state,
                    command_id=cmd.get("id"),
                    status="sent",
                    message="Delivered to agent",
                )
    return {"ok": True, "commands": commands}


@app.post("/api/agent/command_result")
def agent_command_result(payload: dict = Body(...)):
    session_id = payload.get("session_id")
    command_id = payload.get("command_id")
    status = str(payload.get("status") or "").strip().lower()
    message = str(payload.get("message") or "").strip()

    if not session_id or not command_id:
        return JSONResponse({"ok": False, "error": "Missing session_id or command_id"}, status_code=400)

    if status not in {"done", "error"}:
        status = "done"

    state = ensure_session_state(session_id)
    with lock:
        updated = update_action_command_status(
            state,
            command_id=command_id,
            status=status,
            message=message or ("Completed" if status == "done" else "Execution failed"),
        )

    if updated and status == "error":
        push_log_for_session(session_id, f"Modbus action failed: {updated.get('code_label', '-') } {updated.get('function_name', '-') } | {updated.get('message', '-')}")

    return {"ok": True}


@app.post("/api/agent/runtime")
def agent_runtime_update(payload: dict = Body(...)):
    session_id = payload.get("session_id")
    if not session_id:
        return JSONResponse({"ok": False, "error": "Missing session_id"}, status_code=400)

    state = ensure_session_state(session_id)

    server_data = payload.get("server") or {}
    client_data = payload.get("client") or {}
    process_data = payload.get("process_sim") or {}

    previous_server_running = state["remote_server"]["running"]
    previous_client_running = state["remote_client"]["running"]

    # Only update running status from agent; configuration is managed via /api/agent/server/configure and /api/agent/client/configure
    if server_data:
        state["remote_server"].update({
            "running": bool(server_data.get("running", state["remote_server"]["running"])),
            "updated_at": time.time(),
        })
        registers_preview = server_data.get("registers_preview")
        if isinstance(registers_preview, dict):
            values = registers_preview.get("values") or []
            if isinstance(values, list):
                safe_values = []
                for raw in values[:64]:
                    try:
                        safe_values.append(int(raw))
                    except Exception:
                        continue
                state["remote_server"]["registers_preview"] = {
                    "start": int(registers_preview.get("start", 0) or 0),
                    "quantity": int(registers_preview.get("quantity", len(safe_values)) or len(safe_values)),
                    "values": safe_values,
                }

    if client_data:
        state["remote_client"].update({
            "running": bool(client_data.get("running", state["remote_client"]["running"])),
            "updated_at": time.time(),
        })
        values = client_data.get("last_values")
        if isinstance(values, list):
            safe_values = []
            for raw in values[:64]:
                try:
                    safe_values.append(int(raw))
                except Exception:
                    continue
            state["remote_client"]["last_values"] = safe_values
        if "last_error" in client_data:
            state["remote_client"]["last_error"] = client_data.get("last_error")
        if "last_poll_at" in client_data:
            try:
                state["remote_client"]["last_poll_at"] = float(client_data.get("last_poll_at"))
            except Exception:
                pass
        if "last_success_at" in client_data:
            try:
                state["remote_client"]["last_success_at"] = float(client_data.get("last_success_at"))
            except Exception:
                pass

    if process_data:
        current = state.get("process_sim") or default_process_sim()
        server_block = process_data.get("server") or {}
        client_block = process_data.get("client") or {}

        process_snapshot = {
            "running": bool(process_data.get("running", current.get("running", False))),
            "process_type": str(process_data.get("process_type") or current.get("process_type") or "tank_v1"),
            "server": {
                "running": bool(server_block.get("running", current.get("server", {}).get("running", False))),
                "host": str(server_block.get("host") or current.get("server", {}).get("host") or "127.0.0.1"),
                "port": int(server_block.get("port") or current.get("server", {}).get("port") or 15020),
                "registers_preview": {
                    "start": 0,
                    "quantity": 0,
                    "values": [],
                },
            },
            "client": {
                "running": bool(client_block.get("running", current.get("client", {}).get("running", False))),
                "host": str(client_block.get("host") or current.get("client", {}).get("host") or "127.0.0.1"),
                "port": int(client_block.get("port") or current.get("client", {}).get("port") or 15020),
                "poll_interval": float(client_block.get("poll_interval") or current.get("client", {}).get("poll_interval") or 0.5),
                "poll_start": int(client_block.get("poll_start") or current.get("client", {}).get("poll_start") or 0),
                "poll_quantity": int(client_block.get("poll_quantity") or current.get("client", {}).get("poll_quantity") or 16),
                "last_values": [],
                "last_error": client_block.get("last_error", current.get("client", {}).get("last_error")),
                "last_poll_at": client_block.get("last_poll_at", current.get("client", {}).get("last_poll_at")),
                "last_success_at": client_block.get("last_success_at", current.get("client", {}).get("last_success_at")),
            },
        }

        registers_preview = server_block.get("registers_preview")
        if isinstance(registers_preview, dict):
            values = registers_preview.get("values") or []
            safe_values = []
            if isinstance(values, list):
                for raw in values[:64]:
                    try:
                        safe_values.append(int(raw))
                    except Exception:
                        continue
            process_snapshot["server"]["registers_preview"] = {
                "start": int(registers_preview.get("start", 0) or 0),
                "quantity": int(registers_preview.get("quantity", len(safe_values)) or len(safe_values)),
                "values": safe_values,
            }

        last_values = client_block.get("last_values")
        if isinstance(last_values, list):
            safe_values = []
            for raw in last_values[:64]:
                try:
                    safe_values.append(int(raw))
                except Exception:
                    continue
            process_snapshot["client"]["last_values"] = safe_values
        else:
            current_values = (current.get("client") or {}).get("last_values") or []
            process_snapshot["client"]["last_values"] = list(current_values)[:64]

        state["process_sim"] = process_snapshot

    current_server_running = state["remote_server"]["running"]
    current_client_running = state["remote_client"]["running"]

    if not previous_server_running and current_server_running:
        push_log_for_session(
            session_id,
            f"Modbus server running on {state['remote_server']['host']}:{state['remote_server']['port']}"
        )
    elif previous_server_running and not current_server_running:
        push_log_for_session(session_id, "Modbus server stopped")

    if not previous_client_running and current_client_running:
        push_log_for_session(
            session_id,
            f"Modbus client running on {state['remote_client']['host']}:{state['remote_client']['port']} "
            f"(poll={state['remote_client']['poll_interval']}s, start={state['remote_client']['poll_start']}, qty={state['remote_client']['poll_quantity']})"
        )
    elif previous_client_running and not current_client_running:
        push_log_for_session(session_id, "Modbus client stopped")

    return {"ok": True}


@app.post("/api/reset")
def reset_system(request: Request):
    session_id, state = get_session_state_from_request(request)

    with lock:
        # Visual clean only: keep monitor/server/client configuration as-is.
        state["events"].clear()
        state["alerts"].clear()
        state["logs"].clear()
        state["agent_snapshot"] = default_agent_snapshot()
        state["modbus_summary"] = default_modbus_summary()
        if "connection_history" in state:
            state["connection_history"].clear()
        if "event_log_signatures" in state:
            state["event_log_signatures"].clear()
        if "recent_event_signatures" in state:
            state["recent_event_signatures"].clear()
        if "recent_alert_signatures" in state:
            state["recent_alert_signatures"].clear()
        state["pending_commands"].clear()
        if "action_commands" in state:
            state["action_commands"].clear()

    response = JSONResponse({"ok": True, "session_id": session_id})
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.post("/api/agent/register")
def agent_register(payload: dict = Body(...)):
    session_id = payload.get("session_id")
    if not session_id:
        return JSONResponse({"ok": False, "error": "Missing session_id"}, status_code=400)

    state = ensure_session_state(session_id)
    agent_info = state["agent_info"]

    agent_info["connected"] = True
    agent_info["agent_id"] = payload.get("agent_id")
    agent_info["hostname"] = payload.get("hostname")
    agent_info["iface"] = payload.get("iface")
    agent_info["mode"] = payload.get("mode")
    agent_info["port_mode"] = payload.get("port_mode")
    agent_info["custom_ports"] = safe_normalize_custom_ports(payload.get("custom_ports"))
    agent_info["running"] = payload.get("running", False)
    agent_info["last_seen"] = payload.get("timestamp", time.time())
    agent_info["available_ifaces"] = payload.get("available_ifaces", [])
    agent_info["available_monitored_ifaces"] = payload.get("available_monitored_ifaces", [])
    agent_info["available_unmonitored_ifaces"] = payload.get("available_unmonitored_ifaces", [])
    agent_info["capabilities"] = payload.get("capabilities", agent_info.get("capabilities", []))

    push_log_for_session(
        session_id,
        (
            "Agent connected "
            f"({payload.get('hostname', '-')}, interface={payload.get('iface', '-')}, "
            f"mode={payload.get('mode', '-')}, port_mode={payload.get('port_mode', '-')})"
        )
    )

    return {
        "ok": True,
        "config": state["agent_config"],
        "server": state["remote_server"],
        "client": state["remote_client"],
    }


@app.post("/api/agent/heartbeat")
def agent_heartbeat(payload: dict = Body(...)):
    session_id = payload.get("session_id")
    if not session_id:
        return JSONResponse({"ok": False, "error": "Missing session_id"}, status_code=400)

    state = ensure_session_state(session_id)
    agent_info = state["agent_info"]

    agent_info["connected"] = True
    agent_info["agent_id"] = payload.get("agent_id")
    agent_info["hostname"] = payload.get("hostname")
    agent_info["iface"] = payload.get("iface")
    agent_info["mode"] = payload.get("mode")
    agent_info["port_mode"] = payload.get("port_mode")
    agent_info["custom_ports"] = safe_normalize_custom_ports(payload.get("custom_ports"))
    agent_info["running"] = payload.get("running", False)
    agent_info["last_seen"] = payload.get("timestamp", time.time())
    agent_info["available_ifaces"] = payload.get(
        "available_ifaces",
        agent_info.get("available_ifaces", [])
    )
    agent_info["available_monitored_ifaces"] = payload.get(
        "available_monitored_ifaces",
        agent_info.get("available_monitored_ifaces", []),
    )
    agent_info["available_unmonitored_ifaces"] = payload.get(
        "available_unmonitored_ifaces",
        agent_info.get("available_unmonitored_ifaces", []),
    )
    agent_info["capabilities"] = payload.get(
        "capabilities",
        agent_info.get("capabilities", [])
    )

    return {
        "ok": True,
        "config": state["agent_config"],
        "server": state["remote_server"],
        "client": state["remote_client"],
    }


@app.post("/api/agent/snapshot")
def agent_snapshot_ingest(payload: dict = Body(...)):
    session_id = payload.get("session_id")
    if not session_id:
        return JSONResponse({"ok": False, "error": "Missing session_id"}, status_code=400)

    state = ensure_session_state(session_id)
    state["agent_snapshot"] = payload

    agent_info = state["agent_info"]
    agent_info["connected"] = True
    agent_info["agent_id"] = payload.get("agent_id")
    agent_info["hostname"] = payload.get("hostname")
    agent_info["iface"] = payload.get("iface")
    agent_info["mode"] = payload.get("mode")
    agent_info["port_mode"] = payload.get("port_mode")
    agent_info["custom_ports"] = safe_normalize_custom_ports(payload.get("custom_ports"))
    agent_info["running"] = True
    agent_info["last_seen"] = time.time()
    agent_info["available_ifaces"] = payload.get(
        "available_ifaces",
        agent_info.get("available_ifaces", [])
    )
    agent_info["available_monitored_ifaces"] = payload.get(
        "available_monitored_ifaces",
        agent_info.get("available_monitored_ifaces", []),
    )
    agent_info["available_unmonitored_ifaces"] = payload.get(
        "available_unmonitored_ifaces",
        agent_info.get("available_unmonitored_ifaces", []),
    )
    agent_info["capabilities"] = payload.get(
        "capabilities",
        agent_info.get("capabilities", [])
    )

    modbus_summary = state.get("modbus_summary") or default_modbus_summary()
    if modbus_summary.get("detected") and modbus_summary.get("server_ip"):
        avg_polling = extract_avg_polling_from_snapshot(payload, modbus_summary.get("server_ip"))
        if avg_polling is not None:
            modbus_summary["avg_polling_s"] = avg_polling

    return {"ok": True}


@app.post("/api/agent/event")
def agent_event_ingest(payload: dict = Body(...)):
    session_id = payload.get("session_id")
    if not session_id:
        return JSONResponse({"ok": False, "error": "Missing session_id"}, status_code=400)

    state = ensure_session_state(session_id)
    ingest_agent_event_payload(state, session_id, payload)

    return {"ok": True}


@app.post("/api/agent/events_batch")
def agent_events_batch_ingest(payload: dict = Body(...)):
    session_id = payload.get("session_id")
    if not session_id:
        return JSONResponse({"ok": False, "error": "Missing session_id"}, status_code=400)

    events = payload.get("events")
    if not isinstance(events, list):
        return JSONResponse({"ok": False, "error": "events must be a list"}, status_code=400)

    state = ensure_session_state(session_id)
    accepted = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        if not event.get("session_id"):
            event["session_id"] = session_id
        ingest_agent_event_payload(state, session_id, event)
        accepted += 1

    return {"ok": True, "accepted": accepted}


@app.post("/api/agent/alert")
def agent_alert_ingest(payload: dict = Body(...)):
    session_id = payload.get("session_id")
    if not session_id:
        return JSONResponse({"ok": False, "error": "Missing session_id"}, status_code=400)

    state = ensure_session_state(session_id)
    agent_info = state["agent_info"]
    agent_info["connected"] = True
    agent_info["last_seen"] = time.time()
    push_alert(state, payload)

    summary = payload.get("summary")
    if summary:
        push_log_for_session(session_id, f"Alert: {summary}")
    else:
        push_log_for_session(
            session_id,
            f"Alert generated: {payload.get('severity', 'INFO')} "
            f"{payload.get('event_type', 'UNKNOWN')}"
        )

    return {"ok": True}


@app.on_event("shutdown")
def stop_process_sim_on_shutdown():
    try:
        process_sim.stop()
    except Exception:
        pass

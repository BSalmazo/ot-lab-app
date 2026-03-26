import io
import json
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


def get_github_releases():
    """
    Fetch agent releases from GitHub, with caching.
    Returns a list of releases with download information.
    """
    global releases_cache
    
    current_time = time.time()
    
    # Return cached data if still valid
    if releases_cache["data"] is not None and (current_time - releases_cache["timestamp"]) < RELEASES_CACHE_TTL:
        return releases_cache["data"]
    
    try:
        # Try to fetch from GitHub API
        response = requests.get(GITHUB_API_URL, timeout=5)
        response.raise_for_status()
        
        releases = response.json()
        
        # Process releases to extract download links
        processed_releases = []
        for release in releases:
            if release.get("prerelease") and release["tag_name"] != "dev-latest":
                continue  # Skip most prerelease builds, but keep dev-latest
            
            assets = {}
            for asset in release.get("assets", []):
                asset_name = asset["name"]
                
                if "windows" in asset_name.lower() or asset_name.endswith(".exe"):
                    assets["windows"] = {
                        "name": asset_name,
                        "url": asset["browser_download_url"],
                        "size": asset["size"],
                    }
                elif "macos" in asset_name.lower() or "mac" in asset_name.lower():
                    assets["macos"] = {
                        "name": asset_name,
                        "url": asset["browser_download_url"],
                        "size": asset["size"],
                    }
                elif "linux" in asset_name.lower():
                    assets["linux"] = {
                        "name": asset_name,
                        "url": asset["browser_download_url"],
                        "size": asset["size"],
                    }
            
            if assets:  # Only include releases that have assets
                processed_releases.append({
                    "tag": release["tag_name"],
                    "name": release["name"],
                    "published_at": release["published_at"],
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
        "running": False,
        "last_seen": None,
        "available_ifaces": [],
    }


def default_agent_config():
    return {
        "iface": "ALL",
        "mode": "MONITORING",
        "updated_at": None,
    }


def default_remote_server():
    return {
        "running": False,
        "host": "127.0.0.1",
        "port": 5020,
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
        "updated_at": None,
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
        "avg_polling_s": None,
        "writes_detected": False,
        "state": "Inactive",
        "last_seen": None,
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
                "modbus_summary": default_modbus_summary(),
                "pending_commands": [],
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


def push_event(state: dict, event: dict):
    with lock:
        state["events"].append(event)


def push_alert(state: dict, alert: dict):
    with lock:
        state["alerts"].append(alert)


def push_log_for_session(session_id: str, message: str):
    print(message)
    state = ensure_session_state(session_id)
    with lock:
        state["logs"].append(message)

MODBUS_WRITE_FUNCTIONS = {5, 6, 15, 16}


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

    if function_code is None:
        return

    try:
        function_code = int(function_code)
    except (TypeError, ValueError):
        return

    summary["detected"] = True
    summary["protocol"] = "Modbus/TCP"
    summary["interface"] = iface
    summary["port"] = port
    summary["client_ip"] = client_ip
    summary["server_ip"] = server_ip
    summary["last_seen"] = time.time()
    summary["state"] = "Active"

    existing_fc = set(summary.get("functions_seen") or [])
    existing_fc.add(function_code)
    summary["functions_seen"] = sorted(existing_fc)

    if function_code in MODBUS_WRITE_FUNCTIONS or event_type in {"WRITE_REQUEST", "WRITE_RESPONSE"}:
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


def build_modbus_summary(state: dict):
    summary = dict(state.get("modbus_summary") or default_modbus_summary())

    if not summary.get("detected"):
        return {"detected": False}

    last_seen = summary.get("last_seen")
    if last_seen is not None and (time.time() - last_seen > 5):
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
        "avg_polling_s": summary.get("avg_polling_s"),
        "writes_detected": bool(summary.get("writes_detected")),
        "state": summary.get("state") or "Inactive",
    }


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
        state["pending_commands"].append(cmd)

    push_log_for_session(session_id, build_command_log_message(command_type, payload))
    return cmd


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
    }


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
    Downloads agent + installation script as a ZIP.
    Platform: "windows", "macos", "linux"
    
    Returns:
    - Agent binary
    - Installation script
    - Documentation (INSTALLATION.md)
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
    agent_path = config["agent_path"]
    script_path = config["script_path"]
    docs_path = BASE_DIR / "scripts" / "INSTALLATION.md"
    
    # Verify all files exist
    if not agent_path.exists():
        return JSONResponse({"error": "Agent file not found"}, status_code=404)
    if not script_path.exists():
        return JSONResponse({"error": "Installation script not found"}, status_code=404)
    
    # Create ZIP with agent + script + docs
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add agent binary
        zf.write(agent_path, arcname=config["agent_name"])
        
        # Add installation script
        zf.write(script_path, arcname=config["script_name"])
        
        # Add documentation
        if docs_path.exists():
            zf.write(docs_path, arcname="INSTALLATION.md")
        
        # Add README with quick start
        quick_start = f"""
🚀 OT Lab Agent - {platform.upper()} Package

This package contains:
1. otlab-agent-{platform}-amd64 (or .exe) - The agent executable
2. {config['script_name']} - Installation script
3. INSTALLATION.md - Full documentation

QUICK START:
"""
        if platform == "windows":
            quick_start += """
1. Extract all files
2. Right-click install-windows.bat
3. Select "Run as Administrator"
4. Double-click otlab-agent-windows-amd64.exe

Done! ✨
"""
        elif platform == "macos":
            quick_start += """
1. Extract all files
2. Open Terminal and navigate to the folder:
   cd ~/Downloads/  (or wherever you extracted)
3. Run the install script:
   bash install-macos.sh
4. Done! ✨

Note: You may see "cannot verify" warning - the script handles this.
"""
        elif platform == "linux":
            quick_start += """
1. Extract all files
2. Open Terminal and navigate to the folder
3. Run the install script:
   bash install-linux.sh
4. Run the agent:
   ./otlab-agent-linux-amd64

Done! ✨
"""
        
        quick_start += """
SUPPORT:
- See INSTALLATION.md for detailed instructions
- Check requirements for your platform (Npcap/libpcap)
- For issues, contact: support@example.com
"""
        
        zf.writestr("README.txt", quick_start)
    
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
    connected = (
        agent_info["last_seen"] is not None and
        (now - agent_info["last_seen"] <= 8)
    )
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
        "session_id": session_id,
    })
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
        "current": agent_info.get("iface"),
        "session_id": session_id,
    })
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.get("/api/downloads/agent/windows")
def download_agent_windows(request: Request):
    session_id, state = get_session_state_from_request(request)
    agent_path = BASE_DIR / "downloads" / "agent" / "windows" / "otlab-agent.exe"

    if not agent_path.exists():
        return JSONResponse({"ok": False, "error": "Agent file not found"}, status_code=404)

    config = build_agent_config(request, session_id, state)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(agent_path, arcname="otlab-agent.exe")
        zf.writestr("agent-config.json", json.dumps(config, indent=2))

    response = Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=otlab-agent-windows.zip"}
    )
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.get("/api/downloads/agent/mac")
def download_agent_mac(request: Request):
    session_id, state = get_session_state_from_request(request)
    agent_path = BASE_DIR / "downloads" / "agent" / "mac" / "otlab-agent-mac"

    if not agent_path.exists():
        return JSONResponse({"ok": False, "error": "Agent file not found"}, status_code=404)

    config = build_agent_config(request, session_id, state)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(agent_path, arcname="otlab-agent-mac")
        zf.writestr("agent-config.json", json.dumps(config, indent=2))

    response = Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=otlab-agent-mac.zip"}
    )
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.get("/api/downloads/agent/linux")
def download_agent_linux(request: Request):
    session_id, state = get_session_state_from_request(request)
    agent_path = BASE_DIR / "downloads" / "agent" / "linux" / "otlab-agent-linux"

    if not agent_path.exists():
        return JSONResponse({"ok": False, "error": "Agent file not found"}, status_code=404)

    config = build_agent_config(request, session_id, state)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(agent_path, arcname="otlab-agent-linux")
        zf.writestr("agent-config.json", json.dumps(config, indent=2))

    response = Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=otlab-agent-linux.zip"}
    )
    set_session_cookie_if_needed(request, response, session_id)
    return response


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
    
    releases = get_github_releases()
    
    if not releases:
        return JSONResponse({
            "ok": False,
            "error": "No releases available",
            "releases": []
        }, status_code=503)
    
    # Find the latest stable release and dev-latest
    latest_stable = None
    dev_latest = None
    
    for release in releases:
        if release["tag"] == "dev-latest":
            dev_latest = release
        elif not release["prerelease"] and latest_stable is None:
            latest_stable = release
    
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

    if mode not in ["LEARNING", "MONITORING"]:
        return JSONResponse({"ok": False, "error": "Invalid mode"}, status_code=400)

    # Detectar mudança de interface
    old_iface = state["agent_config"]["iface"]
    iface_changed = (old_iface != iface)

    state["agent_config"]["iface"] = iface
    state["agent_config"]["mode"] = mode
    state["agent_config"]["updated_at"] = time.time()

    # Se interface mudou, resetar o sumário de detecção
    if iface_changed:
        state["modbus_summary"] = default_modbus_summary()
        push_log_for_session(session_id, f"Detection interface changed from {old_iface} to {iface} - resetting detection")
    else:
        push_log_for_session(session_id, f"Monitor configuration updated (interface={iface}, mode={mode})")

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
    return {"ok": True, "commands": commands}


@app.post("/api/agent/runtime")
def agent_runtime_update(payload: dict = Body(...)):
    session_id = payload.get("session_id")
    if not session_id:
        return JSONResponse({"ok": False, "error": "Missing session_id"}, status_code=400)

    state = ensure_session_state(session_id)

    server_data = payload.get("server") or {}
    client_data = payload.get("client") or {}

    previous_server_running = state["remote_server"]["running"]
    previous_client_running = state["remote_client"]["running"]

    # Only update running status from agent; configuration is managed via /api/agent/server/configure and /api/agent/client/configure
    if server_data:
        state["remote_server"].update({
            "running": bool(server_data.get("running", state["remote_server"]["running"])),
            "updated_at": time.time(),
        })

    if client_data:
        state["remote_client"].update({
            "running": bool(client_data.get("running", state["remote_client"]["running"])),
            "updated_at": time.time(),
        })

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
        state["events"].clear()
        state["alerts"].clear()
        state["logs"].clear()
        state["agent_info"] = default_agent_info()
        state["agent_snapshot"] = default_agent_snapshot()
        state["agent_config"] = default_agent_config()
        state["remote_server"] = default_remote_server()
        state["remote_client"] = default_remote_client()
        state["modbus_summary"] = default_modbus_summary()
        state["pending_commands"].clear()

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
    agent_info["running"] = payload.get("running", False)
    agent_info["last_seen"] = payload.get("timestamp", time.time())
    agent_info["available_ifaces"] = payload.get("available_ifaces", [])

    push_log_for_session(
        session_id,
        f"Agent connected ({payload.get('hostname', '-')}, interface={payload.get('iface', '-')}, mode={payload.get('mode', '-')})"
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
    agent_info["running"] = payload.get("running", False)
    agent_info["last_seen"] = payload.get("timestamp", time.time())
    agent_info["available_ifaces"] = payload.get(
        "available_ifaces",
        agent_info.get("available_ifaces", [])
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
    agent_info["running"] = True
    agent_info["last_seen"] = time.time()
    agent_info["available_ifaces"] = payload.get(
        "available_ifaces",
        agent_info.get("available_ifaces", [])
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
    push_event(state, payload)
    update_modbus_summary_from_event(state, payload)

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

    return {"ok": True}


@app.post("/api/agent/alert")
def agent_alert_ingest(payload: dict = Body(...)):
    session_id = payload.get("session_id")
    if not session_id:
        return JSONResponse({"ok": False, "error": "Missing session_id"}, status_code=400)

    state = ensure_session_state(session_id)
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
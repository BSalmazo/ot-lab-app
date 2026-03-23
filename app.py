import io
import json
import time
import uuid
import zipfile

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

lock = Lock()
agents_by_session = {}


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
    push_log_for_session(session_id, f"[agent-command] {command_type} {payload}")
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

    state["agent_config"]["iface"] = iface
    state["agent_config"]["mode"] = mode
    state["agent_config"]["updated_at"] = time.time()

    push_log_for_session(session_id, f"[agent-config] iface={iface} mode={mode}")

    response = JSONResponse({
        "ok": True,
        "config": state["agent_config"],
    })
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

    if server_data:
        state["remote_server"].update({
            "running": bool(server_data.get("running", state["remote_server"]["running"])),
            "host": server_data.get("host", state["remote_server"]["host"]),
            "port": int(server_data.get("port", state["remote_server"]["port"])),
            "updated_at": time.time(),
        })

    if client_data:
        state["remote_client"].update({
            "running": bool(client_data.get("running", state["remote_client"]["running"])),
            "host": client_data.get("host", state["remote_client"]["host"]),
            "port": int(client_data.get("port", state["remote_client"]["port"])),
            "poll_interval": float(client_data.get("poll_interval", state["remote_client"]["poll_interval"])),
            "poll_start": int(client_data.get("poll_start", state["remote_client"]["poll_start"])),
            "poll_quantity": int(client_data.get("poll_quantity", state["remote_client"]["poll_quantity"])),
            "updated_at": time.time(),
        })

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
        f"[agent-register] id={payload.get('agent_id')} "
        f"host={payload.get('hostname')} iface={payload.get('iface')} mode={payload.get('mode')}"
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

    return {"ok": True}


@app.post("/api/agent/event")
def agent_event_ingest(payload: dict = Body(...)):
    session_id = payload.get("session_id")
    if not session_id:
        return JSONResponse({"ok": False, "error": "Missing session_id"}, status_code=400)

    state = ensure_session_state(session_id)
    push_event(state, payload)

    push_log_for_session(
        session_id,
        f"[agent-event] agent={payload.get('agent_id')} "
        f"{payload.get('type', 'UNKNOWN')} "
        f"{payload.get('src_ip')}:{payload.get('src_port')} -> "
        f"{payload.get('dst_ip')}:{payload.get('dst_port')}"
    )

    return {"ok": True}


@app.post("/api/agent/alert")
def agent_alert_ingest(payload: dict = Body(...)):
    session_id = payload.get("session_id")
    if not session_id:
        return JSONResponse({"ok": False, "error": "Missing session_id"}, status_code=400)

    state = ensure_session_state(session_id)
    push_alert(state, payload)

    push_log_for_session(
        session_id,
        f"[agent-alert] agent={payload.get('agent_id')} "
        f"{payload.get('severity')} {payload.get('event_type')} score={payload.get('score')}"
    )

    return {"ok": True}
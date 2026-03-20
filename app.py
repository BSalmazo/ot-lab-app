import time
import json
import uuid

from collections import deque
from threading import Lock
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from modbus_client import ModbusTCPClient
from modbus_server import ModbusTCPServer
from process_sim import SimpleProcess

app = FastAPI(title="OT Lab App")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # depois restringimos ao teu domínio
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
SESSION_COOKIE = "scada_session_id"

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
app.mount("/downloads", StaticFiles(directory=BASE_DIR / "downloads"), name="downloads")

lock = Lock()


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
            }
        return agents_by_session[session_id]


def get_or_create_session_id(request: Request):
    session_id = request.cookies.get(SESSION_COOKIE)
    if not session_id:
        session_id = f"sess_{uuid.uuid4().hex}"
    return session_id


def get_session_state_from_request(request: Request):
    session_id = get_or_create_session_id(request)
    state = ensure_session_state(session_id)
    return session_id, state


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


def push_log_global(message: str):
    print(message)
    with lock:
        for state in agents_by_session.values():
            state["logs"].append(message)


def push_log_for_session(session_id: str, message: str):
    state = ensure_session_state(session_id)
    with lock:
        state["logs"].append(message)


agents_by_session = {}

process = SimpleProcess()
server = ModbusTCPServer(process=process, on_log=push_log_global)
client = ModbusTCPClient(on_log=push_log_global)


@app.on_event("startup")
def startup():
    process.start()
    push_log_global("Processo simulado iniciado")


@app.on_event("shutdown")
def shutdown():
    try:
        client.stop()
    except Exception:
        pass
    try:
        server.stop()
    except Exception:
        pass
    try:
        process.stop()
    except Exception:
        pass


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    session_id, _state = get_session_state_from_request(request)
    response = templates.TemplateResponse("index.html", {"request": request})
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
        "process": process.snapshot(),

        "server": {
            "running": server.running,
            "host": server.host,
            "port": server.port,
        },

        "client": {
            "running": client.running,
            "host": client.host,
            "port": client.port,
            "poll_interval": client.poll_interval,
            "poll_start": client.poll_start,
            "poll_quantity": client.poll_quantity,
        },

        "monitor": {
            "running": connected and agent_info["running"],
            "iface": agent_info["iface"] or "-",
            "mode": agent_info["mode"] or "-",
            "snapshot": state["agent_snapshot"],
        },

        "agent": agent_info,
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


@app.get("/api/agent/download")
def download_agent(request: Request):
    session_id, state = get_session_state_from_request(request)
    template_path = BASE_DIR / "downloads" / "agent_template.py"

    if not template_path.exists():
        return JSONResponse({"ok": False, "error": "Template not found"}, status_code=500)

    content = template_path.read_text(encoding="utf-8")

    server_url = str(request.base_url).rstrip("/")
    iface = state["agent_config"].get("iface") or "ALL"
    mode = state["agent_config"].get("mode", "MONITORING")

    content = content.replace("__SERVER_URL__", server_url)
    content = content.replace("__SESSION_ID__", session_id)
    content = content.replace("__IFACE__", iface)
    content = content.replace("__MODE__", mode)

    response = Response(
        content,
        media_type="text/x-python",
        headers={
            "Content-Disposition": "attachment; filename=agent.py"
        }
    )

    set_session_cookie_if_needed(request, response, session_id)
    return response

@app.get("/downloads/agent/windows")
def download_agent_windows():
    path = Path("downloads/agent/windows/otlab-agent.exe")
    return FileResponse(
        path=path,
        filename="otlab-agent.exe",
        media_type="application/octet-stream"
    )

@app.get("/downloads/agent/mac")
def download_agent_mac():
    path = Path("downloads/agent/mac/otlab-agent")
    return FileResponse(
        path=path,
        filename="otlab-agent-mac",
        media_type="application/octet-stream"
    )

@app.get("/downloads/agent/linux")
def download_agent_linux():
    path = Path("downloads/agent/linux/otlab-agent")
    return FileResponse(
        path=path,
        filename="otlab-agent-linux",
        media_type="application/octet-stream"
    )


@app.post("/api/server/start")
async def start_server(request: Request):
    data = await request.json()
    host = data.get("host", "127.0.0.1")
    port = int(data.get("port", 15020))
    server.start(host=host, port=port)
    return {"ok": True}


@app.post("/api/server/stop")
def stop_server():
    server.stop()
    return {"ok": True}


@app.post("/api/client/start")
async def start_client(request: Request):
    data = await request.json()
    client.configure(
        host=data.get("host", "127.0.0.1"),
        port=int(data.get("port", 15020)),
        poll_interval=float(data.get("poll_interval", 1.0)),
        poll_start=int(data.get("poll_start", 0)),
        poll_quantity=int(data.get("poll_quantity", 4)),
    )
    client.start()
    return {"ok": True}


@app.post("/api/client/stop")
def stop_client():
    client.stop()
    return {"ok": True}


@app.post("/api/client/read")
async def manual_read(request: Request):
    data = await request.json()
    start = int(data.get("start", 0))
    quantity = int(data.get("quantity", 4))
    try:
        values = client.read_holding_registers(start, quantity)
        return {"ok": True, "values": values}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.post("/api/client/write")
async def manual_write(request: Request):
    data = await request.json()
    register = int(data.get("register", 1))
    value = int(data.get("value", 1))
    try:
        result = client.write_single_register(register, value)
        return {"ok": True, "result": result}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.post("/api/process/set")
async def set_process(request: Request):
    data = await request.json()
    pump_on = data.get("pump_on")
    valve_open = data.get("valve_open")
    level = data.get("level")

    process.set_actuators(
        pump_on=pump_on if pump_on is not None else None,
        valve_open=valve_open if valve_open is not None else None,
    )

    if level is not None:
        process.write_register(0, int(level))

    return {"ok": True, "process": process.snapshot()}


@app.post("/api/process/reset")
def reset_process():
    process.reset()
    return {"ok": True, "process": process.snapshot()}


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

    response = JSONResponse({"ok": True, "session_id": session_id})
    set_session_cookie_if_needed(request, response, session_id)
    return response


@app.post("/api/agent/register")
def agent_register(payload: dict):
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
    }


@app.post("/api/agent/heartbeat")
def agent_heartbeat(payload: dict):
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

    return {"ok": True}


@app.post("/api/agent/snapshot")
def agent_snapshot_ingest(payload: dict):
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
def agent_event_ingest(payload: dict):
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
def agent_alert_ingest(payload: dict):
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
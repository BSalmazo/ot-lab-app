import argparse
import json
import sys
from pathlib import Path

DEFAULT_SERVER_URL = "https://web-production-56599.up.railway.app/"
DEFAULT_SESSION_ID = "dev-local-session"
DEFAULT_MODE = "MONITORING"
DEFAULT_IFACE = "ALL"

CONFIG_DIR = Path.home() / ".ot_lab_agent"
INSTALLED_CONFIG_FILE = CONFIG_DIR / "agent_config.json"


def get_executable_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


LOCAL_BUNDLED_CONFIG = get_executable_dir() / "agent-config.json"


def load_agent_config():
    # Packaged build priority:
    # 1) agent-config.json next to the executable (intended primary source)
    # 2) installed/user config under ~/.ot_lab_agent/agent_config.json
    candidates = [
        LOCAL_BUNDLED_CONFIG,
        INSTALLED_CONFIG_FILE,
    ]

    for path in candidates:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    print(f"[agent] config encontrada em: {path}")
                    print(f"[agent] config lida: {data}")
                    return data
            except Exception as e:
                print(f"[agent] falha ao ler config {path}: {e}")

    print("[agent] nenhuma config encontrada")
    return {}


def build_arg_parser(bundled_config):
    parser = argparse.ArgumentParser(description="OT Lab Agent")
    parser.add_argument("--server", default=bundled_config.get("server_url") or DEFAULT_SERVER_URL)
    parser.add_argument("--session-id", default=bundled_config.get("session_id") or DEFAULT_SESSION_ID)
    parser.add_argument("--iface", default=bundled_config.get("iface") or DEFAULT_IFACE)
    parser.add_argument(
        "--mode",
        default=bundled_config.get("mode") or DEFAULT_MODE,
        choices=["LEARNING", "MONITORING"],
    )
    return parser

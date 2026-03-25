import json
import time
import uuid

from .config import CONFIG_DIR

IDENTITY_FILE = CONFIG_DIR / "identity.json"


def load_or_create_local_identity():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if IDENTITY_FILE.exists():
        try:
            data = json.loads(IDENTITY_FILE.read_text(encoding="utf-8"))
            if data.get("agent_id"):
                return data
        except Exception:
            pass

    identity = {
        "agent_id": str(uuid.uuid4()),
        "created_at": time.time(),
    }
    IDENTITY_FILE.write_text(json.dumps(identity, indent=2), encoding="utf-8")
    return identity

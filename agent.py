import subprocess
import sys

REQUIRED_PACKAGES = ["requests", "scapy"]


def ensure_dependencies():
    for pkg in REQUIRED_PACKAGES:
        try:
            __import__(pkg)
        except ImportError:
            print(f"[agent] installing missing dependency: {pkg}")
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])


ensure_dependencies()

from agent.main import main


if __name__ == "__main__":
    main()

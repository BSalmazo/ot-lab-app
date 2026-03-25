import subprocess
import sys

REQUIRED_PACKAGES = ["requests", "scapy"]


def ensure_dependencies():
    if getattr(sys, "frozen", False):
        return

    for pkg in REQUIRED_PACKAGES:
        try:
            __import__(pkg)
        except ImportError:
            print(f"[agent] installing missing dependency: {pkg}")
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])


def run():
    from agent.main import main
    main()


if __name__ == "__main__":
    ensure_dependencies()
    run()
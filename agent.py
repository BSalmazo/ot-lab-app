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
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        pass

    args = list(sys.argv[1:])
    if "--gui" in args:
        from agent.gui import main as gui_main
        gui_main()
        return

    if "--cli" in args:
        sys.argv = [sys.argv[0], *[arg for arg in args if arg != "--cli"]]

    from agent.main import main
    main()


if __name__ == "__main__":
    ensure_dependencies()
    run()

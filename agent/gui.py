import json
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from queue import Empty, Queue

import tkinter as tk
from tkinter import ttk

from .config import LOCAL_BUNDLED_CONFIG, INSTALLED_CONFIG_FILE


def _load_config():
    for path in (LOCAL_BUNDLED_CONFIG, INSTALLED_CONFIG_FILE):
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data, path
        except Exception:
            pass
    return {}, None


def _runtime_command():
    if getattr(sys, "frozen", False):
        return [sys.executable, "--cli"]
    return [sys.executable, str(Path(__file__).resolve().parent.parent / "agent.py"), "--cli"]


class RuntimeGui:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("OT Lab Local Runtime")
        self.root.geometry("760x520")
        self.root.minsize(620, 420)

        self.process = None
        self.output_queue = Queue()
        self.config, self.config_path = _load_config()

        self.status_var = tk.StringVar(value="Stopped")
        self.session_var = tk.StringVar(value=self.config.get("session_id", "-"))
        self.server_var = tk.StringVar(value=self.config.get("server_url", "-"))

        self._build()
        self._poll_output()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self):
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)

        title = ttk.Label(outer, text="OT Lab Local Runtime", font=("TkDefaultFont", 16, "bold"))
        title.pack(anchor="w")

        subtitle = ttk.Label(
            outer,
            text="Local bridge for packet capture, Modbus services, and process PLC/HMI runtime.",
        )
        subtitle.pack(anchor="w", pady=(2, 12))

        info = ttk.Frame(outer)
        info.pack(fill="x", pady=(0, 10))
        self._info_row(info, "Status", self.status_var, 0)
        self._info_row(info, "Session", self.session_var, 1)
        self._info_row(info, "Web", self.server_var, 2)

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(0, 10))
        ttk.Button(actions, text="Start Runtime", command=self.start_runtime).pack(side="left")
        ttk.Button(actions, text="Stop Runtime", command=self.stop_runtime).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Open Web Interface", command=self.open_web).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Reload Config", command=self.reload_config).pack(side="left", padx=(8, 0))

        caps = ttk.LabelFrame(outer, text="Capabilities")
        caps.pack(fill="x", pady=(0, 10))
        for text in (
            "Capture Agent / IDS monitor",
            "Modbus Server and Client runtime",
            "Process PLC and HMI runtime",
            "Web control-plane connection",
        ):
            ttk.Label(caps, text=f"• {text}").pack(anchor="w", padx=8, pady=1)

        log_frame = ttk.LabelFrame(outer, text="Runtime Log")
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, height=12, wrap="word", state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        scroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scroll.set)

    def _info_row(self, parent, label, var, row):
        ttk.Label(parent, text=f"{label}:").grid(row=row, column=0, sticky="w", pady=2)
        ttk.Label(parent, textvariable=var).grid(row=row, column=1, sticky="w", padx=(8, 0), pady=2)
        parent.columnconfigure(1, weight=1)

    def _append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def reload_config(self):
        self.config, self.config_path = _load_config()
        self.session_var.set(self.config.get("session_id", "-"))
        self.server_var.set(self.config.get("server_url", "-"))
        self._append_log("[runtime-ui] Config reloaded\n")

    def open_web(self):
        url = str(self.config.get("server_url") or "").strip()
        if url:
            webbrowser.open(url)

    def start_runtime(self):
        if self.process and self.process.poll() is None:
            self._append_log("[runtime-ui] Runtime is already running\n")
            return

        cmd = _runtime_command()
        self._append_log(f"[runtime-ui] Starting: {' '.join(cmd)}\n")
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self.status_var.set("Running")
        threading.Thread(target=self._read_process_output, daemon=True).start()

    def stop_runtime(self):
        if not self.process or self.process.poll() is not None:
            self.status_var.set("Stopped")
            return
        self._append_log("[runtime-ui] Stopping runtime\n")
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.status_var.set("Stopped")

    def _read_process_output(self):
        try:
            for line in self.process.stdout:
                self.output_queue.put(line)
        except Exception as exc:
            self.output_queue.put(f"[runtime-ui] Output reader error: {exc}\n")
        finally:
            self.output_queue.put("[runtime-ui] Runtime process exited\n")

    def _poll_output(self):
        try:
            while True:
                self._append_log(self.output_queue.get_nowait())
        except Empty:
            pass

        if self.process and self.process.poll() is not None:
            self.status_var.set("Stopped")
        self.root.after(150, self._poll_output)

    def _on_close(self):
        self.stop_runtime()
        time.sleep(0.1)
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    RuntimeGui().run()

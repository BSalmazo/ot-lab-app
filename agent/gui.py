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


def _runtime_command(with_monitoring=False):
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--cli"]
    else:
        cmd = [sys.executable, str(Path(__file__).resolve().parent.parent / "agent.py"), "--cli"]
    if not with_monitoring:
        cmd.append("--runtime-only")
    return cmd


class RuntimeGui:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("OT Lab Local Runtime")
        self.root.geometry("680x430")
        self.root.minsize(560, 360)

        self.process = None
        self.monitor_enabled = False
        self.verbose = ("--verbose" in sys.argv) or ("-v" in sys.argv)
        self.output_queue = Queue()
        self.config, self.config_path = _load_config()

        self.status_var = tk.StringVar(value="Stopped")
        self.session_var = tk.StringVar(value=self.config.get("session_id", "-"))
        self.server_var = tk.StringVar(value=self.config.get("server_url", "-"))

        self._build()
        self._set_running(False)
        self._poll_output()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self):
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)

        title = ttk.Label(outer, text="OT Lab Local Runtime", font=("TkDefaultFont", 16, "bold"))
        title.pack(anchor="w")

        info = ttk.Frame(outer)
        info.pack(fill="x", pady=(12, 10))
        self.status_value_label = self._info_row(info, "Status", self.status_var, 0)
        self._info_row(info, "Session", self.session_var, 1)
        self._info_row(info, "Web", self.server_var, 2)

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(0, 10))
        self.runtime_switch = ttk.Button(actions, text="Start Runtime", command=self.toggle_runtime)
        self.runtime_switch.pack(side="left")
        self.monitor_switch = ttk.Button(actions, text="Start Monitor", command=self.toggle_monitor)
        self.monitor_switch.pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Open Web Interface", command=self.open_web).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Reload Config", command=self.reload_config).pack(side="left", padx=(8, 0))

        log_frame = ttk.LabelFrame(outer, text="Runtime Log")
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, height=12, wrap="word", state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        scroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scroll.set)

    def _info_row(self, parent, label, var, row):
        ttk.Label(parent, text=f"{label}:").grid(row=row, column=0, sticky="w", pady=2)
        value_label = ttk.Label(parent, textvariable=var)
        value_label.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=2)
        parent.columnconfigure(1, weight=1)
        return value_label

    def _set_running(self, running):
        self.status_var.set("Running" if running else "Stopped")
        if hasattr(self, "runtime_switch"):
            self.runtime_switch.configure(text="Stop Runtime" if running else "Start Runtime")
        if hasattr(self, "monitor_switch"):
            self.monitor_switch.configure(
                text="Stop Monitor" if self.monitor_enabled else "Start Monitor",
                state="normal" if running else "disabled",
            )
        if hasattr(self, "status_value_label"):
            self.status_value_label.configure(foreground="#16a34a" if running else "")

    def _should_display_line(self, line: str) -> bool:
        if self.verbose:
            return True
        text = str(line or "")
        if not text.strip():
            return False
        important_tokens = (
            "[runtime-ui]",
            "control loop started",
            "processing command",
            "process simulation started",
            "process simulation stopped",
            "command result sent",
            "command result failed",
            "failed",
            "error",
            "stopping...",
            "Runtime process exited",
        )
        if any(token in text for token in important_tokens):
            return True
        if "command poll" in text and "pending=0 received=0" in text:
            return False
        noisy_prefixes = (
            "[agent] Verificando NPCAP/libpcap",
            "[agent] ✓ NPCAP/libpcap",
            "[agent] Interfaces encontradas",
            "[agent] config encontrada",
            "[agent] config lida",
            "=== OT LAB AGENT ===",
            "Interfaces disponíveis:",
            "Interface pedida:",
            "Port mode pedido:",
            "Custom ports pedidos:",
            "[agent] config carregada",
            "[agent] using iface:",
            "[agent] session=",
            "[agent] ALL-mode selected",
            "[agent] interface classification",
            "[agent] capture filter:",
            "[agent] attempting to start",
            "[agent] sniffing successfully started",
        )
        return not text.startswith(noisy_prefixes)

    def _append_log(self, text):
        if not self._should_display_line(text):
            return
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

    def toggle_runtime(self):
        if self.process and self.process.poll() is None:
            self.stop_runtime()
            return
        self.start_runtime(with_monitoring=self.monitor_enabled)

    def start_runtime(self, with_monitoring=False):
        if self.process and self.process.poll() is None:
            self._append_log("[runtime-ui] Runtime is already running\n")
            return

        self.monitor_enabled = bool(with_monitoring)
        cmd = _runtime_command(with_monitoring=with_monitoring)
        self._append_log(f"[runtime-ui] Starting: {' '.join(cmd)}\n")
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._set_running(True)
        threading.Thread(target=self._read_process_output, daemon=True).start()

    def toggle_monitor(self):
        if not self.process or self.process.poll() is not None:
            return
        next_mode = not self.monitor_enabled
        self._append_log(
            f"[runtime-ui] Switching monitor {'ON' if next_mode else 'OFF'} (restarting runtime)\n"
        )
        self.stop_runtime()
        self.start_runtime(with_monitoring=next_mode)

    def stop_runtime(self):
        if not self.process or self.process.poll() is not None:
            self._set_running(False)
            return
        self._append_log("[runtime-ui] Stopping runtime\n")
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self._set_running(False)

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
            self._set_running(False)
        self.root.after(150, self._poll_output)

    def _on_close(self):
        self.stop_runtime()
        time.sleep(0.1)
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    RuntimeGui().run()

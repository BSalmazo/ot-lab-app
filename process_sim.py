import threading
import time


class SimpleProcess:
    """
    Processo simples:
    reg 0 -> nivel (0..100)
    reg 1 -> bomba (0/1)
    reg 2 -> valvula (0/1)
    reg 3 -> alarme alto nivel (0/1)
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None

        self.level = 20
        self.pump_on = False
        self.valve_open = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def reset(self):
        with self._lock:
            self.level = 20
            self.pump_on = False
            self.valve_open = False

    def _run(self):
        while not self._stop_event.is_set():
            with self._lock:
                if self.pump_on and not self.valve_open:
                    self.level += 1
                elif self.valve_open and not self.pump_on:
                    self.level -= 1
                elif self.pump_on and self.valve_open:
                    self.level -= 0.2

                self.level = max(0, min(100, self.level))

            time.sleep(0.5)

    def set_actuators(self, pump_on=None, valve_open=None):
        with self._lock:
            if pump_on is not None:
                self.pump_on = bool(pump_on)
            if valve_open is not None:
                self.valve_open = bool(valve_open)

    def get_holding_register(self, address: int) -> int:
        with self._lock:
            if address == 0:
                return int(self.level)
            if address == 1:
                return int(self.pump_on)
            if address == 2:
                return int(self.valve_open)
            if address == 3:
                return 1 if self.level >= 80 else 0
            return 0

    def set_holding_register(self, address: int, value: int):
        with self._lock:
            if address == 1:
                self.pump_on = bool(value)
            elif address == 2:
                self.valve_open = bool(value)
            elif address == 0:
                self.level = max(0, min(100, int(value)))

    def read_registers(self, start_addr: int, quantity: int) -> list[int]:
        return [self.get_holding_register(start_addr + i) for i in range(quantity)]

    def write_register(self, register: int, value: int):
        self.set_holding_register(register, value)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "level": round(self.level, 1),
                "pump_on": self.pump_on,
                "valve_open": self.valve_open,
                "alarm_high": self.level >= 80,
                "registers": {
                    "0": int(self.level),
                    "1": int(self.pump_on),
                    "2": int(self.valve_open),
                    "3": 1 if self.level >= 80 else 0,
                },
            }
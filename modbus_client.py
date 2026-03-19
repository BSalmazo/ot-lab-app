import socket
import struct
import threading
import time


class ModbusTCPClient:
    def __init__(self, host="127.0.0.1", port=15020, unit_id=1, on_log=None):
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.on_log = on_log or (lambda msg: None)

        self.poll_interval = 1.0
        self.poll_start = 0
        self.poll_quantity = 4

        self._tx_id = 1
        self._thread = None
        self._stop_event = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def configure(self, host=None, port=None, poll_interval=None, poll_start=None, poll_quantity=None):
        if host is not None:
            self.host = host
        if port is not None:
            self.port = int(port)
        if poll_interval is not None:
            self.poll_interval = float(poll_interval)
        if poll_start is not None:
            self.poll_start = int(poll_start)
        if poll_quantity is not None:
            self.poll_quantity = int(poll_quantity)

    def start(self):
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        self.on_log(f"Cliente Modbus iniciado para {self.host}:{self.port}")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        self.on_log("Cliente Modbus parado")

    def _poll_loop(self):
        while not self._stop_event.is_set():
            try:
                self.read_holding_registers(self.poll_start, self.poll_quantity)
            except Exception as e:
                self.on_log(f"Erro no polling: {e}")
            time.sleep(self.poll_interval)

    def _next_tx_id(self) -> int:
        self._tx_id = (self._tx_id + 1) % 65535
        if self._tx_id == 0:
            self._tx_id = 1
        return self._tx_id

    def _send_pdu(self, function_code: int, pdu_payload: bytes) -> bytes:
        tx_id = self._next_tx_id()
        pdu = bytes([function_code]) + pdu_payload
        mbap = struct.pack(">HHHB", tx_id, 0, len(pdu) + 1, self.unit_id)
        request = mbap + pdu

        with socket.create_connection((self.host, self.port), timeout=2.0) as sock:
            sock.sendall(request)

            header = self._recv_exact(sock, 7)
            if not header:
                raise RuntimeError("Sem resposta MBAP")

            rx_tx_id, protocol_id, length = struct.unpack(">HHH", header[:6])
            _unit_id = header[6]
            body = self._recv_exact(sock, length - 1)
            if not body:
                raise RuntimeError("Sem resposta PDU")

            if rx_tx_id != tx_id or protocol_id != 0:
                raise RuntimeError("Resposta Modbus inválida")

            return body

    @staticmethod
    def _recv_exact(sock: socket.socket, n: int):
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    def read_holding_registers(self, start_addr: int, quantity: int) -> list[int]:
        body = self._send_pdu(3, struct.pack(">HH", start_addr, quantity))
        function_code = body[0]

        if function_code & 0x80:
            raise RuntimeError(f"Exceção Modbus FC03: code={body[1]}")

        byte_count = body[1]
        raw = body[2:2 + byte_count]
        values = [
            struct.unpack(">H", raw[i:i + 2])[0]
            for i in range(0, len(raw), 2)
        ]
        return values

    def write_single_register(self, register: int, value: int) -> dict:
        body = self._send_pdu(6, struct.pack(">HH", register, value))
        function_code = body[0]

        if function_code & 0x80:
            raise RuntimeError(f"Exceção Modbus FC06: code={body[1]}")

        reg, val = struct.unpack(">HH", body[1:5])
        return {"register": reg, "value": val}
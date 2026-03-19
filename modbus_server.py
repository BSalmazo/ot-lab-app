import socket
import struct
import threading


class ModbusTCPServer:
    """
    Servidor Modbus/TCP mínimo.
    Suporta:
    - FC03 (Read Holding Registers)
    - FC06 (Write Single Register)
    """

    def __init__(self, process, host="127.0.0.1", port=15020, on_log=None):
        self.process = process
        self.host = host
        self.port = port
        self.on_log = on_log or (lambda msg: None)

        self._sock = None
        self._thread = None
        self._stop_event = threading.Event()
        self._client_threads = []

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, host=None, port=None):
        if self.running:
            return

        if host:
            self.host = host
        if port:
            self.port = int(port)

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass

        for t in list(self._client_threads):
            t.join(timeout=0.5)

        if self._thread:
            self._thread.join(timeout=1.0)

    def _serve(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(5)
        self._sock.settimeout(0.5)

        self.on_log(f"Servidor Modbus iniciado em {self.host}:{self.port}")

        try:
            while not self._stop_event.is_set():
                try:
                    conn, addr = self._sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break

                t = threading.Thread(
                    target=self._handle_client, args=(conn, addr), daemon=True
                )
                self._client_threads.append(t)
                t.start()
        finally:
            self.on_log("Servidor Modbus parado")

    def _handle_client(self, conn: socket.socket, addr):
        conn.settimeout(1.0)
        try:
            while not self._stop_event.is_set():
                header = self._recv_exact(conn, 7)
                if not header:
                    break

                transaction_id, protocol_id, length = struct.unpack(">HHH", header[:6])
                unit_id = header[6]

                pdu = self._recv_exact(conn, length - 1)
                if not pdu:
                    break

                function_code = pdu[0]

                if protocol_id != 0:
                    continue

                if function_code == 3 and len(pdu) == 5:
                    start_addr, quantity = struct.unpack(">HH", pdu[1:5])
                    values = self.process.read_registers(start_addr, quantity)

                    byte_count = quantity * 2
                    response_pdu = bytes([3, byte_count]) + b"".join(
                        struct.pack(">H", v) for v in values
                    )
                    response_mbap = struct.pack(
                        ">HHHB", transaction_id, 0, len(response_pdu) + 1, unit_id
                    )
                    conn.sendall(response_mbap + response_pdu)

                elif function_code == 6 and len(pdu) == 5:
                    register, value = struct.unpack(">HH", pdu[1:5])
                    self.process.write_register(register, value)

                    response_pdu = bytes([6]) + struct.pack(">HH", register, value)
                    response_mbap = struct.pack(
                        ">HHHB", transaction_id, 0, len(response_pdu) + 1, unit_id
                    )
                    conn.sendall(response_mbap + response_pdu)

                else:
                    exception_code = 1
                    response_pdu = bytes([function_code | 0x80, exception_code])
                    response_mbap = struct.pack(
                        ">HHHB", transaction_id, 0, len(response_pdu) + 1, unit_id
                    )
                    conn.sendall(response_mbap + response_pdu)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _recv_exact(conn: socket.socket, n: int):
        data = b""
        while len(data) < n:
            try:
                chunk = conn.recv(n - len(data))
            except socket.timeout:
                return None
            if not chunk:
                return None
            data += chunk
        return data
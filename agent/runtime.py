import socket
import threading
import time


def recv_exact(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("socket closed while receiving")
        data += chunk
    return data


class SimpleModbusServer:
    def __init__(self, host="127.0.0.1", port=5020, register_count=200):
        self.host = host
        self.port = int(port)
        self.register_count = register_count

        self._thread = None
        self._stop_event = threading.Event()
        self._server_socket = None
        self._lock = threading.Lock()

        self.holding_registers = [0] * register_count
        self._seed_demo_data()

    def _seed_demo_data(self):
        if self.register_count >= 8:
            self.holding_registers[0] = 10
            self.holding_registers[1] = 20
            self.holding_registers[2] = 30
            self.holding_registers[3] = 40
            self.holding_registers[4] = 100
            self.holding_registers[5] = 200
            self.holding_registers[6] = 300
            self.holding_registers[7] = 400

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return True

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._serve_loop, daemon=True)
        self._thread.start()
        time.sleep(0.2)
        return self.running

    def stop(self):
        self._stop_event.set()

        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass

        if self._thread:
            self._thread.join(timeout=2)

        self._thread = None
        self._server_socket = None

    def _serve_loop(self):
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((self.host, self.port))
            srv.listen(5)
            srv.settimeout(1.0)
            self._server_socket = srv

            print(f"[modbus-server] listening on {self.host}:{self.port}")

            while not self._stop_event.is_set():
                try:
                    conn, addr = srv.accept()
                    conn.settimeout(2.0)
                    threading.Thread(
                        target=self._handle_client,
                        args=(conn, addr),
                        daemon=True,
                    ).start()
                except socket.timeout:
                    continue
                except OSError:
                    break
                except Exception as e:
                    print(f"[modbus-server] accept error: {e}")

        except Exception as e:
            print(f"[modbus-server] failed to start: {e}")
        finally:
            if self._server_socket:
                try:
                    self._server_socket.close()
                except Exception:
                    pass
            self._server_socket = None
            print("[modbus-server] stopped")

    def _handle_client(self, conn: socket.socket, addr):
        try:
            while not self._stop_event.is_set():
                header = conn.recv(7)
                if not header:
                    break
                if len(header) < 7:
                    break

                tx_id = int.from_bytes(header[0:2], "big")
                proto_id = int.from_bytes(header[2:4], "big")
                length = int.from_bytes(header[4:6], "big")
                unit_id = header[6]

                if proto_id != 0 or length < 2:
                    break

                pdu = recv_exact(conn, length - 1)
                function_code = pdu[0]
                data = pdu[1:]

                response_pdu = self._process_request(function_code, data)
                response_mbap = (
                    tx_id.to_bytes(2, "big")
                    + (0).to_bytes(2, "big")
                    + (len(response_pdu) + 1).to_bytes(2, "big")
                    + bytes([unit_id])
                )
                conn.sendall(response_mbap + response_pdu)

        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _exception_response(self, function_code: int, exc_code: int) -> bytes:
        return bytes([function_code | 0x80, exc_code])

    def _process_request(self, function_code: int, data: bytes) -> bytes:
        if function_code == 3:
            if len(data) != 4:
                return self._exception_response(function_code, 3)

            start_addr = int.from_bytes(data[0:2], "big")
            quantity = int.from_bytes(data[2:4], "big")

            if quantity <= 0 or quantity > 125:
                return self._exception_response(function_code, 3)

            end_addr = start_addr + quantity
            if start_addr < 0 or end_addr > len(self.holding_registers):
                return self._exception_response(function_code, 2)

            with self._lock:
                regs = self.holding_registers[start_addr:end_addr]

            payload = b"".join(v.to_bytes(2, "big") for v in regs)
            return bytes([function_code, len(payload)]) + payload

        if function_code == 6:
            if len(data) != 4:
                return self._exception_response(function_code, 3)

            register = int.from_bytes(data[0:2], "big")
            value = int.from_bytes(data[2:4], "big")

            if register < 0 or register >= len(self.holding_registers):
                return self._exception_response(function_code, 2)

            with self._lock:
                self.holding_registers[register] = value

            return bytes([function_code]) + data

        return self._exception_response(function_code, 1)


class SimpleModbusClient:
    def __init__(self, host="127.0.0.1", port=5020, poll_interval=1.0, poll_start=0, poll_quantity=4):
        self.host = host
        self.port = int(port)
        self.poll_interval = float(poll_interval)
        self.poll_start = int(poll_start)
        self.poll_quantity = int(poll_quantity)

        self._thread = None
        self._stop_event = threading.Event()
        self._tx_id = 1
        self.last_values = []
        self.last_error = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return True

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        time.sleep(2.0)
        return self.running

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None

    def update_config(self, host, port, poll_interval, poll_start, poll_quantity):
        self.host = host
        self.port = int(port)
        self.poll_interval = float(poll_interval)
        self.poll_start = int(poll_start)
        self.poll_quantity = int(poll_quantity)

    def _next_tx_id(self):
        tx = self._tx_id
        self._tx_id = (self._tx_id + 1) % 65536
        if self._tx_id == 0:
            self._tx_id = 1
        return tx

    def _poll_loop(self):
        print(
            f"[modbus-client] polling {self.host}:{self.port} "
            f"start={self.poll_start} qty={self.poll_quantity} every {self.poll_interval}s"
        )

        while not self._stop_event.is_set():
            try:
                values = self._read_holding_registers(
                    host=self.host,
                    port=self.port,
                    start_addr=self.poll_start,
                    quantity=self.poll_quantity,
                )
                self.last_values = values
                self.last_error = None
            except Exception as e:
                self.last_error = str(e)
                print(f"[modbus-client] poll error: {e}")

            sleep_step = 0.1
            waited = 0.0
            while waited < self.poll_interval and not self._stop_event.is_set():
                time.sleep(sleep_step)
                waited += sleep_step

        print("[modbus-client] stopped")

    def _read_holding_registers(self, host, port, start_addr, quantity):
        tx_id = self._next_tx_id()
        unit_id = 1
        function_code = 3

        pdu = bytes([function_code]) + start_addr.to_bytes(2, "big") + quantity.to_bytes(2, "big")
        mbap = (
            tx_id.to_bytes(2, "big")
            + (0).to_bytes(2, "big")
            + (len(pdu) + 1).to_bytes(2, "big")
            + bytes([unit_id])
        )

        with socket.create_connection((host, port), timeout=2.0) as sock:
            sock.sendall(mbap + pdu)

            resp_header = recv_exact(sock, 7)
            resp_tx_id = int.from_bytes(resp_header[0:2], "big")
            resp_proto_id = int.from_bytes(resp_header[2:4], "big")
            resp_len = int.from_bytes(resp_header[4:6], "big")
            _resp_unit_id = resp_header[6]

            if resp_tx_id != tx_id or resp_proto_id != 0:
                raise ValueError("invalid Modbus response header")

            resp_pdu = recv_exact(sock, resp_len - 1)
            fc = resp_pdu[0]

            if fc & 0x80:
                exc_code = resp_pdu[1] if len(resp_pdu) > 1 else -1
                raise ValueError(f"modbus exception code={exc_code}")

            if fc != function_code:
                raise ValueError(f"unexpected function code: {fc}")

            byte_count = resp_pdu[1]
            data = resp_pdu[2:2 + byte_count]

            values = []
            for i in range(0, len(data), 2):
                if i + 1 < len(data):
                    values.append(int.from_bytes(data[i:i + 2], "big"))

            return values

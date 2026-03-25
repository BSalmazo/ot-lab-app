MODBUS_KNOWN_FUNCTION_CODES = {1, 2, 3, 4, 5, 6, 15, 16, 22, 23, 24, 43}


def looks_like_modbus_tcp(payload: bytes) -> bool:
    if len(payload) < 8:
        return False

    protocol_id = int.from_bytes(payload[2:4], "big")
    length_field = int.from_bytes(payload[4:6], "big")

    if protocol_id != 0:
        return False

    if length_field < 2:
        return False

    expected_total_length = 6 + length_field
    if len(payload) < expected_total_length:
        return False

    function_code = payload[7]

    if function_code not in MODBUS_KNOWN_FUNCTION_CODES and (function_code & 0x80) == 0:
        return False

    return True


def extract_modbus_frames(payload: bytes):
    frames = []
    offset = 0
    total_len = len(payload)

    while offset + 8 <= total_len:
        chunk = payload[offset:]

        protocol_id = int.from_bytes(chunk[2:4], "big")
        length_field = int.from_bytes(chunk[4:6], "big")

        if protocol_id != 0:
            break

        if length_field < 2:
            break

        frame_len = 6 + length_field

        if len(chunk) < frame_len:
            break

        frame = chunk[:frame_len]

        function_code = frame[7]
        if function_code not in MODBUS_KNOWN_FUNCTION_CODES and (function_code & 0x80) == 0:
            break

        frames.append(frame)
        offset += frame_len

    return frames


def tx_key(tx_id, src_ip, src_port, dst_ip, dst_port):
    return (tx_id, src_ip, src_port, dst_ip, dst_port)


def reverse_tx_key(tx_id, src_ip, src_port, dst_ip, dst_port):
    return (tx_id, dst_ip, dst_port, src_ip, src_port)


def decode_modbus(payload: bytes, src_ip: str, src_port: int, dst_ip: str, dst_port: int, timestamp: float, context):
    if len(payload) < 8:
        return None

    tx_id = int.from_bytes(payload[0:2], "big")
    protocol_id = int.from_bytes(payload[2:4], "big")
    length = int.from_bytes(payload[4:6], "big")

    total_len = 6 + length
    if len(payload) < total_len:
        return None

    payload = payload[:total_len]

    unit_id = payload[6]
    function_code = payload[7]

    if protocol_id != 0:
        return None

    reverse_key_value = reverse_tx_key(tx_id, src_ip, src_port, dst_ip, dst_port)
    is_response = reverse_key_value in context.state["pending_transactions"]

    decoded = {
        "session_id": context.session_id,
        "agent_id": context.agent_id,
        "timestamp": timestamp,
        "src_ip": src_ip,
        "src_port": src_port,
        "dst_ip": dst_ip,
        "dst_port": dst_port,
        "client": f"{src_ip}:{src_port}" if not is_response else f"{dst_ip}:{dst_port}",
        "server": f"{dst_ip}:{dst_port}" if not is_response else f"{src_ip}:{src_port}",
        "direction": "response" if is_response else "request",
        "transaction_id": tx_id,
        "function_code": function_code,
        "unit_id": unit_id,
        "length": length,
        "protocol": "MODBUS/TCP",
    }

    if function_code & 0x80:
        decoded.update({
            "type": "EXCEPTION_RESPONSE" if is_response else "UNKNOWN_REQUEST",
            "exception_code": payload[8] if len(payload) > 8 else None,
        })
        return decoded

    if function_code in (3, 4):
        if not is_response and len(payload) >= 12:
            start_addr = int.from_bytes(payload[8:10], "big")
            quantity = int.from_bytes(payload[10:12], "big")
            decoded.update({
                "type": "READ_REQUEST",
                "start_addr": start_addr,
                "quantity": quantity,
            })
            return decoded

        if is_response and len(payload) >= 9:
            byte_count = payload[8]
            data_bytes = payload[9:9 + byte_count]
            regs = []
            for i in range(0, len(data_bytes), 2):
                if i + 1 < len(data_bytes):
                    regs.append(int.from_bytes(data_bytes[i:i + 2], "big"))
            decoded.update({
                "type": "READ_RESPONSE",
                "register_values": regs,
            })
            return decoded

    if function_code == 6 and len(payload) >= 12:
        register = int.from_bytes(payload[8:10], "big")
        value = int.from_bytes(payload[10:12], "big")
        decoded.update({
            "register": register,
            "value": value,
            "type": "WRITE_RESPONSE" if is_response else "WRITE_REQUEST",
        })
        return decoded

    if function_code == 16:
        if not is_response and len(payload) >= 13:
            start_addr = int.from_bytes(payload[8:10], "big")
            quantity = int.from_bytes(payload[10:12], "big")
            byte_count = payload[12]
            values = []
            values_data = payload[13:13 + byte_count]

            for i in range(0, len(values_data), 2):
                if i + 1 < len(values_data):
                    values.append(int.from_bytes(values_data[i:i + 2], "big"))

            decoded.update({
                "type": "WRITE_REQUEST",
                "register": start_addr,
                "start_addr": start_addr,
                "quantity": quantity,
                "values": values,
                "value": values[0] if values else None,
            })
            return decoded

        if is_response and len(payload) >= 12:
            start_addr = int.from_bytes(payload[8:10], "big")
            quantity = int.from_bytes(payload[10:12], "big")
            decoded.update({
                "type": "WRITE_RESPONSE",
                "register": start_addr,
                "start_addr": start_addr,
                "quantity": quantity,
                "value": None,
            })
            return decoded

    if function_code in (5, 15):
        decoded.update({
            "type": "WRITE_RESPONSE" if is_response else "WRITE_REQUEST",
        })
        return decoded

    decoded.update({
        "type": "GENERIC_RESPONSE" if is_response else "GENERIC_REQUEST",
    })
    return decoded

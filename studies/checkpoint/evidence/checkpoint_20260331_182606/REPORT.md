# Checkpoint Report

**Decision:** CHECKPOINT APROVADO

## Test Cases

- **CT1 Detecao basica**: PASS - Transition observed in 0.879s (threshold: <=3.0s).
- **CT2 Interface correta**: PASS - Detected interfaces=['en0', 'lo0']; require >=2 non-ALL interfaces.
- **CT3 Escritas**: PASS - writes_detected true or WRITE_* event observed.
- **CT4 Excecoes**: PASS - EXCEPTION_RESPONSE event/alert observed.
- **CT5 Estado conexao**: PASS - Active->Inactive observed in 2.201s (expected around 2s).
- **CT6 Acoes remotas**: PASS - Action final_status=done duration=0.335s.
- **CT7 Estabilidade**: PASS - events_http_ok_ratio=1.0000, duration_actual_s=2158.912.

## Metadata

- Base URL: `http://127.0.0.1:8000`
- Session ID: `sess_92c7874bfe77418daa4e3e200ac629f4`
- Duration (actual): `2158.912` s
- Poll interval: `0.5` s
- Events OK ratio: `1.0`

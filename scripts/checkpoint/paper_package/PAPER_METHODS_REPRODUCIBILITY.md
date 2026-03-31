# Methods and Reproducibility

## Experimental Design

The validation protocol used seven acceptance criteria (CT1..CT7):

- CT1: basic detection latency
- CT2: interface attribution correctness
- CT3: write detection evidence
- CT4: exception detection evidence
- CT5: Active/Inactive state transition
- CT6: remote action lifecycle correctness
- CT7: stability under sustained load

## Environment

- Host OS: macOS (details in `system_info.txt`)
- Python: see `python_version.txt`
- Backend endpoint: `http://127.0.0.1:8000`
- Session isolation: cookie-based `scada_session_id`
- Approved run: `checkpoint_20260331_182606`

## Instrumentation

Data collection script:

- `scripts/checkpoint/collect_checkpoint_data.py`

Collected channels (JSONL):

- status snapshots (`status.jsonl`)
- event snapshots (`events.jsonl`)
- command snapshots (`commands.jsonl`)

Derived artifacts:

- `metadata.json`
- `checkpoint_result.json`
- `REPORT.md`
- `derived_metrics.json`

## Procedure (Approved Campaign)

1. Backend and agent bootstrapped with shared session.
2. Collection started at 0.5s polling interval.
3. Local and remote traffic generation executed:
   - loopback traffic (`lo0`)
   - same-network mobile traffic (`en0`)
4. Remote actions and exception-triggering actions executed.
5. Stability interval maintained under sustained request pressure.
6. Evaluator executed and all CTs passed.

## Determinism and Traceability

- Commit hash was captured (`git_commit.txt`).
- Checkpoint package was archived (`checkpoint_20260331_182606.tar.gz`).
- SHA-256 checksums were computed for key evidence files.

## Reproducibility Command Skeleton

```bash
# 1) Start backend
uvicorn app:app --host 0.0.0.0 --port 8000

# 2) Start agent
python agent.py --server http://127.0.0.1:8000 --session-id <SESSION_ID> --iface ALL --mode MONITORING

# 3) Start collector
python scripts/checkpoint/collect_checkpoint_data.py \
  --base-url http://127.0.0.1:8000 \
  --session-id <SESSION_ID> \
  --interval 0.5 \
  --duration 3600 \
  --output-dir artifacts/checkpoint_runs/<RUN_ID>

# 4) Evaluate
python scripts/checkpoint/evaluate_checkpoint.py \
  --input-dir artifacts/checkpoint_runs/<RUN_ID> \
  --output artifacts/checkpoint_runs/<RUN_ID>/REPORT.md
```

## Notes for paper writing

- Treat JSONL streams as primary telemetry evidence.
- Treat `REPORT.md` and `checkpoint_result.json` as final acceptance evidence.
- Use `derived_metrics.json` for compact quantitative tables in the manuscript.

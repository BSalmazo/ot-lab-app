# Evidence Traceability Map (Claim -> Source)

## C1. Final campaign achieved full acceptance (CT1..CT7 all PASS)
- Source: `studies/checkpoint/evidence/checkpoint_20260331_182606/REPORT.md`
- Source: `studies/checkpoint/evidence/checkpoint_20260331_182606/checkpoint_result.json`

## C2. Multi-interface attribution is validated (`lo0` and `en0`)
- Source: `studies/checkpoint/evidence/checkpoint_20260331_182606/checkpoint_result.json` (CT2 detail)
- Source: `studies/checkpoint/evidence/checkpoint_20260331_182606/derived_metrics.json` (`interface_counts`)

## C3. Detection latency is below 3 seconds (CT1)
- Source: `checkpoint_result.json` -> CT1 detail (`0.879s`)

## C4. Active/Inactive transition around 2 seconds is validated (CT5)
- Source: `checkpoint_result.json` -> CT5 detail (`2.201s`)

## C5. Write traffic is correctly detected (CT3)
- Source: `checkpoint_result.json` -> CT3 PASS
- Source: `derived_metrics.json` -> `event_type_counts.WRITE_REQUEST`

## C6. Modbus exception handling is detected (CT4)
- Source: `checkpoint_result.json` -> CT4 PASS
- Source: `derived_metrics.json` -> `event_type_counts.EXCEPTION_RESPONSE`

## C7. Remote action lifecycle is valid (CT6)
- Source: `checkpoint_result.json` -> CT6 PASS
- Source: `action_lifecycle.json` (timing and final status timeline)

## C8. Long-run stability under sustained load (CT7)
- Source: `checkpoint_result.json` -> CT7 PASS
- Source: `metadata.json` (`events_http_ok_ratio=1.0`, duration)

## C9. Architecture description and implementation boundaries
- Source: `studies/checkpoint/CHECKPOINT_SUPER_DETALHADO.md`
- Source: repository files listed in that dossier.

## C10. Reproducibility and integrity guarantees
- Source: `PAPER_DATA_MANIFEST.json` (checksums + file sizes)
- Source: `git_commit.txt`, `python_version.txt`, `system_info.txt`

# State of the repository: LiscereSecurity/OT-Lab (the Liscere engine)

Phase 1 read-only audit. Date: 2026-09-11. Nothing in the repository, on the Pi or on the PLC was modified. All commands ran against a fresh clone in a scratch directory. "Branch" below means the bench-validated branch `feature/opcua-subscription` at 3cf6c43 unless stated otherwise; "v2-dev" is the default branch at b4b6d76.

Bottom line: the engine core is small, pure and well tested by its own script-based tests, but the repository cannot yet support the measurement goal. There is no replay path, no persisted verdict record with evidence fields, no ground truth, and the bench-validated code sits on three stacked branches that have never run in CI. Details and file references follow.

## 1. Git state

**There is no `main` branch.** The default branch is `v2-dev`. Every "unmerged relative to main" answer below is relative to `v2-dev`.

Branches (remote, all authored by BSalmazo / Bruno Salmazo):

| Branch | Head | Date | Subject |
|---|---|---|---|
| v2-dev (default) | b4b6d76 | 2026-07-16 | Product-oriented discovery UI, with -v for the developer view |
| feature/multi-protocol | 6cbb243 | 2026-07-20 | Survive a tshark death: respawn under backoff |
| feature/opcua-subscription | 3cf6c43 | 2026-07-29 | Observer: reconstruct the state signal during report-by-exception holds |
| fix/ui-flicker | f4ed069 | 2026-07-30 | UI: redraw on a fixed timer to the alternate screen |

The three feature branches are a single linear stack on top of v2-dev (merge base b4b6d76 for all three). Nothing on v2-dev is missing from them.

Unmerged commits relative to v2-dev, oldest first:

- b3b8a98 Probe the wire and run every claimed extractor from one selector loop
- 86ccef8 Label each silo by its own extractor; split unreadable from unevaluable
- cbd0b63 Report capture loss instead of returning silently in continuous evaluate
- 86f00b4 Bound tshark memory with -M, and add --only to restrict which layers run
- 6cbb243 Survive a tshark death: respawn under backoff (head of feature/multi-protocol)
- 77b05b7 OPC UA: split subscription publishes by ClientHandle, read the variant generically
- 1eadb91 OPC UA: read WriteRequest (673) as per-node commands
- eb6e2b4 OPC UA: attribute live state samples per ClientHandle, not per publish message
- 3cf6c43 Observer: reconstruct the state signal during report-by-exception holds (head of feature/opcua-subscription; adds HoldResampler)
- f4ed069 UI: redraw on a fixed timer (head of fix/ui-flicker only)

Diff of feature/opcua-subscription against v2-dev: 15 files, +2699 / -356 lines. The engine package changes are confined to `otlab_core/extractors/*` and the new `otlab_core/wire.py`; `otlab_core/engine/*` is untouched by the branch.

Tags: one, `legacy-engine-v1` (annotated, points at bbb2be6). It preserves the retired FastAPI/Docker/FUXA engine and the old scenario pack.

Pull requests: 17, all merged, the last on 2026-07-16. No PR exists for any of the three open branches. Issues: none.

CI: the last workflow run is from 2026-07-16 on v2-dev. Because `ci.yml` triggers only on push to `main`, `master`, `v2-dev` and on pull requests, **none of the ten unmerged commits has ever run in CI**.

Branch protection: the API returns 404 for v2-dev, consistent with no protection configured (the query ran with the `Balmazo` GitHub account; the pushing account `BSalmazo` is logged in but inactive in `gh`).

Local working clone (OneDrive, `Claude/LiscereSecurity/OT-Lab`): HEAD is on `fix/ui-flicker`; the last reflog entry is a rebase finishing at 2026-07-30 15:16. It carries 17 local branches, 13 of them merged feature branches that no longer exist on the remote. No stash reflog exists. **Working-tree status could not be read**: several files under `.git` (including `packed-refs`) are OneDrive cloud placeholders and every read times out, so `git status`, `git stash list` and `git diff` all fail. Whether that clone has uncommitted changes is unknown. This is a risk in itself (see section 12).

`.gitignore` (29 lines): covers caches, virtual environments, `*.db`, build output and OS files. Half of its entries point at paths that no longer exist (`studies/evidence/...`, `scripts/v2_seed/backups/`, `downloads/`, `*.spec.bak`). It does **not** ignore what the observer actually writes today: the grammar JSON from `--grammar`, the profile JSON from `--profile`, any redirected `.jsonl` stream, or `.pcap` / `.pcapng` captures. A bench run started from the repository root can therefore leave data in the working tree.

Secrets and credentials: none found in tracked files on any branch and none found in history (searched every ref for `.env`, `.pem`, `.key`, `id_rsa`, `tailscale`, `password`, `token`, `secret`). The only match is `.env.example` on the legacy tag, containing Docker-lab addresses (10.20.0.x, 10.30.0.x). All IP addresses in the current tree are synthetic test values (`tests/*.py`, 10.0.0.x and 192.168.1.x). The bench address range (192.168.18.x) does not appear anywhere in the repository. Commit author emails include two machine-local addresses (`@Brunos-MacBook-Pro.local`, `@Brunos-MacBook-Air.local`); cosmetic, not a leak.

## 2. Build and run

Language: Python 3, version undeclared. CI pins 3.11. The tree compiles under 3.12 and all tests pass under 3.14. The lowest workable version is 3.9 because `scripts/liscere_observe.py:1238` uses `argparse.BooleanOptionalAction`. The Python version on the Pi is unknown (not recorded in the repository).

Dependency management: `requirements.txt` only (5 lines). No `pyproject.toml`, no lockfile, no package metadata, no `setup.py`. The engine is not installable; every script inserts the repository root into `sys.path` (`scripts/liscere_observe.py:56`, `scripts/liscere_probe.py:31`, tests likewise). The one third-party dependency is `rich`, needed only by `scripts/liscere_ui.py`. The observer and probe run on the standard library plus the system `tshark` binary, which is invoked by bare name from `PATH` (`scripts/liscere_observe.py:288` and `:350`).

Clean install (fresh venv, Python 3.14.6, this machine):

```
pip install -r requirements.txt   -> rich 15.0.0 (+ markdown-it-py, mdurl, Pygments)
python scripts/liscere_observe.py --help   -> works, no third-party import
python scripts/liscere_probe.py --help     -> works
python scripts/liscere_ui.py --help        -> ModuleNotFoundError: rich (in a venv without requirements installed)
```

Nothing breaks. A fresh clone runs as long as `tshark` is on `PATH`. Capture needs root or capture capabilities on the interface (not documented in the repository).

Entry points (no console scripts):

- `scripts/liscere_observe.py`: the observer. Required `--iface`; `--probe` (15 s), `--observe` (60 s), `--learn`, `--grammar`, `--profile`, `--only`, `--reset-after` (100000), `--respawn-retries` (36), `--respawn-backoff-cap` (30 s), `--emit-json/--no-emit-json`. Exit codes: 0 ok, 1 nothing to do or multi-silo file flow, 2 nothing on the wire or no evaluable silo, 3 every capture permanently lost, 130 Ctrl-C.
- `scripts/liscere_probe.py`: protocol probe, `--iface`, `--duration`.
- `scripts/liscere_ui.py`: terminal UI reading the observer's JSON Lines on stdin.

Environment variables: none are read anywhere (`os.environ` and `getenv` do not appear in `otlab_core` or `scripts`).

How it is started on the Pi: not in the repository. The only recorded invocation is the docstring in `scripts/liscere_ui.py:12`:

```
sudo -E venv/bin/python scripts/liscere_observe.py --iface eth0 --observe 60 --learn 120 2>/dev/null | venv/bin/python scripts/liscere_ui.py
```

On a laptop: the observer docstring (`scripts/liscere_observe.py:38`) shows `--iface en6`. No install script, service file or README section exists for either.

## 3. Tests and quality

Test framework: **none**. Every file in `tests/` is a standalone script with its own `check(name, cond)` helper that prints `PASS`/`FAIL` and returns a non-zero exit code on any failure. They are run with `python tests/test_x.py`.

Inventory and results (this machine, Python 3.14):

| Tree | Test files | check() assertions | Result |
|---|---|---|---|
| v2-dev | 10 | 178 | all pass |
| feature/opcua-subscription | 15 | 263 | all pass |
| fix/ui-flicker | 16 | 263 + render-loop test | all pass |

Files on the branch: test_autocalibrate, test_discover, test_hold_resampler, test_modbus_coalesce_endpoint, test_modbus_flows, test_modbus_parity, test_multi_protocol, test_observer_pipeline, test_opaque_endpoints, test_opcua_live_attribution, test_opcua_subscription, test_respawn, test_s7_pending, test_silo_demux, test_write_reclassify.

pytest: not usable as-is. It collects only the 9 functions that happen to be named `test_*`; 8 pass and one errors (`tests/test_autocalibrate.py:119` declares a `tmp_profile` fixture that does not exist; the script's own `main()` passes the path explicitly). The other 254 assertions are invisible to pytest.

Coverage, measured by running every test script under `coverage` and combining (branch): **71% of statements** overall. Engine core is well covered (`phase_tracker.py` 99%, `autocalibrate.py` 99%, `discover.py` 100%, `grammar.py` 100%, `evaluator.py` 87%, `opcua.py` 88%, `modbus.py` 87%, `liscere_observe.py` 89%). Weak spots: `s7.py` 65%, `extractors/__init__.py` 50%, `adapter.py` 37% (the adapter is not used by the observer; see section 4), `liscere_probe.py` 0%, `liscere_ui.py` 0%.

Linters: none. Type checking: none (the code carries type hints and dataclasses throughout, so `mypy`/`pyright` would have something to work with). Pre-commit: none. Formatter: none.

CI (`.github/workflows/ci.yml`, 32 lines): `python -m compileall` plus **4 of the 15 test files** (autocalibrate, discover, observer_pipeline, modbus_parity). Every OPC UA, S7, silo, respawn, multi-protocol and hold-resampler test is outside CI. A regression in `opcua.py` would pass CI today.

## 4. Architecture map

Package `otlab_core` (about 2,000 lines on the branch) plus `scripts/` (about 2,100 lines).

Protocol-neutral core (`otlab_core/engine/`, untouched by the branch):

- `config.py` (103): `PhaseConfig` (7 phase-inference parameters, defaults are the LTR-2026-03 Modbus values), `OpcUaConfig` (port 4840, optional `state_signal_node`), `ModbusConfig` (`state_signal_register`, `coalesce_window_s`), `ObserverConfig`. `PhaseConfig.from_json` loads a profile.
- `contract.py` (78): `NormalizedEvent`, the protocol-neutral event. Its `to_wire_dict` reproduces JSON for a backend that no longer exists (retired with the legacy engine).
- `engine/phase_tracker.py` (120): **PhaseTracker**. Least-squares slope over a sliding window, hysteresis via `reversal_n`, flat-run counting via `stable_n`; phases UNKNOWN / RISING / FALLING / STABLE with a confidence and a `transitioning` flag. Consumes floats only.
- `engine/autocalibrate.py` (243): derives a `PhaseConfig` from the observed state series (half-cycle period in samples, rate, window-scale noise floor). Writes a profile JSON.
- `engine/discover.py` (159): behavioural flow classifier; picks the STATE signal among flows (STATE / COMMAND / CONSTANT_METADATA / AMBIGUOUS).
- `engine/grammar.py` (66): `GrammarLearner`, counts writes per target per phase, exports fractions. This is the learned "operational grammar".
- `engine/evaluator.py` (77): `evaluate()` returns a `Verdict(verdict, rule, learned_fraction, reason)`: COHERENT (fraction >= 0.30), UNUSUAL (0 < fraction < 0.30, or target never seen), INCOHERENT (fraction 0), UNCERTAIN (phase unsettled or confidence < 0.6). Rules OBS-R005..R009.
- `engine/adapter.py` (71): `LearnedEngineAdapter`. **Not used by the observer** (the observer feeds the tracker directly, see `scripts/liscere_observe.py:21`). Dead weight kept from Phase 1C.
- `wire.py` (74, branch only): `frame.protocols` chain parsing and the low-port server heuristic, shared by probe and observer.

Protocol-specific (`otlab_core/extractors/`):

- `base.py` (85): `ProtocolExtractor` interface: `tshark_fields`, `capture_filter`, `occurrence`, `parse_line`, `security_mode`, `extract_state_samples(evt, state_key)`, optional `opaque_endpoint`. Extractors also duck-type `group_flows`, `variable_key`, `write_datatype`, `coalesce_writes`, `resolve_state_key`; the observer probes for these with `getattr`.
- `__init__.py` (59): `EXTRACTOR_CLASSES`, `get_extractor(name)`, `extractor_for_layer(layer)`. Registering a protocol means adding a class here.
- `opcua.py` (763): OPC UA binary over tshark fields. **ClientHandle attribution** lives here: `_parse_sub_items` (:425) splits a PublishResponse into per-handle items, `_zip_values` (:453) reads each item's value from its variant-typed column, `resolve_state_key` (:533) maps `opcua:sub:<n>` to a handle, `extract_state_samples` (:554) feeds only the discovered handle when `state_key` is given. Writes are split per node in `_parse_write_items` (:503). Flow keys: `opcua:sub:<handle>`, `opcua:write:ns=<n>;i=<id>`, `opcua:read`.
- `modbus.py` (443): polled reads, write coalescing, flow keys `modbus:hr:<n>`. Capture filter is bare `"tcp"`.
- `s7.py` (642): stateful Job/Ack_Data pairing keyed by (conversation, pduref); flow keys `s7:db<n>:<byte>`.

Observer and UI (`scripts/`):

- `liscere_observe.py` (1328): the pipeline. `Emitter` (:92) writes JSON Lines to stdout; `_tshark_cmd`/`_spawn` (:286); `probe_layers` (:357); `_Supervisor` respawn policy (:473); `multiplex` single selector loop over N tshark (:568); `discover_and_calibrate` (:684); **HoldResampler** (:732, branch only); `_feed_or_judge` (:786) is where a sample advances the tracker or a write is learned or judged; `Silo` (:875) one (protocol, server endpoint) with its own tracker, grammar and resampler; `_calibrate_silo` (:920) includes the fail-loud `resolve_state_key` check (:960); `_Run` (:1029) drives observe -> learn -> evaluate as deadlines in the one loop, `tick()` (:1094) drives the resampler; `main` (:1207).
- `liscere_probe.py` (192) and `liscere_ui.py` (582): presentation and reconnaissance, no engine logic.

Data flow, packet to verdict:

1. `tshark -i <iface> -f <capture_filter> -T fields -e ...` per claimed protocol, one process each, stdout read non-blocking in `multiplex`.
2. Each TSV line goes to its extractor's `parse_line` -> `NormalizedEvent` (or `None`, then `opaque_endpoint` may report it).
3. Modbus events pass through `coalesce_writes`; others pass straight through.
4. During OBSERVE, events are buffered per extractor. At the deadline, `build_silos` partitions by `evt.server`, `group_flows` builds flows, `classify_flows` picks the STATE flow, `calibrate_phase_config` derives the `PhaseConfig`, a `PhaseTracker` and `HoldResampler` are created per silo.
5. During LEARN and EVALUATE, `_feed_or_judge` calls `extract_state_samples(evt, state_key)`; samples go to `tracker.update`. A WRITE_REQUEST goes to `GrammarLearner.observe` (learn) or `evaluate()` (evaluate). `tick()` injects flat repeats during holds.
6. The verdict is logged to stderr and emitted as a JSON `verdict` event (see section 7). Grammars are frozen during evaluate.

Policy layer: **there is no declared-policy layer in the current engine.** Only the learned grammar exists. The declared policy (rules OBS-R000..R005, maintenance windows, allowed ranges) lived in the retired `app.py` and is only reachable on the `legacy-engine-v1` tag. The evaluator docstring still refers to a rule-id collision with `app.py`, which no longer exists.

Verdict emitter: `Emitter.verdict` at `scripts/liscere_observe.py:279` plus the stderr line at `:825`.

## 5. Bench coupling

Bench-named things (Level, Temp, ValveA, ValveB, Heater, Agitator, Outlet, the Łódź addresses): **none in code.** The engine discovers the state signal and keys flows by handle or node id. The PLC node `ns=4;i=23` appears only in docstrings (`opcua.py:12`, `:390`, `:491`, `config.py:64`) and in tests. Held values 50.0 and 80.0 appear only in `tests/test_hold_resampler.py` as synthetic hold levels. No cycle timing is hard-coded; timings are derived at run time.

Constants that do shape bench behaviour, and where they live:

| Constant | Value | Location | Config or code |
|---|---|---|---|
| Phase defaults (window, slopes, reversal_n, stable_n, conf) | 8, ±0.25, 3, 6, 1.0, 0.9 | `otlab_core/config.py:36` | code defaults, overridable by profile; the observer never loads a profile (always recalibrates) |
| Calibration ratios (window = P/30, reversal_n = P/120, stable_n = P/20, slope = max(3·noise, 0.3·rate)) | fixed | `autocalibrate.py:179-191` | code |
| Calibration detector thresholds `_MOVE_EPS`, `_SMOOTH_SPAN`, `_SIGN_DEADBAND`, `_FIT_SPAN` | 0.001, 5, 0.01, 10 | `autocalibrate.py:50-53` | code |
| Discovery thresholds `STATE_MIN_UNIQUE`, `STATE_STEP_FRAC`, `STATE_MIN_REVERSALS`, `_CONST_MAX_UNIQUE` | 30, 0.1, 1, 5 | `discover.py:31-36` | code (marked DEBT D5) |
| Grammar `LEARN_MIN_CONFIDENCE`, `BELONGS_MIN_FRACTION` | 0.6, 0.1 | `grammar.py:20-23` | code |
| Evaluator `COHERENT_MIN`, `PHASE_MIN_CONFIDENCE` | 0.30, 0.6 | `evaluator.py:22-26` | code |
| HoldResampler margin and ceiling | 1.75 × cadence; window + 2·stable_n | `liscere_observe.py:758-761` | code |
| Coalesce window bounds | 0.5 s, 5.0 s, 1/20 half-cycle | `liscere_observe.py:644-646`, `config.py:113` | code |
| OPC UA port | 4840 | `config.py:63` | config (never set from the CLI) |
| Modbus ports set | {502, 5020, 15020} | `modbus.py:22` | code (unused for capture, filter is `tcp`) |
| S7 port | 102 | `s7.py:60` | code |
| tshark `-M` reset, respawn retries, backoff cap | 100000, 36, 30 s | CLI defaults `liscere_observe.py:1227-1237` | CLI |
| Probe and observe windows | 15 s, 60 s | CLI defaults | CLI |
| S7 pending map bound and TTL | 1024, 30 s | `s7.py:99-100` | code |
| Reference profile | window 20, slope ±0.05, reversal 5, stable 30 | `profiles/opcua_tank_10hz.json` | file, "REFERENCE ONLY", not loaded by anything |

**The Temperature exclusion is not in the repository.** No code path excludes a handle or node, no document mentions it, and no test encodes it. Where it is applied (presumably the OPC UA client's subscription set on the HMI or a manual step) is unknown from the repository. Note the mechanism that would make it matter: `classify_flows` picks the STATE flow with the most distinct values, so a second continuously varying signal competes with Level for the role of state signal.

## 6. Replay

**No.** The observer only captures live: `-i iface` is hard-coded in both tshark command builders (`liscere_observe.py:288`, `:350`); there is no `-r`, no TSV reader and no JSON Lines reader.

What exists that helps: the pipeline below capture is pure and takes event lists. `run_learn` and `run_evaluate` (`:849`, `:857`) take an iterable of events; `discover_and_calibrate` takes events; `group_flows`, `classify_flows` and `calibrate_phase_config` take plain data. The tests already drive the whole path from hand-built TSV rows (`tests/test_hold_resampler.py:162` builds `parse_line` input directly).

What blocks a deterministic replay today:

1. Wall clock. `_Run.tick`, `_Run.dispatch`, `HoldResampler.real/catch_up` and `Emitter.emit` all use `time.time()` (nine call sites in `liscere_observe.py`). Observe and learn windows are wall-clock deadlines. The resampler cadence check in the live path uses wall time, not frame time (`liscere_observe.py:1066`), while the test uses frame time.
2. Fallback timestamps. All three extractors substitute `time.time()` when `frame.time_epoch` is empty (`opcua.py:287`, `modbus.py:169`, `s7.py:381`).
3. Output. Every emitted event is stamped with wall-clock `ts`.

Given a fixed input and a clock driven by frame timestamps, the core is deterministic: no randomness, ordered dicts, integer counting. Estimate to add replay: a `tshark -r file.pcapng` source or a recorded-TSV/JSONL source, plus a clock abstraction so `_Run` and the resampler advance on frame time, plus `ts` taken from the frame. Roughly 1 to 2 working days including tests, no engine change.

## 7. Verdict record

Format: JSON Lines on stdout (`--emit-json`, default on), plus a human line on stderr. Nothing is written to a file by the observer; persistence is whatever the operator redirects.

Fields of the `verdict` event (`liscere_observe.py:279`): `type`, `target`, `phase`, `result` (COHERENT / UNUSUAL / INCOHERENT / UNCERTAIN), `rule`, `silo` (server endpoint, added by the bound emitter), `ts` (wall clock, 3 dp).

Missing from the JSON record although computed: the written **value** (`evt.value`), the **confidence**, the **learned fraction**, the **reason** text, the **frame timestamp** of the write, the **client** endpoint, any **run identifier**. The stderr line (`:825`) has confidence and reason but not the value. The `variable_value` event emitted just before carries the value, so a consumer can reconstruct it by ordering, but not from the record itself.

FP and FN: **cannot be computed without manual work.** There is no expected-verdict field, no scenario label, no ground truth anywhere in the current tree. The legacy tag has a labelled scenario pack (`studies/liscere/scenarios/*.yaml` with `expected_decision`) and `scripts/run_liscere_scenarios.py`, but they target the retired engine through an HTTP API and Modbus writes; they are a format precedent, not reusable code. The Research repository holds decision logs and pcaps for LTR-2026-03 (Modbus, OpenPLC) but no OPC UA bench capture and no labels. No OPC UA bench pcap is in any repository (earlier session notes place them on the Pi under `~/shared`; not verified in this session).

## 8. Learning and persistence

Learning: `GrammarLearner.observe(target, phase, confidence, transitioning)` counts a write only when the phase is known, not transitioning, and confidence >= 0.6. `export()` turns counts into per-phase fractions and a `learned_coherent_phases` list (fraction >= 0.1). Learning is on only during the LEARN window; grammars are frozen during EVALUATE. The phase parameters are derived once from the OBSERVE window by `autocalibrate`.

Storage and reload:

- Grammar: written only with `--learn N --grammar path`, and **only when exactly one silo is evaluable** (`_require_single`, `:1188`); the run then stops. Reloaded with `--grammar path` without `--learn`, again single silo. In the default continuous run the grammar lives in memory and is lost at exit. Multi-silo runs never persist anything.
- Phase profile: written with `--profile` for a single extractor only. **Never reloaded**: `PhaseConfig.from_json` exists but no code path in the observer calls it. Every run recalibrates.
- Calibration result, discovered state key, silo list: not persisted.

Stabilisation metric: **none.** The grammar document carries `total_writes_observed` per target and `writes_skipped_low_confidence`, but no cycle count, no time span, no per-cycle snapshot, and no convergence test. Nothing in the repository can say "the model was stable after N cycles". The observe and learn windows are fixed seconds, not cycle counts.

## 9. Documentation

`README.md` is 9 lines: names the entry point and the core package, says the legacy engine is on a tag, and ends with "Full documentation pending". It is accurate but empty. No `docs/` folder, no diagrams, no ADRs, no CHANGELOG, no CONTRIBUTING, no SECURITY.md. `LICENSE` is MIT, "Copyright (c) 2026 Liscere".

The real documentation is the module docstrings, which are long and mostly precise. They also carry stale statements a newcomer will trust: `otlab_core/__init__.py` and `engine/__init__.py` say the engine "is NOT wired into the live passive path" and that "the declarative rules in app.py remain the default"; `contract.py` documents wire parity with `scripts/tshark_runtime.py`; `evaluator.py` documents a rule-id collision with `app.py`; `liscere_observe.py:26` still lists D1 as an open debt although the branch resolves it. None of `app.py`, `tshark_runtime.py` or the backend exist on any current branch.

The Liscere workspace repository has decision records (PDR-001..003); PDR-003 "deploy architecture" could not be read in this session (OneDrive placeholder, and the GitHub copy is not reachable with the active account).

Missing on day one: how the bench is wired and which interface is the mirror; how to run on the Pi and with what privileges; what the JSON stream events mean and their order; the test convention (scripts, not pytest); what the three open branches are and which one the bench runs; the known Temperature workaround; where captures and grammars are kept.

## 10. Deployment

Not in the repository. There is no service file, install script, image, deploy script or runbook on any branch or in history (searched all refs for systemd, scp, rsync, journalctl, logrotate, chrony, ntp, raspberry).

What the code implies: run as root (`sudo -E venv/bin/python`, `liscere_ui.py:13`) from a venv inside the clone; `tshark` resolved from `PATH`; one tshark process per protocol with `-M 100000` because an unbounded run reached about 158 MiB and "the Pi thrashes and dies" (`liscere_observe.py:290-296`); a supervisor respawns a dying tshark with backoff (a "weekend soak" saw one exit hours in, `:1294`); exit code 3 when all captures are lost, intended for "restart supervision" (`:1315`), which suggests an external supervisor was planned but is not recorded.

Logging: stderr only, no file, no rotation. JSON stream: stdout only. Time synchronisation on the Pi: unknown. Code path to the Pi (scp, git pull, image): unknown. Pi 4 upgrade: nothing in the code is Pi-Zero-specific (pure Python plus tshark), so a re-clone would work, but since the current install procedure is undocumented the upgrade would be a rediscovery.

## 11. Related repositories

- `LiscereSecurity/Research`: not vendored, not imported. Lineage coupling only: `otlab_core/engine/{phase_tracker,grammar,evaluator}.py` are documented ports of `LTR-2026-03/evaluator/liscere_learn.py` and `liscere_evaluator_step3.py`. Research holds Modbus pcaps and decision logs for LTR-2026-03 and S7 captures for LTR-2026-04; nothing there is consumed by this repository.
- `LiscereSecurity/LiscereSecurity.github.io`: no reference either way.
- `BSalmazo/Liscere` (workspace): holds PDRs and plans that describe an earlier baseline (`research/papers/implementation-plan-v0.1.md` targets the retired scenario runner). Not referenced by code.
- `legacy-engine-v1` tag inside this repository: the retired Docker lab, FastAPI backend and scenario pack.

## 12. Risks and debt, ranked

1. **Ten commits on three stacked branches have never run in CI, and v2-dev is eight weeks behind the bench.** Any measurement starts from code that the repository has not verified.
2. **No replay.** Every FP/FN number would need the physical bench, so nothing is repeatable and nothing can be re-run after a fix.
3. **The verdict record is not a record.** No value, confidence, reason, frame time or run id, and nothing is written to disk by default. Every published number would be reconstructed by hand.
4. **No ground truth.** No scenario labels, no expected verdicts, no labelled OPC UA capture in any repository.
5. **The Temperature exclusion lives outside the repository.** A bench rebuild or a Pi 4 upgrade can silently lose it and the state-signal discovery will change.
6. **Wall-clock coupling** in `_Run`, `HoldResampler` and `Emitter` makes any replay non-deterministic until a clock abstraction exists.
7. **Test harness is bespoke and CI runs 4 of 15 files.** Regressions in `opcua.py`, `s7.py`, silos, respawn and the resampler pass CI today; pytest errors on one file.
8. **Persistence gaps.** Grammar only saved in a single-silo stop-after-learn mode; profile never reloaded; no stabilisation metric, so the model-cost measurement has nothing to read.
9. **Not packaged.** `sys.path` hacks, no `pyproject`, undeclared Python version, `tshark` by bare name, no lockfile; the Pi environment cannot be reproduced from the repository.
10. **The OneDrive working clone is unreliable.** Git reads time out on placeholder files, its working-tree state is unreadable, and it carries 13 stale branches; working there risks a half-synced `.git`.

Secondary: `.gitignore` does not cover run outputs; `adapter.py` is dead code; stale docstrings describe a retired architecture; no branch protection; no Dependabot; the S7 extractor is at 65% coverage.

## Summary

1. The engine core (`otlab_core/engine`) is small, pure, deterministic and well covered; the bench-validated behaviour lives on `feature/opcua-subscription` (3cf6c43), nine commits ahead of the default branch `v2-dev`, with no `main` branch anywhere.
2. All 263 script assertions pass on the branch, but the tests are not pytest, CI runs four files, and the open branches have never been through CI.
3. No replay, no persisted verdict with evidence fields, no labels and no stabilisation metric: the measurement goal cannot be met with the repository as it stands.
4. Bench specifics are not hard-coded, but the Temperature exclusion and the entire Pi deployment are outside the repository and undocumented.
5. The working clone on OneDrive cannot be read reliably; the audit was done on a fresh clone, and the local tree's uncommitted state is unknown.

# Modernisation plan: LiscereSecurity/OT-Lab

Phase 2 proposal. Date: 2026-09-11. Nothing here is implemented. Each item needs written approval before Phase 3 starts on it. Items follow the priority you set. Bucket letters (A hygiene, B research enablement, C architecture) are kept for reference.

Bottom line: about 114 hours of work across eleven items. Items 1 and 2 need nothing else and should go first. Items 5, 6 and 7 are the measurement core and together take about 34 hours; items 4 and 8 depend on them. Nothing proposed puts the observer in the control path, adds active probing or enforcement, or rewrites the engine.

Design constraints applied throughout:

- GitHub is the only source of truth. No step assumes a laptop clone. Everything an operator needs is a link on the repository page, a release, or a command on the Pi.
- Releases are the unit that reaches the Pi. The Pi never runs from a branch.
- Engine behaviour is unchanged by every item except where stated (items 8 and 9, both opt-in).
- The bench-validated behaviour is the acceptance test: phase sequence RISING, STABLE, RISING, STABLE, FALLING; held values exactly 50.0 and 80.0; no flicker; ValveB=40 during heating INCOHERENT and during add-B COHERENT.

Effort figures are working hours for implementation, tests and PR description, by one person. Verification of each item is stated so the PR description can carry it.

## Dependency order

```
1 captures rescue -------------------------+
2 merge stack + CI + main + protection ----+-> 3 packaging -> 4a Pi install/update/unit
                                           |                    |
5 replay + clock --------------------------+                    v
6 verdict record --------------------------+-> 7 labels + metrics -> 4b Pi "check" command
                                           |
8 persistence + stabilisation -------------+   (needs 5, 6)
9 tag exclusion config --------------------+   (needs 3 for the config file)
10 resume-after-pause docs/board ----------+   (needs 2; grows with each item)
11 remaining hygiene ----------------------+   (needs 2, 3)
```

Items 1, 2, 5 and 6 can start in parallel. Item 4 is split: 4a (install, unit, update) after 3; 4b (check) after 7.

---

## 1. Rescue the bench captures from the Pi

**What.** Copy every capture under `~/shared` on the Pi to a versioned, backed-up location, with a manifest (file name, sha256, size, capture date, bench state, protocol, security mode, tags subscribed, cycles covered, notes), then stop treating the Pi as storage.

**Where, and why.** Three candidates were weighed:

| Option | Storage limit | Bandwidth | Versioned | One command from the Pi | Cost |
|---|---|---|---|---|---|
| Git LFS in OT-Lab (public) | 1 GB free, then paid packs | 1 GB/month free, consumed by every clone and CI run | yes | needs git-lfs on the Pi | quota risk; captures exposed publicly |
| Release assets on a data repository | 2 GB per file, no repository quota | no quota | immutable per release | yes, `gh release upload` from the Pi | none |
| Separate data repository with LFS | as LFS | as LFS | yes | needs git-lfs on the Pi | quota risk |

Recommendation: **a private repository `LiscereSecurity/Bench-Data`, captures stored as release assets, one release per bench session, plus a committed `MANIFEST.json`.** Reasons: captures are immutable so LFS versioning buys nothing; release assets have no bandwidth quota, so CI and the Pi can fetch them freely; a private repository keeps lab addresses and PLC endpoints out of the public OT-Lab; the upload is one `gh release upload` command run on the Pi itself, which matters because the Pi is not reachable from the laptop. OT-Lab keeps a copy of the manifest under `experiments/captures/MANIFEST.json` so every number can be traced to a capture hash from the engine repository alone.

In addition, **one trimmed reference capture, about one full process cycle, small enough to commit as a plain file (target under 5 MB), goes into OT-Lab under `tests/data/`** so CI and the Pi `check` command need no network to run the regression. Trimming with `editcap` keeps it a faithful subset.

**Why it matters for measurement.** Every FP/FN number, every cost figure and every regression test is computed over these files. Today they exist in one copy on an SD card.

**Effort.** 6 h (create repository, manifest schema, upload procedure documented as a runbook, trimmed reference cycle, hashes verified on both sides). Excludes your bench time to identify what each file is.

**Risk.** Low. Only risk is naming the captures wrongly in the manifest; mitigated by recording what is known and marking the rest unknown.

**Engine behaviour change.** No.

**Open point for you.** The Pi needs `gh` (or a personal token for `curl`) to upload to a private repository. If you prefer no credentials on the Pi, the fallback is a one-off `scp` to a laptop and upload from there, accepting a temporary local copy.

---

## 2. Bring the stack in

**What.**

1. Extend `ci.yml` to run every file in `tests/` (a shell loop, no framework change) on push to any branch and on every PR, with Python 3.11 and 3.13 in a matrix.
2. Open PRs in order, each after the previous merges: `feature/multi-protocol` into `v2-dev`; `feature/opcua-subscription` into `v2-dev`; `fix/ui-flicker` into `v2-dev`. Each PR is a fast-forward of the existing commits, no rebase, no squash, so the bench-validated SHAs (including eb6e2b4 and 3cf6c43) survive verbatim in history.
3. Rename `v2-dev` to `main` through the GitHub branch settings (GitHub redirects old links and re-targets open PRs). Update `ci.yml` branch list.
4. Branch protection on `main`: require a PR, require the CI check to pass, no force pushes, no deletion, linear history not required. Do not require an approving review: with one maintainer a required review blocks every merge. Enable "allow administrators to bypass" off, so the rule applies to you too.
5. `CODEOWNERS` with `* @BSalmazo` so every PR auto-requests you.

**Merge plan detail.** What the stack changes on top of v2-dev, and what could regress:

| Layer | Changes | Regression surface | Verification before and after |
|---|---|---|---|
| multi-protocol (5 commits) | observer rewritten from 782 to about 1100 lines: probe, selector multiplex over N tshark, silos per extractor, `--only`, `-M`, capture-loss reporting, respawn supervisor; new `otlab_core/wire.py` | Modbus and S7 paths through the new loop; opaque-endpoint reporting; exit codes | the 10 v2-dev test files must still pass unchanged; the 2 new test files (multi_protocol, respawn) pass |
| opcua-subscription (4 commits) | `extract_state_samples(evt, state_key)` signature on the interface and all three extractors; OPC UA per-handle split, per-node writes, `resolve_state_key`; `HoldResampler` and `_Run.tick` | any caller of the old one-argument signature; polled extractors must ignore `state_key`; live phase feed during holds | `test_opcua_subscription`, `test_opcua_live_attribution`, `test_hold_resampler` (the synthetic bench repro: sequence, 50.0 and 80.0, no flicker); all earlier files unchanged |
| ui-flicker (1 commit) | UI redraw loop only | none in the engine | `test_ui_render_loop` |

Honest limit: until items 5 and 7 exist, "verify against the recorded bench behaviour" means the synthetic repro in `tests/test_hold_resampler.py`, not a recorded capture. After item 5 the same three PR merges can be re-verified by replaying the reference cycle; I propose doing that as a documented check in the first release notes.

**Why.** Ten commits with the validated behaviour have never run in CI. Everything else builds on them.

**Effort.** 5 h.

**Risk.** Low. The three branches are already linear on v2-dev, so the merges are fast-forwards with no conflicts. The rename is reversible.

**Engine behaviour change.** No relative to the bench (it is the bench code). Yes relative to v2-dev, by design.

---

## 3. Packaging

**What.**

- `pyproject.toml`: project `liscere`, version from a single source (`liscere/__init__.py`), `requires-python = ">=3.11"`, dependencies `[]`, optional group `ui = ["rich"]`, dev group with ruff, mypy, pytest.
- Package layout with minimal churn: keep `otlab_core` as the engine package (no renames inside it, so no test edits), add a new package `liscere/` holding the three scripts as modules (`liscere/observe.py`, `probe.py`, `ui.py`). Leave three-line shims at `scripts/liscere_*.py` that import and call `main()`, so the ten test files that import from `scripts/` keep working and every documented command line still runs. Remove the `sys.path` hacks from the modules (the shims keep theirs).
- Console entry points: `liscere-observe`, `liscere-probe`, `liscere-ui`, plus `liscere-observe --version`.
- Lockfile and single install command: `uv` with `uv.lock`; `uv sync` installs everything, `uv run liscere-observe ...` runs it. `uv` ships a static binary for arm64 Linux, so the Pi uses the same tool. If you would rather not adopt uv, the fallback is `pip-tools` with `requirements.lock`; same effect, two commands instead of one.
- `ruff` for lint with a small initial rule set (pyflakes, pycodestyle errors, isort) and a line length of 110 to match the existing code. Formatting the whole tree with `ruff format` is a separate, optional PR because it touches every line and makes `git blame` noisy; I recommend not doing it now.
- `mypy` in permissive mode on `otlab_core` and `liscere`, errors ratcheted to zero from the current count in a follow-up, never blocking the first PR.
- `pre-commit` with ruff, end-of-file and trailing-whitespace fixers, and one custom hook that fails on an em dash in `.py` or `.md` files, matching the writing rule.
- `pytest` wrapper without rewriting tests: `tests/test_scripts.py` parametrised over `tests/test_*.py`, running each as a subprocess and asserting exit code 0; plus a two-line `conftest.py` fixture `tmp_profile` so the one currently broken collected test passes. CI switches to `pytest`. The existing scripts stay runnable by hand.
- `Makefile` with the five commands: `make install`, `make test`, `make lint`, `make run IFACE=eth0`, `make release VERSION=x.y.z`.

**Why.** The Pi and CI cannot reproduce the environment today; entry points are what the systemd unit (item 4) calls.

**Effort.** 12 h.

**Risk.** Medium. Moving scripts into a package is the one change that touches imports; mitigated by the shims and by running all 16 test files before and after. Python version on the Pi is unknown; `>=3.11` is an assumption (Raspberry Pi OS Bookworm ships 3.11). If the Pi runs Bullseye (3.9), the floor drops to 3.9, which the code already satisfies.

**Engine behaviour change.** No.

---

## 4. Pi workflow

Delivered in two steps because the check command depends on items 5 and 7.

### 4a. Install, unit, update (after item 3)

**What.**

- `deploy/pi/install.sh`: apt installs `tshark` and `chrony`, grants capture rights without root (`setcap cap_net_raw,cap_net_admin+eip` on `dumpcap` and the `wireshark` group, which is the Debian-supported way), creates `/opt/liscere` with a venv or uv environment, creates `/var/lib/liscere/runs` and `/var/log/liscere`, installs the unit, timer and logrotate files below, and prints the version.
- `deploy/pi/liscere-observe.service`: runs `liscere-observe --iface eth0 --observe <N> --learn <M> --runs-dir /var/lib/liscere/runs` as the `liscere` user, `Restart=on-failure`, `RestartSec=10`, exit code 3 (all captures lost) treated as failure so systemd restarts it. Stderr goes to journald. Arguments live in `/etc/liscere/observe.env`, not in the unit.
- `liscere-update [TAG]`: downloads the wheel and `sbom` for the given or latest GitHub release with `curl` against the public releases API, verifies the sha256 from the release notes, installs it into `/opt/liscere`, restarts the service, prints old and new version. One command, no git on the Pi.
- Log rotation: `logrotate` rule for `/var/lib/liscere/runs/*/events.jsonl` (size based, keep 10) and journald's own limits for stderr.
- Time sync: `chrony` configured with the lab NTP source if one exists, else the PLC is not an NTP server so the Pi keeps free-running time; the runbook states which applies. Frame timestamps from tshark are what the verdict record uses (item 6), so intra-run ordering is unaffected either way; only cross-device correlation needs a synced clock.

**Effort.** 10 h. **Risk.** Medium: needs one on-bench session to validate the install on the real Pi; cannot be tested here. **Engine behaviour change.** No.

### 4b. Check command (after item 7)

**What.** `liscere-check`: replays the reference capture shipped in the package (item 1) through the installed engine (item 5), evaluates the labelled scenario (item 7), compares to `expected.json` (phase sequence, held values 50.0 and 80.0, no flicker, the ValveB verdicts), and writes `/var/lib/liscere/checks/check-<version>-<timestamp>.json` with pass or fail per assertion. `liscere-update` runs it automatically after install and refuses to leave a failing version in place (rolls back to the previous wheel).

**Effort.** 6 h. **Risk.** Low once 5 and 7 exist. **Engine behaviour change.** No.

---

## 5. Replay mode with a clock abstraction

**What.**

- `Clock` interface in `liscere/clock.py`: `now()`, `sleep_until()`. `LiveClock` wraps `time.time()`. `FrameClock` advances to the timestamp of every frame it is handed and never goes backwards.
- All nine `time.time()` call sites in the observer take the clock: `_Run` deadlines, `_Run.dispatch` and `tick`, `HoldResampler.real/catch_up`, `Emitter` `ts`. Extractors' fallback `time.time()` on an empty `frame.time_epoch` is replaced by "drop the frame and count it", because a frame with no timestamp cannot be replayed deterministically.
- Sources: `--pcap FILE` runs `tshark -r FILE` with the same field list per extractor (the probe also reads the file); `--tsv FILE` reads a recorded tshark field stream directly, which is the exact input the extractors saw live. The selector multiplex gains a file mode that reads sequentially and merges by frame time across protocols.
- `--record DIR` on live runs saves the raw TSV per protocol next to the run record, so every live run is replayable later.
- Determinism test in CI: replay the reference cycle twice, compare the event streams byte for byte after removing the run id.

**Why.** Without it every measurement needs the bench and none can be repeated after a change.

**Effort.** 16 h.

**Risk.** Medium. The resampler currently uses wall time in the live path and frame time in its test; unifying on the clock could shift when flats are injected by up to one select interval (0.25 s). The hold-resampler test and, once available, the reference-cycle replay will show any difference.

**Engine behaviour change.** No in the engine package. In the observer, timing decisions move from wall time to the clock; live runs use `LiveClock` and should behave identically within the select interval. This must be stated in the PR.

---

## 6. Complete verdict record

**What.**

- Every run gets a run id (`<utc-date>T<time>Z-<8 hex>`) and a directory `runs/<run_id>/` (default `./runs`, `/var/lib/liscere/runs` on the Pi) containing `run.json` (version, git tag, arguments, source, clock type, start and end, per-silo calibration result and discovered state key, per-silo grammar at end of learn), `events.jsonl` (the full stream, identical to stdout) and `verdicts.jsonl` (one record per verdict).
- Verdict record fields: `run_id`, `silo`, `client`, `frame_ts`, `target`, `value`, `datatype`, `phase`, `confidence`, `transitioning`, `result`, `rule`, `learned_fraction`, `reason`, `seq` (verdict sequence number).
- Written by default; `--no-run-dir` disables. The stdout stream is unchanged for the UI, so no UI change.
- `variable_value` and `phase` events gain `frame_ts` so the record stands on its own.

**Why.** FP/FN needs value, time and confidence per verdict, and a run id so a number traces to a run.

**Effort.** 6 h.

**Risk.** Low. Emission only.

**Engine behaviour change.** No.

---

## 7. Scenario labels and a metrics command

**What.**

- Scenario file, YAML, one per labelled capture: `id`, `capture` (manifest hash), `description`, `bench_state`, and `labels`: a list of `{target, value, from, to, expected}` where `from`/`to` are frame-time bounds and `expected` is COHERENT or INCOHERENT. A write is matched to a label by target, value and time window. Unlabelled writes are reported as such, never silently ignored.
- `liscere-metrics --run runs/<id> --scenario scenarios/<file>.yaml [--out metrics.json]` produces, per scenario and per run: counts of labelled writes, matched verdicts, TP, FP, FN, TN as raw counts, FP rate and FN rate with denominators stated, unmatched writes, and a full confusion table of expected {COHERENT, INCOHERENT} against observed {COHERENT, UNUSUAL, INCOHERENT, UNCERTAIN}.
- `experiments/` folder with a naming convention `EXP-<yyyy>-<nn>-<slug>/` holding the scenario, the run id(s), the metrics output and a short `README.md`; a CI check enforces that every `metrics.json` names a run id and a capture hash.

**Counting UNUSUAL and UNCERTAIN, options for your decision.** Positive means "the observer raised an alarm".

| Option | INCOHERENT | UNUSUAL | UNCERTAIN | Effect |
|---|---|---|---|---|
| A. Strict | alarm | no alarm | no alarm | most conservative: an UNUSUAL or UNCERTAIN on a labelled-incoherent write is an FN; on a labelled-coherent write it is a TN |
| B. Alarm-inclusive | alarm | alarm | no alarm | UNUSUAL counts as a raised flag; raises FP on coherent writes, lowers FN on incoherent ones |
| C. Abstention | alarm | alarm | excluded | UNCERTAIN removed from both denominators and reported as an abstention rate; states plainly how often the engine declined to judge |
| D. Full table | always published | always published | always published | the 2 by 4 table is the primary artefact; A, B or C is chosen only for the headline rate |

Recommendation: D with A for the headline. A is the claim easiest to defend and the table keeps nothing hidden.

**Why.** This is the measurement itself.

**Effort.** 12 h.

**Risk.** Low. Pure post-processing over the run record.

**Engine behaviour change.** No.

---

## 8. Grammar and profile persistence, and a stabilisation metric

**What.**

- In the continuous run, each silo writes its calibration profile at the end of OBSERVE and its grammar at the end of LEARN into the run directory (`silos/<endpoint>/profile.json`, `grammar.json`), for any number of silos. `--resume-from runs/<id>` loads them and skips OBSERVE and LEARN for silos that have both. The single-silo `--grammar` and `--profile` flags stay as they are.
- Cycle detection in the observer layer: a completed process cycle is one return of the phase sequence to its starting phase (for the bench, FALLING then RISING). Engine untouched; the observer watches emitted phase changes.
- Stabilisation metric, recorded per cycle in `run.json` and emitted as a `learning_progress` event: for each silo, snapshot the grammar at each cycle boundary and compute the largest change in any target's phase fractions since the previous snapshot and whether the set of `learned_coherent_phases` changed. Stable is declared after K consecutive cycles (default 3) with no set change and fraction change below epsilon (default 0.05). The record stores `cycles_to_stable`, `wall_seconds_to_stable`, `writes_observed`, and a `human_hours` field that the operator fills in by hand in `run.json` (the engine cannot measure it and should not guess).
- Optional `--learn-until-stable` in place of a fixed learn window; the fixed window stays the default.

**Why.** This is the model-building cost record. Today the grammar is lost at exit in the default run and there is no notion of convergence.

**Effort.** 14 h.

**Risk.** Medium. Cycle detection must not misfire on a flicker; it reads the de-duplicated phase stream and requires the full sequence, which the hold-resampler test already asserts.

**Engine behaviour change.** No for learning and evaluation. `--learn-until-stable` changes when learning stops; opt-in.

---

## 9. Tag exclusion as configuration

**What.** `ObserverConfig` gains `exclude_flows: list[str]` and `state_signal: str | None`. A TOML config file (`liscere.toml`, passed with `--config`, default path `/etc/liscere/liscere.toml` on the Pi) carries it. Excluded flow keys (for example `opcua:sub:3`, or a node id once handle-to-node mapping is known) are dropped from discovery candidates and never fed to the tracker; they still appear in the UI, marked excluded, so the exclusion is visible. `state_signal`, when set, pins the state flow instead of relying on the most-distinct-values rule.

Current bench value: unknown, shipped as an empty list with a comment. When you find where the Temperature exclusion lives, either it moves into this file or, if it turns out to be a subscription setting on the HMI, the file records that fact and stays empty.

**Why.** Portability to a second process needs the bench's one known manual step to be a declared setting; and the state-signal choice is the single most consequential inference in the pipeline.

**Effort.** 5 h.

**Risk.** Low.

**Engine behaviour change.** Yes when set: discovery sees fewer flows. No when empty.

---

## 10. Resume-after-pause layer

**What.**

- `README.md` rewritten with two sections at the top. "Start here": what Liscere is in five lines, the exclusions, how to run a replay in Codespaces, how to update the Pi, where the data is. "Where we are": deliberately short and link-based (latest release, the Project board, the open milestone), so it cannot go stale; a CI check fails if the section names a release tag that is not the latest.
- `docs/` with MkDocs Material on GitHub Pages, built by a workflow on push to `main`: architecture (the section 4 map from the audit, as a diagram), JSON event reference (every event type and field), verdict record schema, test convention, bench wiring (tags, node ids, mirror port, Pi address), Pi runbook (install, update, check, rollback, logs), experiments convention, decision log (ADRs in `docs/adr/`).
- GitHub Project board with one view per research track (Measurement, Model cost, Portability, Bench). Issues are the only to-do list; a `CONTRIBUTING.md` line says so. Milestones: M1 stack merged and released, M2 replay and record, M3 first measured FP/FN, M4 second process.
- `CHANGELOG.md` in Keep a Changelog form; release notes auto-generated from PR titles and labels (GitHub's built-in generator with a `release.yml` category file); the release workflow tags, builds the wheel, attaches the SBOM (item 11) and the sha256 file that `liscere-update` verifies.
- `.devcontainer/` with Python, `tshark`, `uv` and the repository installed, so a Codespace can run the full test suite and a replay from the GitHub page with no laptop setup.

**Why.** This is the operating principle you set: stop for weeks, resume from the GitHub page.

**Effort.** 16 h, plus about 2 h per later item to keep the docs current.

**Risk.** Low. The only risk is documentation rot; mitigated by generating what can be generated and keeping "Where we are" to links.

**Engine behaviour change.** No.

---

## 11. Remaining hygiene

**What.** Dependabot for GitHub Actions and Python (weekly, grouped); PR template (what changed, what was verified, engine behaviour changed yes/no); issue templates (bug, experiment, bench observation); `SECURITY.md` with a private reporting address; CycloneDX SBOM (`cyclonedx-py environment`) attached to every release; remove `otlab_core/engine/adapter.py` and its export (no caller outside tests); rewrite the stale docstrings in `otlab_core/__init__.py`, `engine/__init__.py`, `contract.py`, `evaluator.py` and the observer header to describe the current architecture; `.gitignore` covering `runs/`, `*.jsonl`, `*.pcap*`, `*_grammar.json`, `*_profile.json`.

**Effort.** 6 h.

**Risk.** Low. Removing `adapter.py` is the only code change; a grep shows no external caller.

**Engine behaviour change.** No.

---

## Declared-policy layer: stay out for now

Recommendation: **do not reintroduce a declared-policy module in this two-month window.** Reasons: the goal is measurement of the learned engine, and a second verdict source would make every FP or FN attributable to two mechanisms instead of one; the scenario labels in item 7 already express declared expectations, which is what a policy would be used for during evaluation; and the retired policy engine on the `legacy-engine-v1` tag is coupled to an HTTP backend that no longer exists, so nothing is recoverable cheaply.

If it returns later, the cheap form is a declared grammar in the same JSON shape the learner exports (target, coherent phases), overlaid on the learned grammar before `evaluate()`. That is a data-file feature, not a new verdict path, and item 8's persistence format should be chosen with that in mind. I propose recording this as an ADR in item 10.

---

## Totals and sequencing proposal

| Item | Hours | Depends on |
|---|---|---|
| 1 Captures rescue | 6 | none |
| 2 Stack, CI, main, protection | 5 | none |
| 3 Packaging | 12 | 2 |
| 4a Pi install, unit, update | 10 | 3 |
| 4b Pi check | 6 | 5, 7 |
| 5 Replay and clock | 16 | 2 |
| 6 Verdict record | 6 | 2 |
| 7 Labels and metrics | 12 | 1, 5, 6 |
| 8 Persistence and stabilisation | 14 | 6 |
| 9 Tag exclusion config | 5 | 3 |
| 10 Resume layer | 16 | 2 |
| 11 Hygiene | 6 | 2, 3 |
| Total | 114 | |

Suggested first fortnight: 1, 2, 6, 5, then a first tagged release `v0.1.0` (the bench code, packaged, with the run record) so the Pi can take its first one-command update.

## Open decisions for you

1. Branch names. Your Phase 3 rule names two branches (`chore/repo-modernisation`, `feat/measurement`) but also asks for one small PR per item. One PR per item needs one branch per item. I propose `chore/<item-slug>` for items 2, 3, 4, 10, 11 and `feat/<item-slug>` for 5 to 9, all from `main`. Confirm or keep the two long-lived branches with stacked PRs.
2. Storage for captures: private `Bench-Data` repository with release assets (recommended) or another option from item 1.
3. UNUSUAL and UNCERTAIN counting: A, B, C, or D with a headline choice.
4. Credentials on the Pi for item 1 (a `gh` login) or a one-off laptop hop.
5. `uv` or `pip-tools` for the lockfile.
6. Whether the Pi's Python version is 3.11 or newer, so `requires-python` is a fact rather than an assumption.

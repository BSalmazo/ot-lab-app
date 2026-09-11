# Liscere

Liscere is a passive observer of industrial control traffic. From a mirror port, with no address on
the segment and injecting nothing, it infers the operational state of the physical process from the
observed process variables and judges whether each supervisory control write is coherent with that
state. Every verdict (COHERENT or INCOHERENT, with UNUSUAL and UNCERTAIN when the evidence is thin)
carries its evidence: artefact, value, inferred state, confidence, reason. The operational grammar is
learned from normal operation.

Liscere is not an interlock and is never in the control path. It is not a signature IDS, not an
enforcement layer, and not a replacement for firewalls, segmentation or access control.

## Start here

1. **What the repository is.** `otlab_core/` is the engine (protocol-neutral core, passive protocol
   extractors). `liscere/` holds the programs: `liscere-observe`, `liscere-probe`, `liscere-ui`,
   `liscere-metrics`. `deploy/pi/` is the Raspberry Pi installation. `tests/` are script-style tests
   run by pytest. Read [STATE_OF_THE_REPO.md](STATE_OF_THE_REPO.md) for the audit that started the
   current work and [MODERNISATION_PLAN.md](MODERNISATION_PLAN.md) for the plan and its decisions.
2. **Try it without a bench.** Open the repository in a Codespace (Code, Codespaces, Create) or run
   `make install` locally; then `make test`, or replay a recorded run:
   `uv run liscere-observe --replay runs/<run_id> --observe 60 --learn 120`.
3. **The Pi.** One command to update, one to roll back: [deploy/pi/README.md](deploy/pi/README.md).
   Releases are the only thing that reaches the Pi.
4. **The data.** Bench captures live in the private `LiscereSecurity/Bench-Data` repository as release
   assets; their index is copied to [experiments/captures/MANIFEST.json](experiments/captures/MANIFEST.json).
   Every published number traces to a run id and a capture hash ([experiments/README.md](experiments/README.md)).
5. **The rules.** Every change reaches the repository through a pull request; `main` is protected.
   Issues are the only to-do list. British English, no em dashes. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Where we are

- Latest release: **v0.1.0** ([releases](https://github.com/LiscereSecurity/OT-Lab/releases)).
  `tests/test_readme.py` fails if this line and `pyproject.toml` disagree, so it cannot go stale.
- What changed since: [CHANGELOG.md](CHANGELOG.md).
- What is next: the open [milestones](https://github.com/LiscereSecurity/OT-Lab/milestones) and
  [issues](https://github.com/LiscereSecurity/OT-Lab/issues). The research goal for the coming
  months is measurement, not features: repeatable false-positive and false-negative counts and rates
  over recorded traffic, a measured cost of building the model, and running the engine against a
  second process and protocol without touching the core.

## Install and run

Requires Python 3.11 or newer, `tshark` on `PATH`, and [uv](https://docs.astral.sh/uv/).

```
uv sync --extra ui          # engine, terminal UI and nothing else
uv run liscere-observe --iface eth0 --observe 60 --learn 120
uv run liscere-probe --iface eth0 --duration 30
uv run liscere-observe ... 2>/dev/null | uv run liscere-ui
```

Every run writes a record under `runs/<run_id>/`: `run.json` (manifest, calibration, grammar,
stabilisation), `events.jsonl` (the stream), `verdicts.jsonl` (one record per verdict with its
evidence), `capture/` (the raw input) and `silos/<endpoint>/` (profile and grammar). A run can be
repeated exactly, without tshark or the bench, and continued on a saved model:

```
uv run liscere-observe --pcap capture.pcapng --observe 60 --learn 120    # from a capture file
uv run liscere-observe --replay runs/<run_id> --observe 60 --learn 120   # from a recorded run
uv run liscere-observe --replay runs/<run_id> --observe 60 --resume-from runs/<earlier_run>
uv run liscere-metrics --run runs/<run_id> --scenario scenarios/<file>.toml --out metrics.json
```

Replay runs on frame time, so the same input always gives the same output. Deployment settings
(excluded tags, a pinned state signal) come from a TOML file passed with `--config`; see
[deploy/pi/liscere.toml](deploy/pi/liscere.toml).

Developers: `make install`, `make test`, `make lint`, `make run IFACE=eth0`, `make release VERSION=x.y.z`.
The `scripts/liscere_*.py` files are shims kept so older command lines keep working.

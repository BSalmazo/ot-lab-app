# Start here

Liscere is a passive observer of industrial control traffic. From a mirror port, with no address on
the segment and injecting nothing, it infers the operational state of the physical process from the
observed process variables and judges whether each supervisory control write is coherent with that
state. Every verdict (COHERENT or INCOHERENT, with UNUSUAL and UNCERTAIN when the evidence is thin)
carries its evidence: artefact, value, inferred state, confidence, reason. The operational grammar is
learned from normal operation.

Liscere is not an interlock and is never in the control path. It is not a signature IDS, not an
enforcement layer, and not a replacement for firewalls, segmentation or access control.

## If you are resuming after a pause

1. The [README on GitHub](https://github.com/LiscereSecurity/OT-Lab#where-we-are) states the latest
   release and links the open milestones and issues. Issues are the only to-do list.
2. The [changelog](changelog.md) says what changed since the last release.
3. [MODERNISATION_PLAN.md](https://github.com/LiscereSecurity/OT-Lab/blob/main/MODERNISATION_PLAN.md)
   and [STATE_OF_THE_REPO.md](https://github.com/LiscereSecurity/OT-Lab/blob/main/STATE_OF_THE_REPO.md)
   are the plan and the audit it came from.
4. To run something without the bench, open a Codespace on the repository and replay a recorded run
   ([Replay and recording](replay.md)). To update the Pi, see the [runbook](pi.md).

## Reading order

- [Architecture](architecture.md): modules, data flow, what is protocol-specific.
- [Run record and events](run-record.md): what a run leaves on disk and every event it emits.
- [Replay and recording](replay.md), [Learning, resume and stabilisation](learning.md),
  [Scenarios and metrics](metrics.md), [Configuration](config.md).
- [Bench](bench.md): the reference process and the validated behaviour.
- [Tests and contributing](contributing.md), [Decisions](decisions.md).

## Install and run

Requires Python 3.11 or newer, `tshark` on `PATH`, and [uv](https://docs.astral.sh/uv/).

```
uv sync --extra ui
uv run liscere-observe --iface eth0 --observe 60 --learn 120
uv run liscere-observe --replay runs/<run_id> --observe 60 --learn 120
uv run liscere-metrics --run runs/<run_id> --scenario scenarios/<file>.toml --out metrics.json
```

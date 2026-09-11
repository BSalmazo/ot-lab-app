# Changelog

All notable changes to this repository are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow the release tags.
Release notes on GitHub are generated from pull request titles and labels (`.github/release.yml`).

## [Unreleased]

### Added
- One-command archiving of bench captures from the Pi to the private Bench-Data repository, with a
  manifest merged on GitHub (`bench-data-upload`, PR 28).
- `liscere-metrics`: false positives and false negatives of a run against labelled scenarios (TOML),
  full expected-by-observed table, strict headline, abstention rate, alternatives (PR 29).
- Per-silo grammar and profile persisted in the run directory; `--resume-from`; per-cycle
  stabilisation metric with K and epsilon recorded beside `cycles_to_stable`; `--learn-until-stable`
  (PR 30).
- Deployment configuration `liscere.toml` (`--config`): `exclude_flows` and a pinned `state_signal`,
  recorded in `run.json` (PR 31).
- README "Start here" and "Where we are", this changelog, contribution rules, devcontainer,
  release-note categories, milestones and the issue list as the to-do list.

### Fixed
- The per-target learn log reported only the last silo since the run record was introduced (PR 30).

## [0.1.0] - 2026-09-11

First tagged release: the bench-validated observer, packaged.

### Added
- CI running every test file on every push and pull request, on Python 3.11 and 3.13 (PR 18).
- The bench-validated stack merged: multi-protocol selector loop and respawn, per-ClientHandle
  attribution and the hold-resampler, UI redraw fix (PRs 20 to 22). Default branch renamed to `main`,
  branch protection, CODEOWNERS (PR 23).
- Packaging: `pyproject.toml`, `uv.lock`, console scripts `liscere-observe`, `liscere-probe`,
  `liscere-ui`, ruff, mypy (non-blocking), pytest wrapper, pre-commit, Makefile (PR 24).
- Run record: `run.json`, `events.jsonl`, `verdicts.jsonl` with full evidence per verdict (PR 25).
- Deterministic replay of a recorded run or a pcap on frame time; every live run records its raw
  input (PR 26).
- Raspberry Pi install script, systemd unit, `liscere-update` with checksum verification and
  rollback; release workflow attaching wheel, sdist and `SHA256SUMS` (PR 27).

[Unreleased]: https://github.com/LiscereSecurity/OT-Lab/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/LiscereSecurity/OT-Lab/releases/tag/v0.1.0

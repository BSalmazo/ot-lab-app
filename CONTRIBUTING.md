# Contributing

- **GitHub is the only source of truth.** Work from a fresh clone or a Codespace; nothing depends on
  a laptop. Every change reaches the repository through a pull request against `main`; `main` is
  protected and requires the CI checks. Never rewrite history, never force-push.
- **One pull request per item**, small enough to review in ten minutes. The description says what
  changed, what was verified, and whether engine behaviour changed (yes or no). Label the PR
  (`enhancement`, `bug`, `documentation`, `chore`) so the release notes sort it.
- **Issues are the only to-do list.** Anything not in an issue does not exist. Milestones group
  issues by research goal; the `bench-action` label marks work that needs a person at the bench.
- **Engine behaviour is sacred.** Hygiene changes must not change verdicts. The bench-validated
  behaviour is the acceptance test: phase sequence RISING, STABLE, RISING, STABLE, FALLING; held
  values exactly 50.0 and 80.0; no flicker; ValveB=40 during heating INCOHERENT and during add-B
  COHERENT (`tests/test_hold_resampler.py`, `tests/test_replay.py`).
- **Writing.** British English, short plain sentences, bottom line first, no em dashes (the
  pre-commit hook rejects them), never overclaim beyond what the code and data show. This applies to
  code, comments, commit messages, docs and PR descriptions.
- **Tests** are standalone scripts under `tests/` (each prints PASS/FAIL per check and exits non-zero
  on failure); pytest runs them through `tests/test_scripts.py`. Add a script per item; do not
  rewrite existing ones without a reason stated in the PR.
- **Releases** are the unit that reaches the Pi: bump the version in `pyproject.toml` and the
  "Latest release" line in README in the same PR, merge, then `make release VERSION=x.y.z` (or the
  GitHub release page). The Release workflow attaches the wheel and checksums; the Pi updates with
  `sudo liscere-update`.
- **Permanent exclusions.** Liscere is not an interlock, never in the control path; not a signature
  IDS; not an enforcement layer; not a replacement for firewalls, segmentation or access control.
  Do not propose or implement any of these.

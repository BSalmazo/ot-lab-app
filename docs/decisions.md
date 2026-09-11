# Decisions

Short records of decisions that shape the repository. Newer entries first.

## ADR-004: GitHub is the single source of truth (2026-09-11)

No laptop clone is maintained. Every change reaches the repository through a pull request; `main`
is protected; the Pi runs tagged releases only; captures live in a repository; the run record and
the docs make it possible to stop for weeks and resume from the GitHub page alone.

## ADR-003: Releases are the deployment unit (2026-09-11)

The Pi never runs from a branch. A GitHub release triggers a workflow that builds the wheel and
attaches it with checksums; `liscere-update` on the Pi fetches, verifies, installs and can roll
back. Version in `pyproject.toml` and the README's latest-release line are bumped in the release PR
and checked by a test.

## ADR-002: Scenario files are TOML, not YAML (2026-09-11)

The package has no runtime dependencies and the Pi check must run on the system Python. Python 3.11
reads TOML from the standard library; YAML would add a dependency. The scenario format is otherwise
as planned (labels with target, optional value, time window, expected verdict).

## ADR-001: The declared-policy layer stays out (2026-09-11)

During the measurement work the engine has one verdict source, the learned grammar. A second source
would make every false positive or negative attributable to two mechanisms. Scenario labels already
express declared expectations for evaluation. If a declared policy returns later, it will be a
declared grammar in the same JSON shape the learner exports, overlaid before evaluation: a data
file, not a new verdict path. The retired policy engine remains on the `legacy-engine-v1` tag.

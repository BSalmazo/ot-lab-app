#!/usr/bin/env python3
"""README "Where we are" must not go stale: the Latest release line equals the version in
pyproject.toml (both are bumped in the release PR), and the documents it links to exist.
`python3 tests/test_readme.py`."""
import os
import re
import tomllib

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not cond:
        _failures.append(name)


def main():
    with open(os.path.join(_REPO, "README.md")) as f:
        readme = f.read()
    with open(os.path.join(_REPO, "pyproject.toml"), "rb") as f:
        version = tomllib.load(f)["project"]["version"]
    m = re.search(r"Latest release: \*\*v([0-9][^*]*)\*\*", readme)
    check("README names the latest release", m is not None)
    check("README latest release equals pyproject version", m is not None and m.group(1) == version,
          f"(readme={m.group(1) if m else None}, pyproject={version})")
    for link in re.findall(r"\]\(([A-Za-z0-9_./-]+\.md)\)", readme):
        check(f"linked document exists: {link}", os.path.isfile(os.path.join(_REPO, link)))
    check("README has Start here and Where we are", "## Start here" in readme and "## Where we are" in readme)
    print()
    if _failures:
        print(f"README TEST: FAIL ({len(_failures)}: {_failures})")
        return 1
    print("README TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

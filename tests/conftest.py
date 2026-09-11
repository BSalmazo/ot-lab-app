"""pytest support for the script-style tests.

The files under tests/ are standalone scripts (each has a check() helper and a main()). pytest
also collects the few functions named test_* inside them; one of those asks for a tmp_profile
fixture that its own main() passes explicitly. This fixture supplies it under pytest so both ways
of running the tests pass. Nothing else here; see tests/test_scripts.py for the wrapper.
"""

import pytest


@pytest.fixture
def tmp_profile(tmp_path):
    return str(tmp_path / "profile.json")

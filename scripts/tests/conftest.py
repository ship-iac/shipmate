"""Suite-wide hooks: the `bash_only` marker, whose bash probe runs only when a marked test does."""

import pytest
from _loader import usable_bash


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "bash_only: executes a shipped shell body; skipped with no working bash"
    )


def pytest_runtest_setup(item):
    if item.get_closest_marker("bash_only") and usable_bash() is None:
        pytest.skip("no working bash on this host")

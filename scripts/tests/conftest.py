"""Suite-wide hooks: the `bash_only` marker, whose bash probe runs only when a marked test does,
a `_shipmate._sleep` that fails any test reaching a retry without patching it, a fresh
rate-limit wait budget and deferred-run cache per test, a per-test path for `apply-detect`'s
report, and a `sleeps` fixture recording the delays of a patched `_shipmate._sleep`."""

import _shipmate
import pytest
from _loader import usable_bash


@pytest.fixture(autouse=True)
def _apply_report_path(monkeypatch, tmp_path):
    """`apply-detect` writes its report to `SHIPMATE_REPORT_PATH`, which its action sets."""
    monkeypatch.setenv("SHIPMATE_REPORT_PATH", str(tmp_path / "apply-report.json"))


@pytest.fixture(autouse=True)
def _no_unpatched_retry_sleep(monkeypatch):
    """A test that patches `_shipmate._sleep` itself overrides this. Child processes (`run_step`,
    shims) do not see it."""

    def sleep(delay):
        pytest.fail(f"_shipmate._sleep({delay}) ran unpatched: patch `_shipmate._sleep`")

    monkeypatch.setattr(_shipmate, "_sleep", sleep)
    # The budget is per process, and every in-process test shares one.
    monkeypatch.setattr(_shipmate, "_rate_limit_waits", 0)
    # `apply-gate` binds the dict at import, so it is emptied, not replaced.
    _shipmate.deferred_runs.clear()


@pytest.fixture
def sleeps(monkeypatch):
    slept = []
    monkeypatch.setattr("_shipmate._sleep", slept.append)
    return slept


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "bash_only: executes a shipped shell body; skipped with no working bash"
    )


def pytest_runtest_setup(item):
    if item.get_closest_marker("bash_only") and usable_bash() is None:
        pytest.skip("no working bash on this host")

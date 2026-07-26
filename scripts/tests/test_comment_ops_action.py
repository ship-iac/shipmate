"""Source-derived guards on the comment-ops action's routing wiring.

The action branches on `steps.parse.outputs.route`; the routes come from
comment-parse's VERBS registry. A verb added to the registry with no branch in
the action would parse, authorize, and then silently do nothing.
"""

import importlib.util
import json
import pathlib
import re
from importlib.machinery import SourceFileLoader

_D = pathlib.Path(__file__).resolve().parents[1]
_ACTION = (_D.parent / "actions" / "comment-ops" / "action.yml").read_text(encoding="utf-8")


def _load(fname):
    loader = SourceFileLoader(fname.replace("-", "_"), str(_D / fname))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


cp = _load("comment-parse")
doctor = _load("doctor")


def test_every_active_route_has_a_branch():
    routed = set(re.findall(r"outputs\.route == '([a-z]+)'", _ACTION))
    expected = {s["route"] for s in cp.VERBS.values() if s["route"]}
    assert expected <= routed, expected - routed


def test_bot_authored_comments_are_ignored():
    assert '== *"[bot]"' in _ACTION


def test_doctor_step_supplies_every_env_var_doctor_reads():
    src = (_D / "doctor").read_text(encoding="utf-8")
    read = set(re.findall(r"os\.environ(?:\.get)?[\[(]['\"](SHIPMATE_[A-Z_]+)['\"]", src))
    for name in read:
        assert name in _ACTION, name


def test_doctor_runs_in_report_mode_with_the_app_token():
    assert "SHIPMATE_DOCTOR_MODE: report" in _ACTION
    assert "SHIPMATE_DOCTOR_MODE: check-ids" in _ACTION
    assert "steps.doctortoken.outputs.token" in _ACTION


def test_doctor_sticky_marker_matches_the_script():
    assert doctor.DOCTOR_MARKER in _ACTION


def test_help_does_not_require_the_app():
    """help must answer even when the App is not installed — the state where a
    newcomer most needs it — so it posts with the workflow token."""
    block = _ACTION.split("route == 'help'", 1)[1].split("- name:", 1)[0]
    assert "inputs.github-token" in block


def test_fullmint_requests_the_manifests_exact_permission_set():
    """The full-set probe mint must mirror app/manifest.json: a manifest bump
    that skips this step makes the permission-drift probe test the stale set —
    exactly the drift the probe exists to catch."""
    block = _ACTION.split("id: fullmint", 1)[1].split("- name:", 1)[0]
    requested = dict(re.findall(r"permission-([a-z-]+): (read|write)", block))
    manifest = json.loads((_D.parent / "app" / "manifest.json").read_text(encoding="utf-8"))
    declared = {k.replace("_", "-"): v for k, v in manifest["default_permissions"].items()}
    assert requested == declared

"""I3 / M2 -- Compose cell summary and Upload apply summary must not be able
to strand an otherwise-successful apply's check pending.

Both steps are new `if: always()` steps sitting between `Apply the stored
plan` and `Complete the apply check`, with no `continue-on-error`. GHA
semantics: a failed step (even one gated by `if: always()`) sets the job to
failure, and every later step whose `if` defaults to `success()` is skipped.
So a purely cosmetic failure here (a transient artifact-service hiccup, a
disk-full write) would leave `Complete the apply check` never running while
`Save state` has already advanced past the reviewed `.otplan` -- unrecoverable
without a re-plan. `continue-on-error: true` on both steps fixes this without
weakening the failed-apply case: the apply step's own failure still makes
`success()` false for the completion steps regardless of these two.

M2, same file: `cell.json` / `apply.txt` were written into the repo tree,
which the sibling fail-safe steps explicitly avoid (a stray untracked file at
repo root fails the apply once git-untracked runs). Writing under
`$RUNNER_TEMP` instead removes the ordering dependency on "nothing after this
touches terramate/tofu" entirely.
"""

import pathlib

import yaml

_ACTIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "actions"
_ACTION_PATH = _ACTIONS_DIR / "apply-cell" / "action.yml"


def _steps():
    spec = yaml.safe_load(_ACTION_PATH.read_text(encoding="utf-8"))
    return (spec.get("runs") or {}).get("steps") or []


def _step(name):
    matches = [s for s in _steps() if s.get("name") == name]
    assert len(matches) == 1, f"expected exactly one {name!r} step, got {len(matches)}"
    return matches[0]


def test_compose_cell_summary_has_continue_on_error():
    assert _step("Compose cell summary").get("continue-on-error") is True


def test_upload_apply_summary_has_continue_on_error():
    assert _step("Upload apply summary").get("continue-on-error") is True


def test_upload_apply_summary_has_overwrite():
    with_ = _step("Upload apply summary").get("with") or {}
    assert with_.get("overwrite") is True


def test_upload_apply_summary_paths_are_under_runner_temp_not_repo_tree():
    with_ = _step("Upload apply summary").get("with") or {}
    lines = [ln for ln in (with_.get("path") or "").splitlines() if ln.strip()]
    assert lines == ["${{ runner.temp }}/cell.json", "${{ runner.temp }}/apply.txt"]


def test_compose_cell_summary_writes_cell_json_under_runner_temp_not_repo_tree():
    run = _step("Compose cell summary").get("run") or ""
    assert 'os.path.join(os.environ["RUNNER_TEMP"], "cell.json")' in run
    # The old repo-tree writes must be gone, not merely joined by a new one.
    assert 'open("cell.json"' not in run
    assert 'open("apply.txt"' not in run
    assert 'shutil.copyfile(apply_log, "apply.txt")' not in run


def test_both_new_steps_still_run_always():
    # continue-on-error must not be paired with dropping if: always() -- a
    # blocked/failed cell still needs its cell.json composed and uploaded.
    for name in ("Compose cell summary", "Upload apply summary"):
        step = _step(name)
        raw = str(step.get("if", "")).strip()
        assert raw in ("always()", "${{ always() }}"), f"{name} if: changed to {raw!r}"

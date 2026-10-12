"""pending-checks: check-run POST bodies from downloaded cell summaries."""

import json
import os

import pytest
from _loader import action_yaml, load_script

pc = load_script("pending-checks")

HEAD = "a" * 40
RUN_ID = "32668143791"
PR = "4217"

# Hand-written: the bytes and the digest below were computed once, off the tree, so an
# assertion cannot be satisfied by the same hashlib call the code under test makes. The
# trailing newline is load-bearing -- it is what a `.strip()` before hashing would drop.
PLAN_A = b"Terraform will perform the following actions:\n\n  # null_resource.a will be created\n"
SHA_A = "4e6d7400bf477bbcb3b78627cecd50f1a4706fa04da5a4f809ae0e1fb3b58704"
PLAN_B = b"No changes. Your infrastructure matches the configuration.\n"
SHA_B = "016cf8c84f06456af2ec0f1866e01c39d69ecd581daddf619528719a31e3dbc8"


@pytest.fixture(autouse=True)
def _plan_run(monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", RUN_ID)
    monkeypatch.setenv("SHIPMATE_PR_NUMBER", PR)


def _write_cell(tmp_path, env, slug, plan_text=PLAN_A, **cell):
    d = tmp_path / f"cell-summary.{env}.{slug}"
    d.mkdir(parents=True)
    (d / "cell.json").write_text(json.dumps(cell), encoding="utf-8")
    if plan_text is not None:
        # write_bytes, not write_text: Windows would translate the newline and invalidate
        # the hand-written digest.
        (d / "plan.txt").write_bytes(plan_text)


def test_changed_cell_yields_queued_body(tmp_path):
    """Mutation: omit `"pr"` from the record -- red."""
    _write_cell(
        tmp_path,
        "dev-eu",
        "stacks-app",
        stack="stacks/app",
        environment="dev-eu",
        changed=True,
        fingerprint="f" * 64,
    )
    (body,) = pc.bodies(str(tmp_path), HEAD)
    external_id = body.pop("external_id")
    assert json.loads(external_id) == {
        "fingerprint": "f" * 64,
        "plan_run": RUN_ID,
        "plan_sha256": SHA_A,
        "pr": PR,
    }
    assert body == {
        "name": "apply / stacks/app / dev-eu",
        "head_sha": HEAD,
        "status": "queued",
        "output": {
            "title": "apply pending",
            "summary": "Waiting to be applied. Merge after apply completes "
            "for this stack x environment.",
        },
    }


def test_unchanged_cell_yields_completed_neutral_body(tmp_path):
    _write_cell(
        tmp_path,
        "dev-eu",
        "stacks-dns",
        plan_text=PLAN_B,
        stack="stacks/dns",
        environment="dev-eu",
        changed=False,
        fingerprint="0" * 64,
    )
    (body,) = pc.bodies(str(tmp_path), HEAD)
    assert body["status"] == "completed"
    assert body["conclusion"] == "neutral"
    assert body["output"]["title"] == "no changes"
    assert json.loads(body["external_id"]) == {
        "fingerprint": "0" * 64,
        "plan_run": RUN_ID,
        "plan_sha256": SHA_B,
        "pr": PR,
    }


def test_missing_fingerprint_fails_loud(tmp_path):
    """Mutation: drop `"fingerprint"` from `bodies`' required keys -- a `KeyError` instead of
    the `SystemExit`."""
    _write_cell(
        tmp_path,
        "dev-eu",
        "stacks-app",
        stack="stacks/app",
        environment="dev-eu",
        changed=True,
    )
    with pytest.raises(SystemExit) as exc_info:
        pc.bodies(str(tmp_path), HEAD)
    cell_json = os.path.join(str(tmp_path), "cell-summary.dev-eu.stacks-app", "cell.json")
    assert str(exc_info.value) == (
        f"::error::cell summary {cell_json} missing keys ['fingerprint'] -- plan-cell and the "
        "summary job must run the same engine revision; re-pin every engine reference to one "
        "commit, then re-plan (comment shipmate plan or push)"
    )


def test_missing_plan_text_refuses_with_the_re_plan_remedy(tmp_path):
    """Mutation: revert the refusal's remedy to "re-run the plan"."""
    _write_cell(
        tmp_path,
        "dev-eu",
        "stacks-app",
        plan_text=None,
        stack="stacks/app",
        environment="dev-eu",
        changed=True,
        fingerprint="f" * 64,
    )
    with pytest.raises(SystemExit) as exc_info:
        pc.bodies(str(tmp_path), HEAD)
    cell_json = os.path.join(str(tmp_path), "cell-summary.dev-eu.stacks-app", "cell.json")
    assert str(exc_info.value) == (
        f"::error::{cell_json} has no plan.txt beside it: the plan text a reviewer reads cannot "
        "be bound to the plan that will be applied. plan-cell and summary are from mismatched "
        "engine revisions; re-pin every engine reference to one commit, then re-plan (comment "
        "shipmate plan or push)."
    )


def test_plan_run_comes_from_the_environment(tmp_path, monkeypatch):
    # A distinct value, so the assertion cannot be satisfied by a `plan_run` hardcoded to the
    # fixture's RUN_ID.
    _write_cell(
        tmp_path,
        "dev-eu",
        "stacks-app",
        stack="stacks/app",
        environment="dev-eu",
        changed=True,
        fingerprint="f" * 64,
    )
    monkeypatch.setenv("GITHUB_RUN_ID", "409181227")
    monkeypatch.setenv("SHIPMATE_PR_NUMBER", "31")
    (body,) = pc.bodies(str(tmp_path), HEAD)
    assert json.loads(body["external_id"]) == {
        "fingerprint": "f" * 64,
        "plan_run": "409181227",
        "plan_sha256": SHA_A,
        "pr": "31",
    }


def test_unusable_plan_run_id_fails_loud(tmp_path, monkeypatch):
    _write_cell(
        tmp_path,
        "dev-eu",
        "stacks-app",
        stack="stacks/app",
        environment="dev-eu",
        changed=True,
        fingerprint="f" * 64,
    )
    monkeypatch.delenv("GITHUB_RUN_ID")
    with pytest.raises(SystemExit, match="GITHUB_RUN_ID"):
        pc.bodies(str(tmp_path), HEAD)
    monkeypatch.setenv("GITHUB_RUN_ID", "not-a-run-id")
    with pytest.raises(SystemExit, match="GITHUB_RUN_ID"):
        pc.bodies(str(tmp_path), HEAD)


@pytest.mark.parametrize("value", ["", "12a", "0", "012", " 8", "12345678901"])
def test_an_unusable_pull_request_number_refuses_naming_the_variable(tmp_path, monkeypatch, value):
    """apply-detect refuses a record whose `pr` is not the dispatch's, so a record written with
    a malformed number strands its cell. Mutation: drop the validation (`pr =
    os.environ.get("SHIPMATE_PR_NUMBER", "")`) -- red. Mutation: `re.fullmatch(r"[1-9][0-9]*",
    value)` for `is_pr_number(value)` in `env_pr_number` -- `"12345678901"` red."""
    _write_cell(
        tmp_path,
        "dev-eu",
        "stacks-app",
        stack="stacks/app",
        environment="dev-eu",
        changed=True,
        fingerprint="f" * 64,
    )
    monkeypatch.setenv("SHIPMATE_PR_NUMBER", value)
    with pytest.raises(SystemExit) as exc_info:
        pc.bodies(str(tmp_path), HEAD)
    assert str(exc_info.value) == (
        f"::error::SHIPMATE_PR_NUMBER must be a pull request number (got: {value!r})"
    )


def test_an_unset_pull_request_number_refuses(tmp_path, monkeypatch):
    """Explicit literal beside the parametrization. Mutation: drop the validation -- red."""
    _write_cell(
        tmp_path,
        "dev-eu",
        "stacks-app",
        stack="stacks/app",
        environment="dev-eu",
        changed=True,
        fingerprint="f" * 64,
    )
    monkeypatch.delenv("SHIPMATE_PR_NUMBER")
    with pytest.raises(SystemExit) as exc_info:
        pc.bodies(str(tmp_path), HEAD)
    assert (
        str(exc_info.value) == "::error::SHIPMATE_PR_NUMBER must be a pull request number (got: '')"
    )


def test_the_publishing_step_hands_the_script_exactly_these_names():
    """The whole `env:` of the summary step running pending-checks. Mutation: drop
    `SHIPMATE_PR_NUMBER` from it -- red."""
    (step,) = [
        s
        for s in action_yaml("summary")["runs"]["steps"]
        if "scripts/pending-checks" in str(s.get("run", ""))
    ]
    assert step["env"] == {
        "GH_TOKEN": "${{ steps.token.outputs.token }}",
        "SHIPMATE_HEAD_SHA": "${{ inputs.head-sha }}",
        "SHIPMATE_PR_NUMBER": "${{ inputs.pr-number }}",
    }


def test_cells_sorted_and_multiple(tmp_path):
    _write_cell(
        tmp_path,
        "dev-us",
        "stacks-app",
        stack="stacks/app",
        environment="dev-us",
        changed=True,
        fingerprint="1" * 64,
    )
    _write_cell(
        tmp_path,
        "dev-eu",
        "stacks-app",
        stack="stacks/app",
        environment="dev-eu",
        changed=True,
        fingerprint="2" * 64,
    )
    names = [b["name"] for b in pc.bodies(str(tmp_path), HEAD)]
    assert names == ["apply / stacks/app / dev-eu", "apply / stacks/app / dev-us"]


def test_single_cell_downloaded_flat_still_yields_a_body(tmp_path):
    """`actions/download-artifact` extracts into `path` itself, with no per-artifact
    subdirectory, whenever exactly one artifact matches the pattern
    (`artifacts.length === 1 ? resolvedPath : join(resolvedPath, name)`). A one-cell plan
    therefore lands at `cells/cell.json`, and a glob insisting on the nested layout emits
    nothing: no pending apply check, so the pre-apply snapshot refuses with "nothing to complete
    afterwards" and post-merge deploy-detect finds an empty work queue."""
    (tmp_path / "cell.json").write_text(
        json.dumps(
            {
                "stack": "stacks/auth",
                "environment": "dev-eu",
                "changed": True,
                "fingerprint": "c" * 64,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "plan.txt").write_bytes(PLAN_A)
    (body,) = pc.bodies(str(tmp_path), HEAD)
    assert body["name"] == "apply / stacks/auth / dev-eu"
    assert body["status"] == "queued"


def test_each_cell_gets_the_digest_of_its_own_plan_text(tmp_path):
    """Mutation: hash the first plan.txt found and reuse it for every cell."""
    _write_cell(
        tmp_path,
        "dev-eu",
        "stacks-app",
        plan_text=PLAN_A,
        stack="stacks/app",
        environment="dev-eu",
        changed=True,
        fingerprint="1" * 64,
    )
    _write_cell(
        tmp_path,
        "dev-us",
        "stacks-app",
        plan_text=PLAN_B,
        stack="stacks/app",
        environment="dev-us",
        changed=True,
        fingerprint="2" * 64,
    )
    eu, us = pc.bodies(str(tmp_path), HEAD)
    assert json.loads(eu["external_id"])["plan_sha256"] == SHA_A
    assert json.loads(us["external_id"])["plan_sha256"] == SHA_B


def test_digest_is_computed_never_copied_from_the_cell(tmp_path):
    """external_id's plan_sha256 is the digest of plan.txt, not the cell's claim about it.

    Mutation: read the value from the cell with the computed one as a fallback."""
    _write_cell(
        tmp_path,
        "dev-eu",
        "stacks-app",
        stack="stacks/app",
        environment="dev-eu",
        changed=True,
        fingerprint="f" * 64,
        plan_sha256="b" * 64,
    )
    (body,) = pc.bodies(str(tmp_path), HEAD)
    assert json.loads(body["external_id"])["plan_sha256"] == SHA_A


def test_missing_plan_text_fails_loud_and_emits_nothing(tmp_path):
    """A run where every cell has a plan.txt returns every body; removing one cell's
    plan.txt aborts the whole run, so no body is emitted for the sound cell either.

    Mutations: raise unconditionally -- the first assertion reddens; `continue` (or a
    None/"" fallback) instead of raising -- the second does."""
    _write_cell(
        tmp_path,
        "dev-eu",
        "stacks-app",
        stack="stacks/app",
        environment="dev-eu",
        changed=True,
        fingerprint="1" * 64,
    )
    _write_cell(
        tmp_path,
        "dev-us",
        "stacks-app",
        plan_text=PLAN_B,
        stack="stacks/app",
        environment="dev-us",
        changed=True,
        fingerprint="2" * 64,
    )
    assert [
        json.loads(b["external_id"])["plan_sha256"] for b in pc.bodies(str(tmp_path), HEAD)
    ] == [
        SHA_A,
        SHA_B,
    ]

    (tmp_path / "cell-summary.dev-us.stacks-app" / "plan.txt").unlink()
    with pytest.raises(SystemExit, match=r"cell-summary\.dev-us\.stacks-app.*has no plan\.txt"):
        pc.bodies(str(tmp_path), HEAD)

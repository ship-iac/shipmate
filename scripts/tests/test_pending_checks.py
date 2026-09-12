"""pending-checks: check-run POST bodies from downloaded cell summaries."""

import json

import pytest
from _loader import load_script

pc = load_script("pending-checks")

HEAD = "a" * 40
RUN_ID = "32668143791"

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


def _write_cell(tmp_path, env, slug, plan_text=PLAN_A, **cell):
    d = tmp_path / f"cell-summary.{env}.{slug}"
    d.mkdir(parents=True)
    (d / "cell.json").write_text(json.dumps(cell), encoding="utf-8")
    if plan_text is not None:
        # write_bytes, not write_text: Windows would translate the newline and invalidate
        # the hand-written digest.
        (d / "plan.txt").write_bytes(plan_text)


def test_changed_cell_yields_queued_body(tmp_path):
    _write_cell(
        tmp_path,
        "dev-eu",
        "stacks-app",
        stack="app",
        stack_path="stacks/app",
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
    }
    assert body == {
        "name": "apply / stacks/app / dev-eu",
        "head_sha": HEAD,
        "status": "queued",
        "output": {
            "title": "apply pending",
            "summary": "Waiting to be applied. Merge after apply completes "
            + "for this stack x environment.",
        },
    }


def test_unchanged_cell_yields_completed_neutral_body(tmp_path):
    _write_cell(
        tmp_path,
        "dev-eu",
        "stacks-dns",
        plan_text=PLAN_B,
        stack="dns",
        stack_path="stacks/dns",
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
    }


def test_missing_fingerprint_fails_loud(tmp_path):
    _write_cell(
        tmp_path,
        "dev-eu",
        "stacks-app",
        stack="app",
        stack_path="stacks/app",
        environment="dev-eu",
        changed=True,
    )
    with pytest.raises(SystemExit, match="has no 'fingerprint' key"):
        pc.bodies(str(tmp_path), HEAD)


def test_plan_run_comes_from_the_environment(tmp_path, monkeypatch):
    # A distinct value, so the assertion cannot be satisfied by a `plan_run` hardcoded to the
    # fixture's RUN_ID.
    _write_cell(
        tmp_path,
        "dev-eu",
        "stacks-app",
        stack="app",
        stack_path="stacks/app",
        environment="dev-eu",
        changed=True,
        fingerprint="f" * 64,
    )
    monkeypatch.setenv("GITHUB_RUN_ID", "409181227")
    (body,) = pc.bodies(str(tmp_path), HEAD)
    assert json.loads(body["external_id"]) == {
        "fingerprint": "f" * 64,
        "plan_run": "409181227",
        "plan_sha256": SHA_A,
    }


def test_unusable_plan_run_id_fails_loud(tmp_path, monkeypatch):
    _write_cell(
        tmp_path,
        "dev-eu",
        "stacks-app",
        stack="app",
        stack_path="stacks/app",
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


def test_cells_sorted_and_multiple(tmp_path):
    _write_cell(
        tmp_path,
        "dev-us",
        "stacks-app",
        stack="app",
        stack_path="stacks/app",
        environment="dev-us",
        changed=True,
        fingerprint="1" * 64,
    )
    _write_cell(
        tmp_path,
        "dev-eu",
        "stacks-app",
        stack="app",
        stack_path="stacks/app",
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
                "stack": "auth",
                "stack_path": "stacks/auth",
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
        stack="app",
        stack_path="stacks/app",
        environment="dev-eu",
        changed=True,
        fingerprint="1" * 64,
    )
    _write_cell(
        tmp_path,
        "dev-us",
        "stacks-app",
        plan_text=PLAN_B,
        stack="app",
        stack_path="stacks/app",
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
        stack="app",
        stack_path="stacks/app",
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
        stack="app",
        stack_path="stacks/app",
        environment="dev-eu",
        changed=True,
        fingerprint="1" * 64,
    )
    _write_cell(
        tmp_path,
        "dev-us",
        "stacks-app",
        plan_text=PLAN_B,
        stack="app",
        stack_path="stacks/app",
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

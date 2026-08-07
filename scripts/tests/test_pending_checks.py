"""pending-checks: check-run POST bodies from downloaded cell summaries."""

import json

import pytest
from _loader import load_script

pc = load_script("pending-checks")

HEAD = "a" * 40


def _write_cell(tmp_path, env, slug, **cell):
    d = tmp_path / f"cell-summary.{env}.{slug}"
    d.mkdir(parents=True)
    (d / "cell.json").write_text(json.dumps(cell), encoding="utf-8")


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
    assert body == {
        "name": "apply / stacks/app / dev-eu",
        "head_sha": HEAD,
        "status": "queued",
        "external_id": "f" * 64,
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
    assert body["external_id"] == "0" * 64


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
    with pytest.raises(SystemExit, match="fingerprint"):
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
    # `actions/download-artifact` extracts into `path` itself, with no
    # per-artifact subdirectory, whenever exactly ONE artifact matches the
    # pattern (`artifacts.length === 1 ? resolvedPath : join(resolvedPath,
    # name)`). A one-cell plan therefore lands at `cells/cell.json`, and a
    # glob that insists on the nested layout emits nothing -- no pending apply
    # check, so the pre-apply snapshot refuses ("nothing to complete
    # afterwards") and post-merge deploy-detect finds an empty work queue.
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
    (body,) = pc.bodies(str(tmp_path), HEAD)
    assert body["name"] == "apply / stacks/auth / dev-eu"
    assert body["status"] == "queued"

"""scripts/lock-info: parse OpenTofu's state-lock acquisition failure."""

import pathlib

from _loader import load_script  # existing helper; see other tests for usage

li = load_script("lock-info")
FIX = pathlib.Path(__file__).parent / "fixtures"


def _fixture(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_parses_the_local_backend_capture():
    got = li.parse(_fixture("lock_error_local.txt"))
    assert got == {
        "id": "6e191647-8a1d-d0fc-5096-9dd8d93b2fb6",
        "path": "terraform.tfstate",
        "operation": "OperationTypeApply",
        "created": "2026-08-20 19:46:05.0074419 +0000 UTC",
    }


def test_parses_the_s3_capture_despite_a_different_error_message():
    got = li.parse(_fixture("lock_error_s3.txt"))
    assert got["id"] == "0f866bdc-d621-7230-876f-fa7398eff1f8"
    assert got["operation"] == "OperationTypePlan"
    assert got["path"].endswith("sandbox/box/terraform.tfstate")


def test_who_is_never_returned():
    # Who is user@host on a runner and cannot attribute a run; no caller may
    # render it, so the parser does not hand it out at all.
    assert "who" not in li.parse(_fixture("lock_error_s3.txt"))


def test_a_plain_apply_failure_is_not_a_lock():
    assert li.parse("Error: creating SSM Parameter: AccessDenied\n") is None


def test_success_output_is_not_a_lock():
    assert li.parse("Apply complete! Resources: 1 added, 0 changed, 0 destroyed.\n") is None


def test_a_truncated_block_is_not_a_lock():
    # Fail closed: the acquisition error is present but the block is cut off
    # before ID, so there is nothing safe to force-unlock.
    text = "│ Error: Error acquiring the state lock\n│ Lock Info:\n"
    assert li.parse(text) is None


def test_lock_info_without_the_acquisition_error_is_not_a_lock():
    text = "│ Lock Info:\n│   ID:        6e191647-8a1d-d0fc-5096-9dd8d93b2fb6\n"
    assert li.parse(text) is None


def test_an_out_of_charset_id_is_refused():
    # apply.txt is provider/local-exec output: a provisioner can echo a
    # look-alike block. An ID that could not be a lock id is refused rather
    # than carried into a force-unlock argv.
    text = (
        "│ Error: Error acquiring the state lock\n"
        "│ Lock Info:\n"
        "│   ID:        ; rm -rf /\n"
        "│   Path:      terraform.tfstate\n"
        "│   Operation: OperationTypeApply\n"
        "│   Created:   2026-08-20 19:46:05 +0000 UTC\n"
    )
    assert li.parse(text) is None


def test_undecorated_output_parses_too():
    # No box prefix (a future renderer, or piped through a filter).
    text = (
        "Error: Error acquiring the state lock\n"
        "Lock Info:\n"
        "  ID:        6e191647-8a1d-d0fc-5096-9dd8d93b2fb6\n"
        "  Path:      terraform.tfstate\n"
        "  Operation: OperationTypeApply\n"
        "  Created:   2026-08-20 19:46:05.0074419 +0000 UTC\n"
    )
    assert li.parse(text)["id"] == "6e191647-8a1d-d0fc-5096-9dd8d93b2fb6"

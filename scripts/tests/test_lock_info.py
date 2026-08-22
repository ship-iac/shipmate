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


def test_a_block_cut_after_the_id_is_not_a_lock():
    # Fail closed: ID is captured and valid, but the block is cut off before
    # the other required fields, so guessing an id from half a block is refused.
    text = (
        "│ Error: Error acquiring the state lock\n"
        "│ Lock Info:\n"
        "│   ID:        6e191647-8a1d-d0fc-5096-9dd8d93b2fb6\n"
    )
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


def _with(field, value):
    """The s3 capture with one field's value replaced."""
    olds = {
        "Created": "2026-08-20 19:53:19.7388258 +0000 UTC",
        "Operation": "OperationTypePlan",
    }
    return _fixture("lock_error_s3.txt").replace(olds[field], value)


S3_ID = "0f866bdc-d621-7230-876f-fa7398eff1f8"


def test_a_real_created_value_survives_its_guard():
    # The guard below must reject crafted values WITHOUT blanking the real
    # format -- a blanket blank-out would silently cost every lock its age.
    assert li.parse(_fixture("lock_error_local.txt"))["created"] == (
        "2026-08-20 19:46:05.0074419 +0000 UTC"
    )
    assert li.parse(_fixture("lock_error_s3.txt"))["created"] == (
        "2026-08-20 19:53:19.7388258 +0000 UTC"
    )
    assert li.parse(_fixture("lock_error_s3.txt"))["operation"] == "OperationTypePlan"


def test_an_oversized_created_is_dropped_but_the_lock_survives():
    # apply.txt is capped at SIZE_BUDGET (60,000 chars), so a `local-exec` line
    # can carry a ~59,900-char Created value. The id is what the engine acts
    # on, so the lock must survive; the unbounded value must not.
    got = li.parse(_with("Created", "2" * 59_900))
    assert got["created"] == ""
    assert got["id"] == S3_ID


def test_a_created_carrying_markdown_emphasis_is_dropped():
    # _md_escape does not escape `*` or a backtick, and the renderer puts this
    # value inside a **bold** span in the bot's trusted voice.
    assert li.parse(_with("Created", "2026 **ship it** now"))["created"] == ""
    assert li.parse(_with("Created", "2026 `code` now"))["created"] == ""
    assert li.parse(_with("Created", "2026 **ship it** now"))["id"] == S3_ID


def test_an_out_of_charset_operation_is_dropped_but_the_lock_survives():
    got = li.parse(_with("Operation", "OperationType**Apply**"))
    assert got["operation"] == ""
    assert got["id"] == S3_ID


def test_parses_real_ansi_coloured_ci_output():
    """OpenTofu colours its diagnostics on a runner, and the parser must cope.

    This fixture is the verbatim probe output of a real `shipmate unlock sbx`
    run against the S3 backend, escape sequences intact. Before the ANSI strip,
    `parse` returned None on it -- the acquisition line still matched as a
    substring, but `Lock Info:` never compared equal because `ESC[31m|ESC[0m`
    sits in front of it. The cell then reported the lock state as
    undetermined and went red while a lock it could have released was sitting
    right there in its own output.

    Mutation: drop `_ANSI_RE.sub` from `_strip` and this reddens while the two
    colour-free captures stay green.
    """
    got = li.parse(_fixture("lock_error_s3_ansi.txt"))
    assert got == {
        "id": "2c655069-36ee-1bd8-0847-d0467ebe8307",
        "path": (
            "repo-examples-shipmate-state/repo-example-stacks-aws/sbx/eu-west-1"
            "/sandbox/box/terraform.tfstate"
        ),
        "operation": "OperationTypePlan",
        "created": "2026-08-22 14:19:20.313637 +0000 UTC",
    }

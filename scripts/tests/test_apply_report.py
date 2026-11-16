"""apply-detect's `report` output as `apply-comment` reads it from `SHIPMATE_REPORT`."""

import io
import json
import pathlib

import pytest
from _loader import load_script

ac = load_script("apply-comment")

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "apply-report"
SHA = "0123456789abcdef0123456789abcdef01234567"


def _waits(stack, env, after_stack, after_env):
    return {
        "stack": stack,
        "environment": env,
        "after_stack": after_stack,
        "after_environment": after_env,
    }


def _locked(stack, env, holder):
    return {"stack": stack, "environment": env, "cause": "locked", "holder": holder}


def _overlap(stack, env):
    return {"stack": stack, "environment": env, "cause": "overlap"}


#: Each scenario: (render step env beyond the defaults, the cells applied as (stack, env), the
#: report). Together they set every report field to a non-empty value at least once.
SCENARIOS = {
    "bare-every-kind": (
        {},
        [("stacks/app", "qa"), ("stacks/db", "lck")],
        {
            "excluded_envs": ["tgt", "both"],
            "skipped_envs": ["s1", "s3", "s5"],
            "skipped_after": {"s1": ["tgt"], "s3": ["lck", "dev"], "s5": ["ovl", "wt"]},
            "review_held_envs": ["rev", "both"],
            "applied_exempt_envs": ["qa"],
            "review_not_required_envs": ["lck"],
            "waiting_cells": [_waits("stacks/w", "wt", "stacks/t", "tgt")],
            "held_cells": [_locked("stacks/app", "lck", 7), _overlap("stacks/o", "ovl")],
            "env_order": ["qa", "lck", "tgt", "both", "rev", "wt", "ovl", "s1", "s3", "s5"],
        },
    ),
    "auto": (
        {"SHIPMATE_AUTO": "true"},
        [("stacks/app", "dev-eu")],
        {
            "excluded_envs": [],
            "skipped_envs": ["prod"],
            "skipped_after": {"prod": ["dev", "dev-eu"]},
            "review_held_envs": [],
            "applied_exempt_envs": [],
            "review_not_required_envs": [],
            "waiting_cells": [],
            "held_cells": [_overlap("stacks/db", "dev-eu")],
            "env_order": ["dev-eu", "dev", "prod"],
            "pusher": "k3np",
            "bare_envs": ["dev"],
        },
    ),
    "targeted": (
        {"SHIPMATE_ENVIRONMENT": "dev-eu"},
        [("stacks/dns", "dev-eu")],
        {
            "review_not_required_envs": ["dev-eu"],
            "waiting_cells": [_waits("stacks/app", "dev-eu", "stacks/db", "dev-us")],
            "held_cells": [_locked("stacks/db2", "dev-eu", 12)],
            "env_order": ["dev-eu"],
        },
    ),
    "detect-refused": (
        {
            "SHIPMATE_DETECT_REFUSAL": "apply refused: no apply check on the head",
            "SHIPMATE_RESULTS": "failure",
        },
        [],
        {},
    ),
}


def _render(monkeypatch, tmp_path, name, report_env):
    """`apply-comment.main`'s (comment, stdout) for scenario `name`, the report fields given by
    `report_env`, a dict of render step env vars."""
    extra, applied, _ = SCENARIOS[name]
    cells = tmp_path / "cells"
    cells.mkdir()
    for stack, env in applied:
        d = cells / f"apply-summary.{env}.{stack.replace('/', '-')}"
        d.mkdir()
        cell = {
            "stack": stack,
            "environment": env,
            "result": "applied",
            "reason": "",
            "state_lost": False,
        }
        (d / "cell.json").write_text(json.dumps(cell), encoding="utf-8")
    checks = tmp_path / "checks.jsonl"
    checks.write_text("", encoding="utf-8")
    waves = {"wave0": [{"stack": s, "environment": e} for s, e in applied]}
    env = {
        "CELLS": str(cells),
        "SHIPMATE_ENVIRONMENT": "",
        "SHIPMATE_ENVLEVEL0_WAVES": json.dumps(waves),
        "SHIPMATE_RESULTS": "success",
        "SHIPMATE_CHECKS": str(checks),
        "SHIPMATE_APP_ID": "12345",
        "SHIPMATE_HEAD_SHA": SHA,
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "acme/repo",
        "GITHUB_RUN_ID": "42",
        "GITHUB_RUN_NUMBER": "7",
        **extra,
        **report_env,
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    ac.main()
    return (tmp_path / "comment.md").read_text(encoding="utf-8"), out.getvalue()


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_report_renders_the_comment_the_eleven_inputs_rendered(monkeypatch, tmp_path, name):
    """`fixtures/apply-report/<name>.md` and `.out` are the comment and stdout `apply-comment.main`
    rendered from the same fields as eleven separate env vars, captured raw at engine commit
    `27dab46f`, before they became one `report`; the report renders them byte for byte.

    Mutation: default `skipped_after` to `[]` in `_REPORT_DEFAULTS` -- every scenario that omits
    it fails the type check, red. Mutation: drop `pusher` from `main`'s `build_comment` call --
    `auto`, red."""
    report = SCENARIOS[name][2]
    body, out = _render(
        monkeypatch, tmp_path, name, {"SHIPMATE_REPORT": json.dumps(report) if report else ""}
    )
    assert body == (FIXTURES / f"{name}.md").read_text(encoding="utf-8")
    assert out == (FIXTURES / f"{name}.out").read_text(encoding="utf-8")


ad = load_script("apply-detect")

#: Every report field at the value a missing one reads as, hand-written.
_DEFAULTS = {
    "excluded_envs": [],
    "skipped_envs": [],
    "skipped_after": {},
    "review_held_envs": [],
    "applied_exempt_envs": [],
    "review_not_required_envs": [],
    "waiting_cells": [],
    "held_cells": [],
    "env_order": [],
    "pusher": "",
    "bare_envs": [],
}


def test_an_empty_report_reads_as_every_field_empty():
    """Detect refused or never ran, so the render step passes an empty `report`.

    Mutation: `json.loads(raw)` without the `if raw` guard -- JSONDecodeError, red."""
    assert ac.read_report("") == (_DEFAULTS, {})


def test_a_missing_field_reads_as_its_default():
    """The targeted form writes four fields; the other seven read empty.

    Mutation: `report[key]` for `report.get(key, default)` in `read_report` -- KeyError, red."""
    fields, omitted = ac.read_report('{"held_cells": [{"stack": "a", "environment": "e"}]}')
    assert fields == {**_DEFAULTS, "held_cells": [{"stack": "a", "environment": "e"}]}
    assert omitted == {}


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ("[]", "::error::the apply-detect report is not a JSON object"),
        ('{"held_cells": {}}', "::error::the apply-detect report's held_cells is not a JSON list"),
        ('{"pusher": null}', "::error::the apply-detect report's pusher is not a JSON str"),
        (
            '{"waiting_cells_more": "3"}',
            "::error::the apply-detect report holds a count that is not an integer",
        ),
    ],
)
def test_a_malformed_report_fails_the_render(raw, error):
    """Mutation: drop the type check from `read_report`'s field loop -- `held_cells` and `pusher`
    pass, red. Mutation: drop the `isinstance(report, dict)` check -- `[]` raises AttributeError,
    not this SystemExit, red. Mutation: drop the integer check on the counts -- `"3"` passes,
    red."""
    with pytest.raises(SystemExit) as exc:
        ac.read_report(raw)
    assert str(exc.value) == error


def _worst_case():
    """The largest report one run can write: 256 waiting and 256 held cells with 100-character
    stack paths and 30-character environment names, four environments in every list."""
    envs = [f"e{i}".ljust(30, "x") for i in range(4)]

    def stack(i):
        return f"s{i:03d}".ljust(100, "p")

    return {
        "excluded_envs": envs,
        "skipped_envs": envs,
        "skipped_after": {e: envs for e in envs},
        "review_held_envs": envs,
        "applied_exempt_envs": envs,
        "review_not_required_envs": envs,
        "waiting_cells": [
            {
                "stack": stack(i),
                "environment": envs[i % 4],
                "after_stack": stack(255 - i),
                "after_environment": envs[(i + 1) % 4],
            }
            for i in range(256)
        ],
        "held_cells": [
            {"stack": stack(i), "environment": envs[i % 4], "holder": 999999, "cause": "locked"}
            for i in range(256)
        ],
        "env_order": envs,
        "pusher": "x" * 39,
        "bare_envs": envs,
    }


def test_the_worst_case_report_is_trimmed_within_its_budget():
    """Untrimmed, the worst case serializes to 139,875 bytes, past the 128 KiB env string cap.
    Trimmed, it fits 96 KiB: the waiting cells lose 124 entries from the end (338 bytes each; 123
    leave 98,328 bytes, 24 over), the held cells none, and the count names them.

    Mutation: drop the trim loop from `report_json` -- 139,875 bytes, red."""
    report = _worst_case()
    assert len(json.dumps(report)) == 139_875
    text = ad.report_json(report)
    assert len(text) <= ad.REPORT_BUDGET == 96 * 1024
    trimmed = json.loads(text)
    assert trimmed == {
        **report,
        "waiting_cells": report["waiting_cells"][:132],
        "waiting_cells_more": 124,
    }


def test_a_report_within_budget_is_written_whole():
    """Mutation: write `waiting_cells_more` as 0 on every report -- red."""
    report = SCENARIOS["auto"][2]
    assert ad.report_json(report) == json.dumps(report)


def test_trimmed_lists_render_how_many_entries_detect_left_out(monkeypatch, tmp_path):
    """Mutation: drop `more` from the footer parts in `build_comment` -- no count lines, red."""
    report = {
        **SCENARIOS["targeted"][2],
        "waiting_cells_more": 126,
        "held_cells_more": 2,
        "env_order_more": 1,
    }
    body, _ = _render(monkeypatch, tmp_path, "targeted", {"SHIPMATE_REPORT": json.dumps(report)})
    expected = (
        (FIXTURES / "targeted.md")
        .read_text(encoding="utf-8")
        .replace(
            "\n\nComment `shipmate help`",
            "\n⚪ and 126 more waiting cell(s); see the run"
            "\n⚪ and 2 more held cell(s); see the run"
            "\n⚪ and 1 more environment name(s); see the run"
            "\n\nComment `shipmate help`",
        )
    )
    assert body == expected


def test_the_held_cells_are_trimmed_once_the_waiting_cells_are_gone():
    """The worst case with every held cell both locked and overlapping (512 held entries)
    serializes to 186,979 bytes. Dropping all 256 waiting cells leaves it over 96 KiB, so the held
    cells lose 12 from the end too, and each list names its own count.

    Mutation: trim only `waiting_cells` in `report_json` (`for key in _TRIM_FIRST[:1]`) -- over
    budget, red."""
    report = _worst_case()
    locked = report["held_cells"]
    overlap = [{**c, "cause": "overlap"} for c in locked]
    for c in overlap:
        del c["holder"]
    report["held_cells"] = locked + overlap
    assert len(json.dumps(report)) == 186_979
    text = ad.report_json(report)
    assert len(text) <= ad.REPORT_BUDGET
    assert json.loads(text) == {
        **report,
        "waiting_cells": [],
        "held_cells": report["held_cells"][:500],
        "waiting_cells_more": 256,
        "held_cells_more": 12,
    }


def test_the_compact_form_also_ends_with_the_trimmed_counts(monkeypatch):
    """256 blocked rows with long reasons overflow HARD_CAP, so the comment falls back to the
    compact form, which keeps the trimmed-count line.

    Mutation: drop `+ more` from the compact form's footer parts in `build_comment` -- red."""
    for k, v in {
        "GITHUB_SERVER_URL": "https://gh",
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_RUN_ID": "1",
        "GITHUB_RUN_NUMBER": "7",
    }.items():
        monkeypatch.setenv(k, v)
    rows = [
        {
            "environment": "production-eu-west-1",
            "stack": f"stacks/platform/shared-services/eu-west-1/backend/service-{i:03}",
            "status": "blocked",
            "reason": "r" * 400,
            "state_lost": False,
            "apply_text": None,
        }
        for i in range(256)
    ]
    body = ac.build_comment(
        rows,
        [],
        "https://gh/run/1",
        [],
        [],
        "production-eu-west-1",
        "failure",
        head_sha=SHA,
        omitted={"waiting_cells": 3},
    )
    assert len(body) <= ac.sc.HARD_CAP
    assert body.split("\n")[-6:] == [
        "",
        "blocked: each blocked cell's reason is in its logs.",
        "",
        "⚪ and 3 more waiting cell(s); see the run",
        "",
        "Comment `shipmate help` for the available commands.",
    ]

"""apply-detect's `report` output as `apply-comment` reads it from `SHIPMATE_REPORT`."""

import io
import json
import pathlib

import pytest
from _loader import load_script

ac = load_script("apply-comment")
ad = load_script("apply-detect")

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


def _render(monkeypatch, tmp_path, name, report_env, report=None):
    """`apply-comment.main`'s (comment, stdout) for scenario `name`: `report_env` holds render step
    env vars, and `report`, when not None, is the text of the downloaded `report.json`."""
    extra, applied, _ = SCENARIOS[name]
    path = tmp_path / "apply-report" / "report.json"
    if report is not None:
        path.parent.mkdir()
        path.write_text(report, encoding="utf-8")
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
        "SHIPMATE_REPORT_PATH": str(path),
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
    `27dab46f`, before they became one report file; the file renders them byte for byte. A refused
    detect writes `{}`, which renders as every field empty.

    Mutation: default `skipped_after` to `[]` in `_REPORT_DEFAULTS` -- every scenario that omits
    it reads as unread, red. Mutation: drop `pusher` from `main`'s `build_comment` call --
    `auto`, red."""
    report = json.dumps(SCENARIOS[name][2])
    body, out = _render(monkeypatch, tmp_path, name, {}, report)
    assert body == (FIXTURES / f"{name}.md").read_text(encoding="utf-8")
    assert out == (FIXTURES / f"{name}.out").read_text(encoding="utf-8")


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


def test_a_missing_field_reads_as_its_default(tmp_path):
    """The targeted form writes four fields; the other seven read empty.

    Mutation: `report[k]` for `report.get(k, d)` in `read_report` -- KeyError, red."""
    path = tmp_path / "report.json"
    path.write_text('{"held_cells": [{"stack": "a", "environment": "e"}]}', encoding="utf-8")
    fields, unread = ac.read_report(str(path))
    assert fields == {**_DEFAULTS, "held_cells": [{"stack": "a", "environment": "e"}]}
    assert unread is False


#: The red line an unread report adds under the verdict, hand-written.
_UNREAD = "🔴 could not read the detect report; see the run"
#: The `detect-refused` scenario's env with the refusal taken back: a run whose jobs succeeded.
_NO_REFUSAL = {"SHIPMATE_RESULTS": "success", "SHIPMATE_DETECT_REFUSAL": ""}
_AT = (
    "at [0123456](https://github.com/acme/repo/commit/0123456789abcdef0123456789abcdef01234567) "
    "in [run #7](https://github.com/acme/repo/actions/runs/42)"
)


@pytest.mark.parametrize(
    "report",
    [
        pytest.param(None, id="missing"),
        pytest.param("", id="empty"),
        pytest.param("{", id="not-json"),
        pytest.param("[]", id="not-an-object"),
        pytest.param('{"held_cells": {}}', id="held-cells-not-a-list"),
        pytest.param('{"pusher": null}', id="pusher-not-a-string"),
        pytest.param('{"env_order": ["dev", 1]}', id="env-order-not-names"),
    ],
)
def test_an_unread_report_renders_the_red_line_and_never_green(monkeypatch, tmp_path, report):
    """Detect never uploaded the report (it died before writing it, or the upload or download
    failed), or the file is not the report's shape: every field reads empty, which alone would
    render `🟢 no pending applies` over pending cells, so the verdict is 🔴 and the red line names
    the cause. The render does not fail.

    Mutation: return the defaults without the unread flag when the file is missing -- `missing`
    renders green, red. Mutation: drop the `except` around `json.loads` -- `empty` and `not-json`
    raise, red. Mutation: drop the type check -- the wrong-typed cases render green or raise, red.
    Mutation: drop the env order's name check -- `env-order-not-names` renders green, red.
    Mutation: leave `report_unread` out of `failed` in `build_comment` -- 🟢 verdict, red."""
    body, out = _render(monkeypatch, tmp_path, "detect-refused", _NO_REFUSAL, report)
    assert body == (
        f"### shipmate apply\n\n🔴 failed {_AT}\n\n{_UNREAD}\n\n"
        "Comment `shipmate help` for the available commands."
    )
    assert out.startswith("::warning::could not read the detect report (")


def test_a_wide_environment_graph_renders_pending_not_green(monkeypatch, tmp_path):
    """54 targeted environments left pending and 54 skipped after them, each waiting on all 54:
    the report holds every one (156,131 bytes, past the 128 KiB one env string can carry), and
    the comment names them pending instead of reading green.

    Mutation: write the report through `json.dumps(report)[:131072]` in `apply-detect`'s
    `write_report` -- the file is cut to 131,072 bytes, red."""
    targeted = [f"targeted-environment-{i:03}-eu-west-1-production" for i in range(54)]
    skipped = [f"skipped-environment-{i:03}-eu-west-1-production" for i in range(54)]
    report = {
        "excluded_envs": targeted,
        "skipped_envs": skipped,
        "skipped_after": {e: targeted for e in skipped},
        "env_order": targeted + skipped,
    }
    path = tmp_path / "written.json"
    monkeypatch.setenv("SHIPMATE_REPORT_PATH", str(path))
    ad.write_report(report)
    text = path.read_text(encoding="utf-8")
    assert len(text) == 156_131
    body, _ = _render(monkeypatch, tmp_path, "detect-refused", _NO_REFUSAL, text)
    lines = body.split("\n")
    assert lines[2] == (
        f"⚪ nothing applied, the pending environments wait on the lines below {_AT}"
    )
    assert _UNREAD not in body
    # Past HARD_CAP, so the compact form groups each disposition on one line.
    for_each = "comment `shipmate apply <env>` for each"
    waits = "; ".join(f"{e} (apply it first with `shipmate apply {e}`)" for e in targeted)
    assert lines[4:] == [
        f"🟡 left pending (targeted): {', '.join(targeted)}; {for_each}",
        f"⚪ skipped, waits on {waits}: {', '.join(skipped)}",
        "",
        "Comment `shipmate help` for the available commands.",
    ]

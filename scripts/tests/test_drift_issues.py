"""Unit tests for scripts/drift-issues."""

import json

import pytest
from _loader import load_script

di = load_script("drift-issues")


def _cell(**over):
    base = {
        "stack": "stacks/app",
        "stack_name": "app",
        "environment": "dev-eu",
        "plan_ok": True,
        "drifted": False,
        "add": 0,
        "change": 0,
        "destroy": 0,
    }
    base.update(over)
    return base


def _write_cell(cells_dir, name, cell):
    d = cells_dir / f"drift-summary.dev-eu.{name}"
    d.mkdir(parents=True)
    (d / "cell.json").write_text(json.dumps(cell), encoding="utf-8")


# ---- load_cells ----------------------------------------------------------


def test_load_cells_reads_every_downloaded_cell_sorted(tmp_path):
    _write_cell(tmp_path, "b-stack", _cell(stack="stacks/b"))
    _write_cell(tmp_path, "a-stack", _cell(stack="stacks/a"))
    cells = di.load_cells(str(tmp_path))
    assert [c["stack"] for c in cells] == ["stacks/a", "stacks/b"]


def test_load_cells_missing_key_fails_loud(tmp_path):
    d = tmp_path / "drift-summary.dev-eu.app"
    d.mkdir(parents=True)
    (d / "cell.json").write_text(json.dumps({"stack": "stacks/app"}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        di.load_cells(str(tmp_path))
    assert "missing keys" in str(exc.value)


def test_load_cells_on_missing_directory_is_empty(tmp_path):
    assert di.load_cells(str(tmp_path / "does-not-exist")) == []


# ---- title / body ----------------------------------------------------------


def test_title_and_body_carry_the_counts_and_run_link():
    cell = _cell(drifted=True, add=1, change=2, destroy=3)
    assert di._title(cell) == "drift: dev-eu / app"
    body = di._body(cell, "https://example.invalid/run/1")
    assert "+1 ~2 -3" in body
    assert "https://example.invalid/run/1" in body
    assert "`app`" in body and "`dev-eu`" in body


# ---- upsert_or_close --------------------------------------------------------


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        return ""


def test_plan_not_ok_cell_is_skipped_entirely(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(di, "_run", rec)
    cell = _cell(plan_ok=False, drifted=True)
    result = di.upsert_or_close(cell, {"drift: dev-eu / app": 7}, "url", [False])
    assert result is False
    assert rec.calls == []  # an existing open issue is left completely untouched


def test_drifted_with_no_existing_issue_creates_one(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(di, "_run", rec)
    label_calls = []
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: label_calls.append(a) or type("R", (), {"returncode": 0})(),
    )
    cell = _cell(drifted=True, add=1)
    result = di.upsert_or_close(cell, {}, "url", [False])
    assert result is True
    assert len(rec.calls) == 1
    assert rec.calls[0][:3] == ["gh", "issue", "create"]
    assert label_calls, "expected the label to be (best-effort) created"


def test_drifted_with_existing_issue_edits_it(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(di, "_run", rec)
    cell = _cell(drifted=True)
    result = di.upsert_or_close(cell, {"drift: dev-eu / app": 42}, "url", [True])
    assert result is True
    assert rec.calls == [["gh", "issue", "edit", "42", "--body", di._body(cell, "url")]]


def test_clean_with_existing_issue_closes_it(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(di, "_run", rec)
    cell = _cell(drifted=False)
    result = di.upsert_or_close(cell, {"drift: dev-eu / app": 42}, "url", [True])
    assert result is False
    assert rec.calls[0][:3] == ["gh", "issue", "close"]


def test_clean_with_no_existing_issue_touches_nothing(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(di, "_run", rec)
    cell = _cell(drifted=False)
    result = di.upsert_or_close(cell, {}, "url", [True])
    assert result is False
    assert rec.calls == []


# ---- existing_issue_numbers --------------------------------------------------


def test_existing_issue_numbers_parses_the_listing(monkeypatch):
    monkeypatch.setattr(
        di,
        "_run",
        lambda args: json.dumps([{"number": 5, "title": "drift: dev-eu / app"}]),
    )
    assert di.existing_issue_numbers() == {"drift: dev-eu / app": 5}


def test_existing_issue_numbers_keeps_the_lowest_on_a_title_collision(monkeypatch):
    # gh does not guarantee listing order; a duplicate-titled issue must not
    # be resolved by whichever happened to come first/last in that order --
    # the lower number wins regardless of listing order, deterministically.
    rows = [
        {"number": 9, "title": "drift: dev-eu / app"},
        {"number": 3, "title": "drift: dev-eu / app"},
        {"number": 7, "title": "drift: dev-eu / app"},
    ]
    monkeypatch.setattr(di, "_run", lambda args: json.dumps(rows))
    assert di.existing_issue_numbers() == {"drift: dev-eu / app": 3}

    monkeypatch.setattr(di, "_run", lambda args: json.dumps(list(reversed(rows))))
    assert di.existing_issue_numbers() == {"drift: dev-eu / app": 3}


# ---- main / Slack -----------------------------------------------------------


def test_main_skips_slack_for_a_plan_not_ok_cell(tmp_path, monkeypatch):
    _write_cell(tmp_path, "app", _cell(plan_ok=False, drifted=True))
    monkeypatch.setenv("SHIPMATE_CELLS_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://example.invalid")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/demo")
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    monkeypatch.setenv("SHIPMATE_SLACK_WEBHOOK", "https://hooks.example.invalid/x")

    def fake_run(args):
        if args[:3] == ["gh", "issue", "list"]:
            return "[]"
        pytest.fail(f"gh mutated an issue for a blocked cell: {args}")
        return ""

    monkeypatch.setattr(di, "_run", fake_run)
    monkeypatch.setattr(
        di, "notify_slack", lambda webhook, cell: pytest.fail("slack hit for a blocked cell")
    )
    di.main()  # must not raise, and must not touch any issue


def test_main_notifies_slack_once_for_a_newly_drifted_cell(tmp_path, monkeypatch):
    _write_cell(tmp_path, "app", _cell(drifted=True))
    monkeypatch.setenv("SHIPMATE_CELLS_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://example.invalid")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/demo")
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    monkeypatch.setenv("SHIPMATE_SLACK_WEBHOOK", "https://hooks.example.invalid/x")

    def fake_run(args):
        if args[:3] == ["gh", "issue", "list"]:
            return "[]"
        return ""

    monkeypatch.setattr(di, "_run", fake_run)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: type("R", (), {"returncode": 0})())
    notified = []
    monkeypatch.setattr(di, "notify_slack", lambda webhook, cell: notified.append(cell["stack"]))
    di.main()
    assert notified == ["stacks/app"]


def test_main_with_no_cells_never_lists_issues(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIPMATE_CELLS_DIR", str(tmp_path))
    monkeypatch.setattr(di, "_run", lambda args: pytest.fail(f"unexpected gh call: {args}"))
    di.main()  # must return early, no env/gh access needed at all


def test_slack_failure_warns_but_does_not_raise(tmp_path, monkeypatch, capsys):
    _write_cell(tmp_path, "app", _cell(drifted=True))
    monkeypatch.setenv("SHIPMATE_CELLS_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://example.invalid")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/demo")
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    monkeypatch.setenv("SHIPMATE_SLACK_WEBHOOK", "https://hooks.example.invalid/x")

    def fake_run(args):
        if args[:3] == ["gh", "issue", "list"]:
            return "[]"
        return ""

    monkeypatch.setattr(di, "_run", fake_run)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: type("R", (), {"returncode": 0})())

    def boom(webhook, cell):
        raise di.urllib.error.URLError("nope")

    monkeypatch.setattr(di, "notify_slack", boom)
    di.main()
    assert "slack notify failed" in capsys.readouterr().out


def test_no_webhook_never_calls_notify_slack(tmp_path, monkeypatch):
    _write_cell(tmp_path, "app", _cell(drifted=True))
    monkeypatch.setenv("SHIPMATE_CELLS_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://example.invalid")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/demo")
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    monkeypatch.delenv("SHIPMATE_SLACK_WEBHOOK", raising=False)

    def fake_run(args):
        if args[:3] == ["gh", "issue", "list"]:
            return "[]"
        return ""

    monkeypatch.setattr(di, "_run", fake_run)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: type("R", (), {"returncode": 0})())
    monkeypatch.setattr(
        di, "notify_slack", lambda webhook, cell: pytest.fail("must not notify with no webhook")
    )
    di.main()

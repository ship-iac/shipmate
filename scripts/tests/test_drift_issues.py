"""Unit tests for scripts/drift-issues."""

import json

import pytest
from _loader import action_steps, load_script

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


def test_title_and_body_carry_the_counts_and_run_link():
    cell = _cell(drifted=True, add=1, change=2, destroy=3)
    assert di._title(cell) == "drift: dev-eu / app"
    body = di._body(cell, "https://example.invalid/run/1")
    assert "+1 ~2 -3" in body
    assert "https://example.invalid/run/1" in body
    assert "`app`" in body and "`dev-eu`" in body


def test_body_is_exactly_the_expected_text():
    """The whole Issue body, including an auto-close promise a scoped sweep keeps."""
    cell = _cell(drifted=True, add=1, change=2, destroy=3)
    assert di._body(cell, "https://example.invalid/run/1") == (
        "Drift detected in `app` @ `dev-eu`: +1 ~2 -3. "
        "[Drift run](https://example.invalid/run/1) "
        "-- plan output is in that run's log. "
        "Auto-closed on the next clean drift run that covers this stack and environment."
    )


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
    assert rec.calls == []  # An existing open issue is left untouched.


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


def test_existing_issue_numbers_parses_the_listing(monkeypatch):
    monkeypatch.setattr(
        di,
        "_run",
        lambda args: json.dumps([{"number": 5, "title": "drift: dev-eu / app"}]),
    )
    assert di.existing_issue_numbers() == {"drift: dev-eu / app": 5}


def test_existing_issue_numbers_keeps_the_lowest_on_a_title_collision(monkeypatch):
    # gh does not guarantee listing order, and a duplicate-titled issue must not be resolved
    # by whichever came first or last in it. The lower number wins deterministically, whatever
    # the listing order.
    rows = [
        {"number": 9, "title": "drift: dev-eu / app"},
        {"number": 3, "title": "drift: dev-eu / app"},
        {"number": 7, "title": "drift: dev-eu / app"},
    ]
    monkeypatch.setattr(di, "_run", lambda args: json.dumps(rows))
    assert di.existing_issue_numbers() == {"drift: dev-eu / app": 3}

    monkeypatch.setattr(di, "_run", lambda args: json.dumps(list(reversed(rows))))
    assert di.existing_issue_numbers() == {"drift: dev-eu / app": 3}


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
    di.main()  # It must not raise, and must not touch any issue.


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


def test_one_cells_failure_does_not_abandon_the_rest(tmp_path, monkeypatch, capsys):
    # A rate limit on one cell must not leave every later cell unprocessed: a stack that has
    # gone clean would keep an open Issue saying it drifts. The run still fails, naming every
    # cell that failed.
    for name in ("a", "b", "c"):
        _write_cell(tmp_path, name, _cell(stack=f"stacks/{name}", stack_name=name, drifted=True))
    monkeypatch.setenv("SHIPMATE_CELLS_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://example.invalid")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/demo")
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    monkeypatch.delenv("SHIPMATE_SLACK_WEBHOOK", raising=False)
    created = []

    def fake_run(args):
        if args[:3] == ["gh", "issue", "list"]:
            return "[]"
        title = args[args.index("--title") + 1]
        if title.endswith("/ b"):
            raise SystemExit("::error::command failed (1): gh issue create")
        created.append(title)
        return ""

    monkeypatch.setattr(di, "_run", fake_run)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: type("R", (), {"returncode": 0})())
    with pytest.raises(SystemExit) as exc:
        di.main()
    assert created == ["drift: dev-eu / a", "drift: dev-eu / c"]
    assert "dev-eu / b" in str(exc.value)
    assert "dev-eu / a" not in str(exc.value)


def test_main_with_no_cells_never_lists_issues(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIPMATE_CELLS_DIR", str(tmp_path))
    monkeypatch.setattr(di, "_run", lambda args: pytest.fail(f"unexpected gh call: {args}"))
    di.main()  # It returns early, with no env or gh access at all.


def test_slack_failure_fails_the_run_but_still_processes_later_cells(tmp_path, monkeypatch, capsys):
    """A revoked or rotated webhook URL must not leave every nightly run green while no drift
    notification reaches anyone. Slack failures therefore join the same collected `failed` list
    the `gh` failures use, naming the cell and exiting nonzero only after every remaining cell
    has been processed."""
    for name in ("a", "b"):
        _write_cell(tmp_path, name, _cell(stack=f"stacks/{name}", stack_name=name, drifted=True))
    monkeypatch.setenv("SHIPMATE_CELLS_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://example.invalid")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/demo")
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    monkeypatch.setenv("SHIPMATE_SLACK_WEBHOOK", "https://hooks.example.invalid/x")
    created = []

    def fake_run(args):
        if args[:3] == ["gh", "issue", "list"]:
            return "[]"
        created.append(args[args.index("--title") + 1])
        return ""

    monkeypatch.setattr(di, "_run", fake_run)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: type("R", (), {"returncode": 0})())

    def boom(webhook, cell):
        if cell["stack_name"] == "a":
            raise di.urllib.error.URLError("nope")

    monkeypatch.setattr(di, "notify_slack", boom)
    with pytest.raises(SystemExit) as exc:
        di.main()
    assert created == ["drift: dev-eu / a", "drift: dev-eu / b"]
    assert "dev-eu / a" in str(exc.value)
    assert "dev-eu / b" not in str(exc.value)
    assert "::error::slack notify failed" in capsys.readouterr().out


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


def test_action_names_the_repository_for_gh():
    """`gh issue list/create/edit/close` and `gh label` are repository-scoped and otherwise
    resolve their repository from a checkout's git remote. The job running this holds the App
    key and so has no checkout at all, so without GH_REPO every one of those calls fails with
    "failed to determine base repository" and `_run` raises, killing the first nightly drift
    run."""
    steps = action_steps("drift-issues")
    step = next(s for s in steps if "scripts/drift-issues" in str(s.get("run", "")))
    assert step.get("env", {}).get("GH_REPO") == "${{ github.repository }}", (
        f"drift-issues' script step must export GH_REPO, got {step.get('env')!r}"
    )

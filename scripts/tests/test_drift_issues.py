"""Unit tests for scripts/drift-issues."""

import json
import os

import pytest
from _loader import action_steps, action_yaml, load_script, step_by

di = load_script("drift-issues")


@pytest.fixture(autouse=True)
def _fresh_label_cache():
    """`_ensure_label` is cached per process; clear it so no test inherits another's call."""
    di._ensure_label.cache_clear()


def _cell(**over):
    base = {
        "stack": "stacks/app",
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
    """The whole refusal, so the pin-skew sentence the caller passes to `cell_summaries` is
    checked too. Mutations: drop `{skew}` from `cell_summaries`' message; drop `"stack"` from
    `CELL_KEYS`."""
    d = tmp_path / "drift-summary.dev-eu.app"
    d.mkdir(parents=True)
    (d / "cell.json").write_text(json.dumps({}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        di.load_cells(str(tmp_path))
    path = os.path.join(str(tmp_path), "drift-summary.dev-eu.app", "cell.json")
    assert str(exc.value) == (
        f"::error::cell summary {path} missing keys ['stack', 'environment', 'plan_ok', "
        "'drifted', 'add', 'change', 'destroy'] -- drift-cell and this script must be pinned "
        "at the same engine SHA"
    )


def test_load_cells_on_missing_directory_is_empty(tmp_path):
    assert di.load_cells(str(tmp_path / "does-not-exist")) == []


def test_title_and_body_carry_the_counts_and_run_link():
    """The title is the Issue's identity: an Issue opened under an earlier engine is found by it,
    so it names the whole stack path. Mutation: `_title` uses `cell['stack'].split('/')[-1]`."""
    cell = _cell(drifted=True, add=1, change=2, destroy=3)
    assert di._title(cell) == "drift: dev-eu / stacks/app"
    body = di._body(cell, "https://example.invalid/run/1")
    assert "+1 ~2 -3" in body
    assert "https://example.invalid/run/1" in body
    assert "`stacks/app`" in body and "`dev-eu`" in body


def test_body_is_exactly_the_expected_text():
    """The whole Issue body, including an auto-close promise a scoped sweep keeps: a cell
    that leaves a query stays in the tree, so only a clean plan or leaving the repository closes
    its Issue. Mutation: restore "leaves the drift sweep"."""
    cell = _cell(drifted=True, add=1, change=2, destroy=3)
    assert di._body(cell, "https://example.invalid/run/1") == (
        "Drift detected in `stacks/app` @ `dev-eu`: +1 ~2 -3. "
        "[Drift run](https://example.invalid/run/1) "
        "-- plan output is in that run's log. "
        "Auto-closed when a sweep finds it clean or the stack or environment leaves the "
        "repository."
    )


_LIST = [
    "gh",
    "issue",
    "list",
    "--label",
    "drift",
    "--state",
    "open",
    "--limit",
    "1000",
    "--json",
    "number,title",
]

_RUN_URL = "https://example.invalid/acme/demo/actions/runs/1"

_SHA = "a" * 40
_NEWER = "b" * 40


def _main_env(monkeypatch, cells_dir, tree):
    """`main()`'s environment: the artifact directory, the run, and every cell of the scanned
    tree as `(environment, stack)` pairs."""
    monkeypatch.setenv("SHIPMATE_CELLS_DIR", str(cells_dir))
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://example.invalid")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/demo")
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    cells = [{"environment": e, "stack": st} for e, st in tree]
    monkeypatch.setenv("SHIPMATE_TREE_CELLS", json.dumps(cells))
    monkeypatch.setenv("GITHUB_SHA", _SHA)
    monkeypatch.setenv("SHIPMATE_DEFAULT_BRANCH", "main")
    _head(monkeypatch, _SHA)


def _head(monkeypatch, head):
    """The default branch head `main()` reads: `head` for `main`, unreadable for any other."""
    monkeypatch.setattr(di, "default_branch_head", lambda b: head if b == "main" else None)


def _gh(monkeypatch, rows):
    """Patch `_run`: the listing answers `rows`, every other call is recorded and succeeds."""
    calls = []

    def fake_run(args):
        if args == _LIST:
            return json.dumps(rows)
        calls.append(args)
        return ""

    monkeypatch.setattr(di, "_run", fake_run)
    return calls


def _left(number, label):
    comment = (
        f"`{label}` is no longer a managed cell: [drift run]({_RUN_URL}) found no such stack "
        "and environment. The stack or environment left the repository, or the stack carries "
        "no `env/*` tag."
    )
    return ["gh", "issue", "close", str(number), "--comment", comment]


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
    di.upsert_or_close(cell, {"drift: dev-eu / stacks/app": 7}, "url")
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
    di.upsert_or_close(cell, {}, "url")
    assert len(rec.calls) == 1
    assert rec.calls[0][:3] == ["gh", "issue", "create"]
    assert label_calls, "expected the label to be (best-effort) created"


def test_two_new_issues_create_the_label_once(monkeypatch):
    """Mutation: `@functools.cache` -> `@functools.lru_cache(maxsize=0)` on `_ensure_label`."""
    monkeypatch.setattr(di, "_run", _Recorder())
    label_calls = []
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: label_calls.append(a) or type("R", (), {"returncode": 0})(),
    )
    di.upsert_or_close(_cell(drifted=True, stack="stacks/app"), {}, "url")
    di.upsert_or_close(_cell(drifted=True, stack="stacks/db"), {}, "url")
    assert len(label_calls) == 1


def test_drifted_with_existing_issue_edits_it(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(di, "_run", rec)
    cell = _cell(drifted=True)
    di.upsert_or_close(cell, {"drift: dev-eu / stacks/app": 42}, "url")
    assert rec.calls == [["gh", "issue", "edit", "42", "--body", di._body(cell, "url")]]


def test_clean_with_existing_issue_closes_it(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(di, "_run", rec)
    cell = _cell(drifted=False)
    di.upsert_or_close(cell, {"drift: dev-eu / stacks/app": 42}, "url")
    assert rec.calls[0][:3] == ["gh", "issue", "close"]


def test_clean_with_no_existing_issue_touches_nothing(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(di, "_run", rec)
    cell = _cell(drifted=False)
    di.upsert_or_close(cell, {}, "url")
    assert rec.calls == []


def test_existing_issue_numbers_parses_the_listing(monkeypatch):
    monkeypatch.setattr(
        di,
        "_run",
        lambda args: json.dumps([{"number": 5, "title": "drift: dev-eu / app"}]),
    )
    assert di.existing_issue_numbers(di.open_issues()) == {"drift: dev-eu / app": 5}


def test_existing_issue_numbers_keeps_the_lowest_on_a_title_collision(monkeypatch):
    # gh does not guarantee listing order, and a duplicate-titled issue must not be resolved
    # by whichever came first or last in it. The lower number wins deterministically, whatever
    # the listing order.
    rows = [
        {"number": 9, "title": "drift: dev-eu / app"},
        {"number": 3, "title": "drift: dev-eu / app"},
        {"number": 7, "title": "drift: dev-eu / app"},
    ]
    assert di.existing_issue_numbers(rows) == {"drift: dev-eu / app": 3}
    assert di.existing_issue_numbers(list(reversed(rows))) == {"drift: dev-eu / app": 3}


def test_one_cells_failure_does_not_abandon_the_rest(tmp_path, monkeypatch, capsys):
    """A rate limit on one cell must not leave every later cell unprocessed: a stack that has
    gone clean would keep an open Issue saying it drifts. The run still fails, naming every
    cell that failed, and annotates the failed cell. Mutation: delete the per-cell
    `::error::drift issue update failed` print in `main()`."""
    for name in ("a", "b", "c"):
        _write_cell(tmp_path, name, _cell(stack=f"stacks/{name}", drifted=True))
    _main_env(monkeypatch, tmp_path, [("dev-eu", f"stacks/{n}") for n in ("a", "b", "c")])
    created = []

    def fake_run(args):
        if args[:3] == ["gh", "issue", "list"]:
            return "[]"
        title = args[args.index("--title") + 1]
        if title.endswith("/ stacks/b"):
            raise SystemExit("::error::command failed (1): gh issue create")
        created.append(title)
        return ""

    monkeypatch.setattr(di, "_run", fake_run)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: type("R", (), {"returncode": 0})())
    with pytest.raises(SystemExit) as exc:
        di.main()
    assert created == ["drift: dev-eu / stacks/a", "drift: dev-eu / stacks/c"]
    assert "dev-eu / stacks/b" in str(exc.value)
    assert "dev-eu / stacks/a" not in str(exc.value)
    assert capsys.readouterr().out == (
        "loaded 3 drift cell summaries\n"
        "::error::drift issue update failed for dev-eu / stacks/b: "
        "::error::command failed (1): gh issue create\n"
    )


def test_main_leaves_a_plan_not_ok_cells_open_issue_untouched(tmp_path, monkeypatch):
    """A failed plan says nothing about drift, so its open Issue is neither edited nor closed,
    and the cell is still in the tree, so it is no removed cell either. Mutations: in
    `upsert_or_close`, close the open Issue for a `plan_ok` false cell; build `present` in
    `main()` from the loaded `plan_ok` cells instead of the tree."""
    _write_cell(tmp_path, "app", _cell(plan_ok=False, drifted=False))
    _main_env(monkeypatch, tmp_path, [("dev-eu", "stacks/app")])
    calls = []

    def fake_run(args):
        calls.append(args)
        return json.dumps([{"number": 7, "title": "drift: dev-eu / stacks/app"}])

    monkeypatch.setattr(di, "_run", fake_run)
    di.main()
    assert calls == [_LIST]


def test_main_closes_only_the_issues_of_cells_that_left_the_repository(tmp_path, monkeypatch):
    """An Issue whose title names no cell of the tree closes with the whole comment. A tree
    cell's Issue, and a `drift`-labelled Issue a human titled freely, stay open. Mutations:
    skip the removed-cell close in `main()`; drop its `startswith(PREFIX)` check."""
    _write_cell(tmp_path, "app", _cell(drifted=True))
    _main_env(monkeypatch, tmp_path, [("dev-eu", "stacks/app")])
    calls = _gh(
        monkeypatch,
        [
            {"number": 4, "title": "drift: dev-eu / stacks/app"},
            {"number": 5, "title": "drift: dev-eu / stacks/old"},
            {"number": 6, "title": "Investigate prod drift"},
        ],
    )
    di.main()
    body = di._body(_cell(drifted=True), _RUN_URL)
    assert calls == [
        ["gh", "issue", "edit", "4", "--body", body],
        _left(5, "dev-eu / stacks/old"),
    ]


def test_main_closes_every_duplicate_titled_issue_of_a_removed_cell(tmp_path, monkeypatch):
    """Mutation: hand `close_removed` the entries of `existing` instead of the raw rows."""
    _write_cell(tmp_path, "app", _cell())
    _main_env(monkeypatch, tmp_path, [("dev-eu", "stacks/app")])
    rows = [
        {"number": 9, "title": "drift: prod-eu / stacks/app"},
        {"number": 3, "title": "drift: prod-eu / stacks/app"},
    ]
    calls = _gh(monkeypatch, rows)
    di.main()
    assert calls == [_left(9, "prod-eu / stacks/app"), _left(3, "prod-eu / stacks/app")]


def test_main_keeps_the_issue_of_a_tree_cell_whose_artifact_is_missing(tmp_path, monkeypatch):
    """A lost artifact, or a cell job that died before uploading, is not a removed cell.
    Mutation: build `present` in `main()` from the loaded cells instead of the tree."""
    _write_cell(tmp_path, "app", _cell())
    _main_env(monkeypatch, tmp_path, [("dev-eu", "stacks/app"), ("dev-eu", "stacks/db")])
    calls = _gh(monkeypatch, [{"number": 8, "title": "drift: dev-eu / stacks/db"}])
    di.main()
    assert calls == []


def test_a_filtered_sweep_leaves_another_sweeps_issue_open(tmp_path, monkeypatch):
    """A sweep whose query selects only `dev-eu` loads only that cell, while the tree also holds
    `dev-us`, which another drift file sweeps. That cell's Issue stays open; an Issue naming a
    cell in neither closes. Mutation: hand `close_removed` the titles of the loaded cells instead
    of `tree_titles()`."""
    _write_cell(tmp_path, "app", _cell())
    _main_env(monkeypatch, tmp_path, [("dev-eu", "stacks/app"), ("dev-us", "stacks/app")])
    calls = _gh(
        monkeypatch,
        [
            {"number": 4, "title": "drift: dev-us / stacks/app"},
            {"number": 5, "title": "drift: dev-eu / stacks/old"},
        ],
    )
    di.main()
    assert calls == [_left(5, "dev-eu / stacks/old")]


def test_main_with_no_loaded_cells_still_closes_a_removed_cells_issue(
    tmp_path, monkeypatch, capsys
):
    """The removed-cell close depends on the tree alone, and the count line still comes
    first. Mutation: restore `if not cells: return` above the removed-cell close."""
    _main_env(monkeypatch, tmp_path, [("dev-eu", "stacks/app")])
    calls = _gh(monkeypatch, [{"number": 5, "title": "drift: dev-eu / stacks/old"}])
    di.main()
    assert calls == [_left(5, "dev-eu / stacks/old")]
    assert capsys.readouterr().out == "loaded 0 drift cell summaries\n"


def test_a_stale_sweep_closes_no_removed_cells_issue(tmp_path, monkeypatch, capsys):
    """A re-run of an old sweep, or an older sweep finishing after a newer one, would close the
    Issue of a cell added since. Per-cell closes still run. Mutations: drop the freshness check;
    compare the head against itself instead of `GITHUB_SHA`."""
    _write_cell(tmp_path, "app", _cell())
    _main_env(monkeypatch, tmp_path, [("dev-eu", "stacks/app")])
    _head(monkeypatch, _NEWER)
    calls = _gh(
        monkeypatch,
        [
            {"number": 4, "title": "drift: dev-eu / stacks/app"},
            {"number": 5, "title": "drift: dev-eu / stacks/new"},
        ],
    )
    di.main()
    assert calls == [
        [
            "gh",
            "issue",
            "close",
            "4",
            "--comment",
            "Drift resolved -- clean plan for dev-eu / stacks/app.",
        ]
    ]
    assert capsys.readouterr().out == (
        "loaded 1 drift cell summaries\n"
        f"::notice::this sweep planned {_SHA} but the default branch is at {_NEWER}, so no "
        "Issue of a cell that is no longer managed was closed\n"
    )


def test_an_unread_head_closes_no_removed_cells_issue_and_fails_the_run(
    tmp_path, monkeypatch, capsys
):
    """An unknown head is not a fresh one. Mutation: treat a `None` head as `GITHUB_SHA`."""
    _main_env(monkeypatch, tmp_path, [("dev-eu", "stacks/app")])
    _head(monkeypatch, None)
    calls = _gh(monkeypatch, [{"number": 5, "title": "drift: dev-eu / stacks/old"}])
    with pytest.raises(SystemExit) as exc:
        di.main()
    assert calls == []
    assert str(exc.value) == "::error::drift reporting failed for: the default branch head"
    assert capsys.readouterr().out == (
        "loaded 0 drift cell summaries\n"
        "::error::could not read the default branch head, so no Issue of a cell that is no "
        "longer managed was closed\n"
    )


class _Proc:
    def __init__(self, returncode, stdout):
        self.returncode, self.stdout = returncode, stdout


def test_default_branch_head_reads_the_branch_with_the_workflow_token(monkeypatch):
    """The whole argv, and `GH_TOKEN` swapped for the workflow token: the App token cannot read
    contents on a private repository. Mutation: drop the `GH_TOKEN` override from `env`."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/demo")
    monkeypatch.setenv("GH_TOKEN", "app-token")
    monkeypatch.setenv("SHIPMATE_READ_TOKEN", "workflow-token")
    seen = []

    def fake(args, **kw):
        seen.append((args, kw["env"]["GH_TOKEN"]))
        return _Proc(0, _SHA + "\n")

    monkeypatch.setattr("subprocess.run", fake)
    assert di.default_branch_head("main") == _SHA
    assert seen == [
        (["gh", "api", "repos/acme/demo/branches/main", "--jq", ".commit.sha"], "workflow-token")
    ]


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [(1, _SHA + "\n"), (0, ""), (0, "null\n")],
    ids=["failed", "empty", "not-a-sha"],
)
def test_default_branch_head_is_none_when_unreadable(monkeypatch, returncode, stdout):
    """Mutations: ignore the exit code (reddens `failed`); return the stripped stdout whatever
    its shape (reddens `empty` and `not-a-sha`)."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/demo")
    monkeypatch.setattr("subprocess.run", lambda args, **kw: _Proc(returncode, stdout))
    assert di.default_branch_head("main") is None


def test_default_branch_head_is_none_for_an_empty_branch(monkeypatch):
    """Mutation: drop the empty-branch check, and the read asks for `branches/`."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/demo")
    monkeypatch.setattr("subprocess.run", lambda args, **kw: _Proc(0, _SHA))
    assert di.default_branch_head("") is None


def test_a_failing_removed_cell_close_is_collected_and_fails_the_run(tmp_path, monkeypatch, capsys):
    """The other closes still run, then the run exits nonzero naming the failed one.
    Mutation: let the `SystemExit` from the removed-cell close escape its `try`."""
    _main_env(monkeypatch, tmp_path, [("dev-eu", "stacks/app")])
    rows = [
        {"number": 5, "title": "drift: dev-eu / stacks/a"},
        {"number": 6, "title": "drift: dev-eu / stacks/b"},
    ]
    calls = []

    def fake_run(args):
        if args == _LIST:
            return json.dumps(rows)
        calls.append(args)
        if args[3] == "5":
            raise SystemExit("::error::command failed (1): gh issue close")
        return ""

    monkeypatch.setattr(di, "_run", fake_run)
    with pytest.raises(SystemExit) as exc:
        di.main()
    assert calls == [_left(5, "dev-eu / stacks/a"), _left(6, "dev-eu / stacks/b")]
    assert str(exc.value) == "::error::drift reporting failed for: dev-eu / stacks/a"
    assert capsys.readouterr().out == (
        "loaded 0 drift cell summaries\n"
        "::error::drift issue update failed for dev-eu / stacks/a: "
        "::error::command failed (1): gh issue close\n"
    )


@pytest.mark.parametrize("value", [None, "", "[]", "not json"], ids=["unset", "empty", "[]", "bad"])
def test_main_refuses_a_missing_or_empty_tree_before_any_gh_call(tmp_path, monkeypatch, value):
    """An empty `present` set would close every drift Issue. Mutations: in `tree_titles`,
    return the empty set for an unset, empty or `[]` input (reddens three cases), or for an
    unparseable one (reddens `unset`, `empty` and `bad`)."""
    _write_cell(tmp_path, "app", _cell(drifted=True))
    _main_env(monkeypatch, tmp_path, [])
    if value is None:
        monkeypatch.delenv("SHIPMATE_TREE_CELLS")
    else:
        monkeypatch.setenv("SHIPMATE_TREE_CELLS", value)
    monkeypatch.setattr(di, "_run", lambda args: pytest.fail(f"unexpected gh call: {args}"))
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail("unexpected gh call"))
    with pytest.raises(SystemExit) as exc:
        di.main()
    assert str(exc.value) == (
        "::error::drift-issues needs every cell of the scanned tree as a non-empty JSON list "
        f"in SHIPMATE_TREE_CELLS, got {value or ''!r}"
    )


def test_the_script_step_hands_the_script_exactly_these_names():
    """The whole `env:`, the whole `run:` and the `cells` input's `required`. `gh issue` and
    `gh label` resolve their repository from a checkout's git remote, and this job checks out
    nothing, so without GH_REPO every call fails. The tree cells travel through `env:`,
    never interpolated into `run:`. Mutations: drop GH_REPO; move the `SHIPMATE_TREE_CELLS`
    binding into `run:` as `SHIPMATE_TREE_CELLS='${{ inputs.cells }}' python3 ...`; set the
    `cells` input to `required: false`; bind `SHIPMATE_READ_TOKEN` to the App token."""
    steps = action_steps("drift-issues")
    step = next(s for s in steps if "scripts/drift-issues" in str(s.get("run", "")))
    assert step["env"] == {
        "GH_TOKEN": "${{ steps.token.outputs.token }}",
        "GH_REPO": "${{ github.repository }}",
        "SHIPMATE_TREE_CELLS": "${{ inputs.cells }}",
        "SHIPMATE_DEFAULT_BRANCH": "${{ inputs.default-branch }}",
        "SHIPMATE_READ_TOKEN": "${{ github.token }}",
    }
    assert step["run"] == (
        'set -euo pipefail\npython3 "$GITHUB_ACTION_PATH/../../scripts/drift-issues"\n'
    )
    inputs = action_yaml("drift-issues")["inputs"]
    assert inputs["cells"]["required"] is True
    assert inputs["default-branch"]["required"] is True


def test_the_download_fails_the_job_on_an_artifact_api_error():
    """The whole step: no `continue-on-error`, no `github-token`, and `path` is the script's
    default directory. The gate for the empty case is the calling job's `if:`; degrading the
    download too makes an artifact-API error read as no drift.

    Mutations: add `continue-on-error: true`; change `path`.
    """
    assert step_by("drift-issues", name="Download drift cell summaries") == {
        "name": "Download drift cell summaries",
        "uses": "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
        "with": {"pattern": "drift-summary.*", "path": "drift"},
    }

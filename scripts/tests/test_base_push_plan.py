"""`scripts/base-push-plan` selects the open pull requests a push to the default branch may have
put behind, and `actions/base-push-plan` dispatches `shipmate plan` for each. The notify is
display-only: it can neither fail the deploy nor dispatch a plan into a pull request with a live
App apply run."""

import json
import os

import _shipmate
import pytest
from _detect_fixtures import APP_ID, _apply_check, behind_check, check_run
from _loader import (
    ACTIONS,
    bash_only,
    load_script,
    run_step,
    step_by,
    write_stub,
)

bpp = load_script("base-push-plan")

SLUG = "shipmate-app"
#: Hand-written whole reads.
PULLS = [
    "gh",
    "api",
    "--paginate",
    "repos/acme/demo/pulls?state=open&base=main&per_page=100",
    "--jq",
    ".[] | {number, draft, sha: .head.sha} | tojson",
]
STATUSES = ("requested", "pending", "waiting", "queued", "in_progress")
LIVE_LISTS = [
    [
        "gh",
        "api",
        (
            "repos/acme/demo/actions/workflows/shipmate.yml/runs"
            f"?event=workflow_dispatch&status={status}&per_page=100&page=1"
        ),
    ]
    for status in STATUSES
]


def _sha(n):
    return f"{n:040x}"


def _checks_read(n):
    return [
        "gh",
        "api",
        "--paginate",
        f"repos/acme/demo/commits/{_sha(n)}/check-runs?filter=all&per_page=100",
        "--jq",
        ".check_runs[]",
    ]


def _pull(n, draft=False):
    return {"number": n, "draft": draft, "sha": _sha(n)}


def _run_row(rid, title, actor=f"{SLUG}[bot]"):
    return {
        "id": rid,
        "display_title": title,
        "event": "workflow_dispatch",
        "status": "queued",
        "actor": {"login": actor},
    }


def _apply_run(rid, pr, verb="apply", actor=f"{SLUG}[bot]"):
    return _run_row(rid, f"shipmate · {verb} pr={pr} ref={'b' * 40} comment=", actor)


def _select(
    monkeypatch,
    capsys,
    *,
    pulls,
    checks,
    push='{"stacks": ["stacks/app"], "outside": false}',
    live=(),
    failing=(),
    slug=SLUG,
):
    """`main()` over `pulls`, each head's `checks` (`{number: [check run]}`) and the live runs
    `live`, queued. A read in `failing` raises gh's failure. Returns `(printed numbers, stderr,
    reads)`."""
    for name, value in {
        "GITHUB_REPOSITORY": "acme/demo",
        "SHIPMATE_APP_ID": APP_ID,
        "SHIPMATE_APP_SLUG": slug,
        "SHIPMATE_PUSH": push,
        "SHIPMATE_DEFAULT_BRANCH": "main",
    }.items():
        monkeypatch.setenv(name, value)
    reads = []
    monkeypatch.setattr(_shipmate, "_run", _gh(reads, pulls, checks, live, failing))
    monkeypatch.setattr(_shipmate, "_sleep", lambda s: None)
    bpp.main()
    out, err = capsys.readouterr()
    return out.split(), err, reads


def _gh(reads, pulls, checks, live, failing):
    """`_select`'s `_shipmate._run` double: it appends each argv to `reads` and answers it."""

    def run(args):
        reads.append(args)
        if args in failing:
            raise SystemExit(
                f"::error::command failed (1): {' '.join(args)}\ngh: Not Found (HTTP 404)"
            )
        if args == PULLS:
            return "".join(json.dumps(p) + "\n" for p in pulls)
        for n, rows in checks.items():
            if args == _checks_read(n):
                return "".join(json.dumps(r) + "\n" for r in rows)
        if args in LIVE_LISTS[:1]:
            return json.dumps({"workflow_runs": list(live)})
        if args in LIVE_LISTS:
            return json.dumps({"workflow_runs": []})
        raise AssertionError(args)

    return run


def test_check_stacks_skip_a_name_outside_the_apply_check_grammar(monkeypatch):
    """Mutations: `parse_check_name` reads the stack-first order as a fallback (`stacks/b`
    appears); index a None parse instead of skipping it (raises)."""
    rows = [
        check_run(name="apply / dev / stacks/a", id=1),
        check_run(name="apply / stacks/b / dev", id=2),
    ]
    monkeypatch.setattr(_shipmate, "_run", _gh([], [], {1: rows}, (), ()))
    assert bpp.check_stacks("acme/demo", _sha(1), APP_ID) == {"stacks/a"}


@pytest.mark.parametrize(
    "check",
    [
        pytest.param(_apply_check("stacks/app", cells=1), id="queued"),
        pytest.param(check_run(), id="success"),
        pytest.param(check_run(conclusion="neutral"), id="neutral"),
        pytest.param(check_run(conclusion="failure"), id="failure"),
        pytest.param(behind_check("stacks/app", cells=1), id="behind"),
    ],
)
def test_an_apply_check_in_any_state_on_a_pushed_stack_selects_the_pull_request(
    monkeypatch, capsys, check
):
    """A pull request that already applied the cell still plans it, and a re-plan marks it
    behind. Mutation: select on a pending check only (`not ag._is_done`) -- the success and
    neutral rows select nothing."""
    printed, err, _ = _select(monkeypatch, capsys, pulls=[_pull(1)], checks={1: [check]})
    assert (printed, err) == (["1"], "")


def test_a_draft_and_a_pull_request_without_a_pushed_stack_are_not_selected(monkeypatch, capsys):
    """#2 is a draft on the pushed stack, #3 checks another stack, #4 has no App check (another
    App's check on the pushed stack). Another base is filtered by the listing's `base=`, pinned
    whole in `PULLS`. Mutations: drop the draft filter -- #2 prints; select every pull request
    with an App check -- #3 prints; drop `from_app` (read every check) -- #4 prints."""
    printed, err, reads = _select(
        monkeypatch,
        capsys,
        pulls=[_pull(1), _pull(2, draft=True), _pull(3), _pull(4)],
        checks={
            1: [_apply_check("stacks/app", cells=1)],
            2: [_apply_check("stacks/app", cells=1)],
            3: [_apply_check("stacks/dns", cells=1)],
            4: [check_run(app={"id": 1})],
        },
    )
    assert (printed, err) == (["1"], "")
    assert reads == [PULLS, _checks_read(1), _checks_read(3), _checks_read(4), *LIVE_LISTS]


def test_a_push_outside_every_stack_selects_every_pull_request_with_an_app_check(
    monkeypatch, capsys
):
    """Mutation: ignore `outside` -- #3 is not printed."""
    printed, _, _ = _select(
        monkeypatch,
        capsys,
        push='{"stacks": [], "outside": true}',
        pulls=[_pull(1), _pull(3), _pull(4)],
        checks={
            1: [_apply_check("stacks/app", cells=1)],
            3: [_apply_check("stacks/dns", cells=1)],
            4: [],
        },
    )
    assert printed == ["1", "3"]


def test_a_live_app_apply_run_skips_its_pull_request_and_names_it(monkeypatch, capsys):
    """#1 has a live App `auto` run, #2 a live `apply` run another actor started, #3 none. One
    live listing serves every candidate. Mutations: drop `dispatched_by_app` (skip any live
    run) -- #2 is skipped; list the live runs per pull request -- the listings repeat."""
    printed, err, reads = _select(
        monkeypatch,
        capsys,
        pulls=[_pull(1), _pull(2), _pull(3)],
        checks={n: [_apply_check("stacks/app", cells=1)] for n in (1, 2, 3)},
        live=[_apply_run(71, 1, "auto"), _apply_run(72, 2, actor="dev")],
    )
    assert printed == ["2", "3"]
    assert err == (
        "::notice title=re-plan::pull request #1 was not re-planned: its auto run 71 is live, "
        "and a plan now would republish the apply checks it applies from\n"
    )
    assert reads == [PULLS, *map(_checks_read, (1, 2, 3)), *LIVE_LISTS]


@pytest.mark.parametrize(
    ("live", "failing"),
    [
        pytest.param([], LIVE_LISTS[1:2], id="failed-page"),
        pytest.param([_run_row(9, "shipmate custom")], (), id="unparsed-app-run"),
    ],
)
def test_an_incomplete_live_listing_selects_nothing(monkeypatch, capsys, live, failing):
    """A failed page or an App run whose title does not parse can hide a live apply. Mutation:
    read an incomplete listing as complete -- #1 prints."""
    printed, err, _ = _select(
        monkeypatch,
        capsys,
        pulls=[_pull(1)],
        checks={1: [_apply_check("stacks/app", cells=1)]},
        live=live,
        failing=failing,
    )
    assert printed == []
    assert err.splitlines()[-1] == (
        "::warning::the live shipmate.yml run listing is incomplete, so no open pull request was "
        "re-planned after this push"
    )


def test_a_failed_check_read_skips_only_that_pull_request(monkeypatch, capsys):
    """Mutation: `return picked` in place of `continue` in the read failure's handler -- #2 is
    not printed."""
    printed, err, _ = _select(
        monkeypatch,
        capsys,
        pulls=[_pull(1), _pull(2)],
        checks={2: [_apply_check("stacks/app", cells=1)]},
        failing=[_checks_read(1)],
    )
    assert printed == ["2"]
    assert err.splitlines()[-1].startswith(
        "::warning::pull request #1 was not re-planned: command failed (1): gh api --paginate "
    )


@pytest.mark.parametrize(
    "push",
    [
        "",
        "not json",
        '{"stacks": ["stacks/app"]}',
        '{"stacks": null, "outside": false}',
        '{"stacks": "stacks/app", "outside": false}',
        '{"stacks": ["stacks/app"], "outside": "false"}',
    ],
)
def test_an_empty_or_malformed_push_selects_nothing_and_reads_nothing(monkeypatch, capsys, push):
    """Mutation: read an unreadable push as `outside` (`(set(), True)`) -- #1 prints."""
    printed, err, reads = _select(
        monkeypatch,
        capsys,
        push=push,
        pulls=[_pull(1)],
        checks={1: [_apply_check("stacks/app", cells=1)]},
    )
    assert (printed, reads) == ([], [])
    assert err == (
        "::warning::deploy detect did not report the changed stacks, so no open pull request "
        "was re-planned after this push\n"
    )


def test_an_oversized_push_selects_every_pull_request_with_an_app_check(monkeypatch, capsys):
    """`{"stacks": null, "outside": true}` is the over-cap value. Mutation: refuse a null
    `stacks` -- nothing prints."""
    printed, _, _ = _select(
        monkeypatch,
        capsys,
        push='{"stacks": null, "outside": true}',
        pulls=[_pull(1)],
        checks={1: [_apply_check("stacks/dns", cells=1)]},
    )
    assert printed == ["1"]


def test_an_empty_slug_selects_nothing(monkeypatch, capsys):
    """No live run could be told to be the App's. Mutation: drop the slug check -- #1 prints
    (the stub run counts as no App run)."""
    printed, err, reads = _select(
        monkeypatch,
        capsys,
        slug="",
        pulls=[_pull(1)],
        checks={1: [_apply_check("stacks/app", cells=1)]},
        live=[_apply_run(71, 1)],
    )
    assert (printed, reads) == ([], [])
    assert err == (
        "::warning::the App id or slug is empty, so no open pull request was re-planned after "
        "this push\n"
    )


# The step, through bash, with `python3` stubbed per script.

STEP = "Dispatch a plan of each selected pull request"
MINT = "Mint the re-plan token"
EXPRESSIONS = {
    "${{ steps.token.outputs.token }}": "replan-token",
    "${{ steps.token.outputs.app-slug }}": SLUG,
    "${{ inputs.app-id }}": APP_ID,
    "${{ inputs.push }}": '{"stacks": [], "outside": true}',
    "${{ github.event.repository.default_branch }}": "main",
}
#: `python3`: `base-push-plan` prints `$FAKE_PRS` and `$FAKE_SELECT_ERR` and exits
#: `$FAKE_SELECT_RC`; `dispatch-body` records its env, prints `$FAKE_DISPATCH_ERR` to stderr and
#: exits `$FAKE_RC_<pr>` (0 by default).
_PYTHON3 = """\
if [[ "$1" == */base-push-plan ]]; then
  printf '%s' "${FAKE_PRS:-}"
  printf '%s' "${FAKE_SELECT_ERR:-}" >&2
  [ -z "${CLOCK:-}" ] || echo $(( $(cat "$CLOCK") + ${FAKE_SELECT_SECONDS:-0} )) > "$CLOCK"
  exit "${FAKE_SELECT_RC:-0}"
elif [[ "$1" == */dispatch-body ]]; then
  echo "$PR_NUMBER $VERB $DISPATCH_REF $SHIPMATE_APP_SLUG $GH_TOKEN ${COMMENT_ID-unset}" >> "$CALLS"
  printf '%b' "${FAKE_DISPATCH_ERR:-}" >&2
  [ -z "${CLOCK:-}" ] || echo $(( $(cat "$CLOCK") + ${FAKE_DISPATCH_SECONDS:-0} )) > "$CLOCK"
  rc_var="FAKE_RC_$PR_NUMBER"
  exit "${!rc_var:-0}"
fi
exit 9
"""


#: `date`: the epoch seconds in `$CLOCK`, which only the `python3` stub advances, by
#: `$FAKE_SELECT_SECONDS` per selection and `$FAKE_DISPATCH_SECONDS` per dispatch. The step reads
#: the clock only through `date +%s`.
_DATE = 'cat "$CLOCK"'


def _step(tmp_path, token="replan-token", clock=False, **fake):  # noqa: S107 - a stub token
    """Run the dispatch step as the runner would: its parsed `env:` resolved from
    `EXPRESSIONS`. With `clock`, `date` is stubbed (`_DATE`) from 0; otherwise the real clock
    runs. Returns `(exit code, stdout, stderr, dispatch-body calls)`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_stub(bin_dir, "python3", _PYTHON3)
    if clock:
        write_stub(bin_dir, "date", _DATE)
        (tmp_path / "clock").write_text("0\n", encoding="utf-8", newline="\n")
        fake["CLOCK"] = str(tmp_path / "clock")
    calls = tmp_path / "calls"
    expressions = {**EXPRESSIONS, "${{ steps.token.outputs.token }}": token}
    step = step_by("base-push-plan", name=STEP)
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
    env |= {k: expressions[v] if v.startswith("${{") else v for k, v in step["env"].items()}
    env |= {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "CALLS": str(calls),
        "GITHUB_ACTION_PATH": str(ACTIONS / "base-push-plan"),
        "RUNNER_TEMP": str(tmp_path),
        **fake,
    }
    proc = run_step(tmp_path, step["run"], env)
    made = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return proc.returncode, proc.stdout, proc.stderr, made


def _notice(pr):
    return (
        f"::notice title=re-plan::dispatched shipmate plan for pull request #{pr}: this push may "
        "have changed a stack it plans"
    )


@bash_only
@pytest.mark.parametrize("rc", ["1", "3", "4"])
def test_a_failed_dispatch_warns_and_the_next_pull_request_is_still_dispatched(tmp_path, rc):
    """Each failure handler falls through to the next pull request, so one failed dispatch
    never drops the later ones.
    Mutations: `exit 0` after the failed-dispatch warning -- row 1 dispatches no #2; `exit 0`
    after the may-have-started warning -- rows 3 and 4 do not; `exit 1` after the
    failed-dispatch warning -- the step fails."""
    code, out, _, calls = _step(tmp_path, FAKE_PRS="1\n2\n", **{"FAKE_RC_1": rc})
    assert (code, calls) == (
        0,
        [f"{n} plan main {SLUG} replan-token unset" for n in (1, 2)],
    )
    warning = (
        "::warning::the plan dispatch for pull request #1 failed; comment shipmate plan on it to "
        "check it against the base"
        if rc == "1"
        else "::warning::the plan dispatch for pull request #1 answered with an error, so a plan "
        "may have started; check the Actions tab"
    )
    assert out.splitlines() == [warning, _notice(2)]


@bash_only
def test_a_failed_dispatchs_errors_are_downgraded_to_warnings(tmp_path):
    """The re-plan never fails the deploy, so `dispatch-body`'s `::error::` lines reach the log
    as warnings, an indented one too: the runner trims leading whitespace before it parses a
    command. Mutations: `cat dispatch.err >&2` in place of the downgrade -- both lines stay
    errors; drop `[[:space:]]*` from the pattern -- the indented one stays an error."""
    code, _, err, _ = _step(
        tmp_path,
        FAKE_PRS="1\n",
        FAKE_RC_1="1",
        FAKE_DISPATCH_ERR="::error::gh failed\\n  ::error::indented\\n",
    )
    assert (code, err) == (0, "::warning::gh failed\n::warning::indented\n")


@bash_only
def test_a_spent_budget_names_every_pull_request_not_reached_and_exits_0(tmp_path):
    """Selection takes no time and #1's dispatch takes 600 s, the 10-minute budget: #1 is
    dispatched, #2 and #3 are not, and one warning names both. Mutations: drop the budget check
    -- #2 and #3 are dispatched; check the budget after the dispatch -- the warning names #1
    too."""
    code, out, _, calls = _step(
        tmp_path, clock=True, FAKE_DISPATCH_SECONDS="600", FAKE_PRS="1\n2\n3\n"
    )
    assert (code, calls) == (0, [f"1 plan main {SLUG} replan-token unset"])
    assert out.splitlines() == [
        _notice(1),
        (
            "::warning::not re-planned: the re-plan budget of 10 minutes is spent; comment "
            "shipmate plan on #2, #3"
        ),
    ]


@bash_only
def test_a_budget_spent_during_selection_dispatches_nothing(tmp_path):
    """The budget starts at the step's top, so it covers selection: selection takes 600 s, so
    no dispatch begins and the warning names both. Mutation: take `start` after the selection --
    #1 and #2 are dispatched."""
    code, out, _, calls = _step(tmp_path, clock=True, FAKE_SELECT_SECONDS="600", FAKE_PRS="1\n2\n")
    assert (code, calls) == (0, [])
    assert out.splitlines() == [
        (
            "::warning::not re-planned: the re-plan budget of 10 minutes is spent; comment "
            "shipmate plan on #1, #2"
        ),
    ]


@bash_only
def test_the_selection_log_is_written_under_runner_temp(tmp_path):
    """The step's cwd is the consumer's workspace, so the log goes to `RUNNER_TEMP`. Mutation:
    `err=select.err` -- the log lands in the cwd."""
    temp = tmp_path / "runner-temp"
    temp.mkdir()
    code, _, _, _ = _step(tmp_path, FAKE_PRS="", RUNNER_TEMP=str(temp))
    assert (code, (temp / "select.err").is_file(), (tmp_path / "select.err").exists()) == (
        0,
        True,
        False,
    )


@bash_only
def test_a_failed_mint_warns_and_dispatches_nothing(tmp_path):
    """Mutation: drop the empty-token check -- the selection runs and #1 is dispatched."""
    assert _step(tmp_path, token="", FAKE_PRS="1\n") == (
        0,
        (
            "::warning::the re-plan token mint failed, so no open pull request was re-planned "
            "after this push\n"
        ),
        "",
        [],
    )


@bash_only
def test_a_failed_selection_warns_and_dispatches_nothing(tmp_path):
    """The selection's `::error::` is downgraded and the step exits 0. Mutation: `exit 1` in the
    selection's failure branch -- the step fails."""
    code, out, err, calls = _step(
        tmp_path, FAKE_PRS="1\n", FAKE_SELECT_RC="1", FAKE_SELECT_ERR="::error::boom\n"
    )
    assert (code, err, calls) == (0, "::warning::boom\n", [])
    assert out == (
        "::warning::could not select the open pull requests to re-plan, so none was re-planned "
        "after this push\n"
    )


def test_the_mint_is_display_only_with_its_permissions_whole():
    """Mutations: drop `continue-on-error`; add `permission-pull-requests: write`; drop
    `permission-checks`."""
    mint = step_by("base-push-plan", name=MINT)
    assert (mint["id"], mint["continue-on-error"]) == ("token", True)
    assert mint["uses"] == step_by("auto-dispatch", id="autotoken")["uses"]
    assert mint["with"] == {
        "client-id": "${{ inputs.app-id }}",
        "private-key": "${{ inputs.private-key }}",
        "owner": "${{ github.repository_owner }}",
        "repositories": "${{ github.event.repository.name }}",
        "permission-actions": "write",
        "permission-pull-requests": "read",
        "permission-checks": "read",
    }


def test_the_dispatch_step_env_is_whole():
    """Mutations: drop `SHIPMATE_APP_SLUG`; read `GH_TOKEN` from `github.token`."""
    assert step_by("base-push-plan", name=STEP)["env"] == {
        "GH_TOKEN": "${{ steps.token.outputs.token }}",
        "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
        "SHIPMATE_APP_SLUG": "${{ steps.token.outputs.app-slug }}",
        "SHIPMATE_PUSH": "${{ inputs.push }}",
        "SHIPMATE_DEFAULT_BRANCH": "${{ github.event.repository.default_branch }}",
        "VERB": "plan",
        "DISPATCH_REF": "${{ github.event.repository.default_branch }}",
    }

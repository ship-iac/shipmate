"""`scripts/mirror-checks` and the summary step that runs it.

Three of these carry the weight. The suite-id refusal: a run whose own check suite could not be
identified must mirror nothing, or it copies every check on the commit, other workflows'
included, onto the pull request head. The two row filters: `completed` excludes this job's own
still-running check, and the `plan / ` prefix excludes the consumer file's sibling jobs,
which complete as `skipped` in this same suite. And the fixed
output text: the plan output is author-controlled, so no cell summary or plan text may reach the
mirrored check.

The step-order guard compares the action's whole step-name list against a hand-written constant.
The mirror running before `Build comment + gate state` is the only thing making the sticky
comment's per-cell links resolve under a dispatch, and nothing else in the suite would notice it
moving.
"""

import io
import json
import os

import pytest
from _loader import (
    action_steps,
    action_yaml,
    bash_only,
    load_script,
    run_step,
    step_by,
)

mc = load_script("mirror-checks")

HEAD = "a" * 40
RUN_COMMIT = "b" * 40
SUITE = 4242
OTHER_SUITE = 9999

#: Hand-written, never read back from the module: the whole `output` mapping every mirrored body
#: must carry, whatever the source check said.
EXPECTED_OUTPUT = {
    "title": "planned on demand",
    "summary": (
        "Mirrored onto this commit from this plan run's own check, which is attached to the "
        "ref the run was dispatched on. Follow the link for the plan output."
    ),
}


def _check(name, **over):
    check = {
        "name": name,
        "status": "completed",
        "conclusion": "success",
        "head_sha": RUN_COMMIT,
        "html_url": f"https://github.test/runs/{name.rsplit(' / ', 1)[-1]}",
        "check_suite": {"id": SUITE},
        "output": {"title": "5 to add", "summary": "plan text a pull request author wrote"},
    }
    check.update(over)
    return check


def _lines(*checks):
    return [json.dumps(c) for c in checks]


def test_only_this_suites_completed_checks_are_mirrored():
    """Both filters, and both directions of each: a completed check in another suite and an
    unfinished check in this one are equally out."""
    lines = _lines(
        _check("plan / facts"),
        _check("plan / dev-eu / dns"),
        _check("plan / another run's cell", check_suite={"id": OTHER_SUITE}),
        _check("plan / a cell with no suite", check_suite=None),
        _check("plan / summary", status="in_progress", conclusion=None),
        _check("plan / queued cell", status="queued", conclusion=None),
    )
    got = [b["name"] for b in mc.bodies(lines, SUITE, HEAD)]
    assert got == ["plan / facts", "plan / dev-eu / dns"]


def test_only_the_engine_cell_checks_are_mirrored():
    """A dispatched plan's suite holds the callee's cell checks AND the skipped router jobs of
    the consumer's one workflow file. Only the first belong on the pull-request head: a grey
    `apply` or `deploy` there says an apply was skipped for this commit, which is not
    what happened and not what the reviewer is being asked to read.

    Mutation: drop the prefix filter from `bodies`, and the four skipped router rows below are
    mirrored too.
    """
    lines = _lines(
        _check("plan / dev-eu / stacks/network"),
        _check("comment", conclusion="skipped"),
        _check("deploy", conclusion="skipped"),
        _check("apply", conclusion="skipped"),
        _check("unlock", conclusion="skipped"),
    )
    got = [b["name"] for b in mc.bodies(lines, SUITE, HEAD)]
    assert got == ["plan / dev-eu / stacks/network"], (
        f"only the engine's own cell checks belong on the head: {got}"
    )


@pytest.mark.parametrize("suite_id", ["", None, "abc", "12a", " 12", "1.0", "-1", "42\n"])
def test_an_unidentified_suite_refuses_instead_of_matching_everything(suite_id):
    # `match`, so a refusal raised later for an unrelated reason cannot stand in for this one.
    with pytest.raises(SystemExit, match="check suite could not be identified"):
        mc.bodies(_lines(_check("plan / dev-eu / dns")), suite_id, HEAD)


def test_the_suite_id_is_accepted_as_the_number_the_api_answers():
    """`check_suite_id` comes back from the runs API as a JSON number, and `check_suite.id` in
    the listing likewise, so the comparison must survive both being ints rather than strings."""
    assert [b["name"] for b in mc.bodies(_lines(_check("plan / dev-eu / dns")), SUITE, HEAD)] == [
        "plan / dev-eu / dns"
    ]


def test_every_body_targets_the_head_not_the_runs_own_commit():
    bodies = mc.bodies(
        _lines(_check("plan / dev-eu / dns"), _check("plan / dev-eu / app")), SUITE, HEAD
    )
    assert [b["head_sha"] for b in bodies] == [HEAD, HEAD]


def test_a_conclusion_the_create_endpoint_rejects_becomes_neutral():
    """`stale` is answered by the listing and refused on create. An accepted conclusion must
    still come through unchanged, or every mirrored row reads neutral."""
    bodies = mc.bodies(
        _lines(
            _check("plan / stale one", conclusion="stale"),
            _check("plan / none at all", conclusion=None),
            _check("plan / failed one", conclusion="failure"),
        ),
        SUITE,
        HEAD,
    )
    assert [b["conclusion"] for b in bodies] == ["neutral", "neutral", "failure"]
    assert [b["status"] for b in bodies] == ["completed", "completed", "completed"]


def test_the_output_text_is_fixed_and_carries_no_plan_text():
    """The whole mapping against a hand-written constant: the source check's own title and
    summary are author-controlled, and the mirrored check must not become a second rendering
    surface for them."""
    bodies = mc.bodies(_lines(_check("plan / dev-eu / dns")), SUITE, HEAD)
    assert [b["output"] for b in bodies] == [EXPECTED_OUTPUT]


def test_details_url_is_the_source_check_and_absent_when_it_has_none():
    """Absent, not empty: `POST /check-runs` takes `details_url` as a URL, and an empty string is
    not one."""
    bodies = mc.bodies(
        _lines(
            _check("plan / linked"),
            _check("plan / unlinked", html_url=None),
            _check("plan / blank", html_url=""),
        ),
        SUITE,
        HEAD,
    )
    assert [b.get("details_url") for b in bodies] == ["https://github.test/runs/linked", None, None]
    assert "details_url" not in bodies[1] and "details_url" not in bodies[2]


def _run_main(monkeypatch, capsys, stdin):
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/consumer")
    monkeypatch.setenv("GITHUB_RUN_ID", "77")
    monkeypatch.setenv("SHIPMATE_HEAD_SHA", HEAD)
    monkeypatch.setattr(mc, "gh_json", lambda path: {"check_suite_id": SUITE})
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    mc.main()
    return capsys.readouterr()


def test_a_mirror_that_copied_nothing_says_so(monkeypatch, capsys):
    """Silence here means the reviewer sees no per-cell checks and nothing anywhere says why."""
    out = _run_main(monkeypatch, capsys, "")
    assert out.out == ""
    assert out.err.startswith("::warning::")


def test_main_emits_one_json_body_per_mirrored_check(monkeypatch, capsys):
    out = _run_main(monkeypatch, capsys, "\n".join(_lines(_check("plan / dev-eu / dns"))) + "\n")
    assert [json.loads(line)["name"] for line in out.out.splitlines()] == ["plan / dev-eu / dns"]
    assert out.err == ""


#: The summary action's whole step-name list, hand-written and in order. The mirror must run
#: before `Build comment + gate state`, which resolves each cell's comment link against the
#: checks on the head; after it, every link on a dispatched plan's comment degrades to the
#: workflow-run URL. The download sits where `apply-summary`'s does, between the key check and
#: the mint. `Create apply checks` follows the comment, so a failed comment write publishes no apply
#: check. Mutations: move the download after the mint; move `Create apply checks` back above the
#: mirror.
EXPECTED_STEP_NAMES = [
    "Verify the App key arrived",
    "Download plan cell summaries",
    "Mint App installation token",
    "Hold the gate while apply checks publish",
    "Mirror this run's per-cell plan checks onto the head",
    "$/actions/python-yaml",
    "Doctor: settings-drift warnings (annotations only, never blocks)",
    "Mark cells another pull request holds",
    "Build comment + gate state",
    "Decide gate state and comment mode",
    "Download cell errors",
    "Upsert sticky comment",
    "Create apply checks (pending / no-changes)",
    "Create/refresh gate",
    "Collapse the started reply",
]


def test_the_mirror_step_runs_before_the_comment_is_built():
    assert [s.get("name") or s["uses"] for s in action_steps("summary")] == EXPECTED_STEP_NAMES


_MIRROR = "Mirror this run's per-cell plan checks onto the head"


def test_the_mirror_runs_only_for_an_on_demand_plan():
    """Unconditional would put a second producer behind every plan check name on a pull-request
    run, where those checks are already on the head. A superseded run mirrors nothing.
    Mutation: drop `inputs.on-demand == 'true' &&` -> red."""
    assert step_by("summary", name=_MIRROR)["if"] == (
        "${{ inputs.on-demand == 'true' && steps.hold.outputs.superseded != 'true' }}"
    )


def test_the_action_declares_the_on_demand_input_it_decides_on():
    """An undeclared action input is merely ignored with a warning, so the step's `if:` would
    read empty on every run and the mirror would never fire."""
    declared = action_yaml("summary")["inputs"]["on-demand"]
    assert (declared["required"], declared["default"]) == (False, "")


#: Answers each POST `gh` call, recording every body it was handed and refusing the first.
#: `python3` stands in for `gate-status checks`: it refuses any argv but the Mirror step's whole
#: one, else prints `$FAKE_LISTING` and returns `$FAKE_LISTING_RC` without reading stdin. Any
#: other `python3` call is mirror-checks. Bash resolves a function before searching PATH, so this
#: needs no fake executables.
_GH_STUB = """
gh() {
  case "$*" in
    *"/check-runs"*) body=$(cat)
      printf '%s\\n' "$body" >> "$POSTED"
      case "$body" in *"first cell"*) return 1 ;; esac ;;
    *) printf 'unexpected gh call: %s\\n' "$*" >&2 ; return 1 ;;
  esac
}
python3() {
  if [[ "$1" == */scripts/gate-status ]]; then
    want="$GITHUB_ACTION_PATH/../../scripts/gate-status checks $GITHUB_SHA .check_runs[]"
    if [ "$*" != "$want" ]; then
      printf 'unexpected gate-status call: %s\\n' "$*" >&2 ; return 1
    fi
    printf '%s' "${FAKE_LISTING:-}"
    return "${FAKE_LISTING_RC:-0}"
  fi
  cat > /dev/null ; printf '%s\\n' "$FAKE_BODIES"
}
"""


@bash_only
def test_a_failed_post_still_attempts_the_rest_and_names_what_it_lost(tmp_path):
    """The listing is unordered, so abandoning the remaining bodies drops an arbitrary suffix of
    them, which can be exactly the failed cell the mirror exists to put on the pull request. The
    step still exits 0. Mutations: list `$SHIPMATE_HEAD_SHA` instead of `$GITHUB_SHA`, or change
    the `.check_runs[]` projection -> the stub refuses the listing and nothing is posted."""
    posted = tmp_path / "posted"
    proc = run_step(
        tmp_path,
        _GH_STUB + step_by("summary", name=_MIRROR)["run"],
        {
            **os.environ,
            "GH_TOKEN": "x",
            "GITHUB_REPOSITORY": "acme/demo",
            "GITHUB_SHA": RUN_COMMIT,
            "GITHUB_ACTION_PATH": str(tmp_path),
            "SHIPMATE_HEAD_SHA": HEAD,
            "FAKE_BODIES": "\n".join(
                json.dumps({"name": name, "head_sha": HEAD}) for name in ("first cell", "second")
            ),
            "POSTED": str(posted),
        },
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    attempted = [json.loads(line)["name"] for line in posted.read_text().splitlines()]
    assert attempted == ["first cell", "second"], f"stdout={proc.stdout!r}"
    assert '::warning::could not mirror the check "first cell"' in proc.stdout
    assert "second" not in proc.stdout


@bash_only
def test_a_failed_listing_warns_and_mirrors_nothing(tmp_path):
    """Reviewer-facing only, so a failed listing degrades rather than failing the job before
    `Create/refresh gate`, and adds no annotation of its own. Mutation: drop the `if !` around
    the listing -> `set -e` ends the step with exit 1."""
    posted = tmp_path / "posted"
    proc = run_step(
        tmp_path,
        _GH_STUB + step_by("summary", name=_MIRROR)["run"],
        {
            **os.environ,
            "GH_TOKEN": "x",
            "GITHUB_REPOSITORY": "acme/demo",
            "GITHUB_SHA": RUN_COMMIT,
            "GITHUB_ACTION_PATH": str(tmp_path),
            "SHIPMATE_HEAD_SHA": HEAD,
            "FAKE_LISTING_RC": "1",
            "FAKE_BODIES": json.dumps({"name": "first cell", "head_sha": HEAD}),
            "POSTED": str(posted),
        },
    )
    assert (proc.returncode, proc.stdout, proc.stderr, posted.exists()) == (
        0,
        (
            "::warning::could not list this run's own check runs; the per-cell plan checks were "
            "not mirrored onto the pull request head\n"
        ),
        "",
        False,
    )

"""`scripts/mirror-checks` and the summary step that runs it.

Three of these carry the weight. The suite-id refusal: a run whose own check
suite could not be identified must mirror NOTHING, or it copies every check on
the commit -- other workflows' included -- onto the pull request head. The
`completed` filter: it is what excludes this job's own still-running check
without matching on a job name the consumer's wrapper chooses. And the fixed
output text: the plan output is author-controlled, so no cell summary or plan
text may reach the mirrored check.

The step-order guard compares the action's whole step-name list against a
hand-written constant. The mirror running before `Build comment + gate state` is
the only thing making the sticky comment's per-cell links resolve under a
dispatch, and nothing else in the suite would notice it moving.
"""

import io
import json
import os
import subprocess

import pytest
from _loader import action_steps, action_yaml, load_script, usable_bash

_BASH = usable_bash()

mc = load_script("mirror-checks")

HEAD = "a" * 40
RUN_COMMIT = "b" * 40
SUITE = 4242
OTHER_SUITE = 9999

#: Hand-written, never read back from the module: the whole `output` mapping
#: every mirrored body must carry, whatever the source check said.
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
        "html_url": f"https://github.test/runs/{name}",
        "check_suite": {"id": SUITE},
        "output": {"title": "5 to add", "summary": "plan text a pull request author wrote"},
    }
    check.update(over)
    return check


def _lines(*checks):
    return [json.dumps(c) for c in checks]


def test_only_this_suites_completed_checks_are_mirrored():
    """Both filters, and both directions of each: a completed check in another
    suite and an unfinished check in this one are equally out."""
    lines = _lines(
        _check("shipmate / facts"),
        _check("dns / dev-eu"),
        _check("some other workflow", check_suite={"id": OTHER_SUITE}),
        _check("no suite at all", check_suite=None),
        _check("shipmate / summary", status="in_progress", conclusion=None),
        _check("queued cell", status="queued", conclusion=None),
    )
    got = [b["name"] for b in mc.bodies(lines, SUITE, HEAD)]
    assert got == ["shipmate / facts", "dns / dev-eu"]


@pytest.mark.parametrize("suite_id", ["", None, "abc", "12a", " 12", "1.0", "-1", "42\n"])
def test_an_unidentified_suite_refuses_instead_of_matching_everything(suite_id):
    # `match`, so a refusal raised later for an unrelated reason cannot stand in
    # for this one.
    with pytest.raises(SystemExit, match="check suite could not be identified"):
        mc.bodies(_lines(_check("dns / dev-eu")), suite_id, HEAD)


def test_the_suite_id_is_accepted_as_the_number_the_api_answers():
    """`check_suite_id` comes back from the runs API as a JSON number, and
    `check_suite.id` in the listing likewise -- the comparison must survive
    both being ints rather than strings."""
    assert [b["name"] for b in mc.bodies(_lines(_check("dns / dev-eu")), SUITE, HEAD)] == [
        "dns / dev-eu"
    ]


def test_every_body_targets_the_head_not_the_runs_own_commit():
    bodies = mc.bodies(_lines(_check("dns / dev-eu"), _check("app / dev-eu")), SUITE, HEAD)
    assert [b["head_sha"] for b in bodies] == [HEAD, HEAD]


def test_a_conclusion_the_create_endpoint_rejects_becomes_neutral():
    """`stale` is answered by the listing and refused on create; an accepted one
    must still come through unchanged, or every mirrored row reads neutral."""
    bodies = mc.bodies(
        _lines(
            _check("stale one", conclusion="stale"),
            _check("none at all", conclusion=None),
            _check("failed one", conclusion="failure"),
        ),
        SUITE,
        HEAD,
    )
    assert [b["conclusion"] for b in bodies] == ["neutral", "neutral", "failure"]
    assert [b["status"] for b in bodies] == ["completed", "completed", "completed"]


def test_the_output_text_is_fixed_and_carries_no_plan_text():
    """The whole mapping against a hand-written constant: the source check's own
    title and summary are author-controlled, and the mirrored check must not
    become a second rendering surface for them."""
    bodies = mc.bodies(_lines(_check("dns / dev-eu")), SUITE, HEAD)
    assert [b["output"] for b in bodies] == [EXPECTED_OUTPUT]


def test_details_url_is_the_source_check_and_absent_when_it_has_none():
    """Absent, not empty: `POST /check-runs` takes `details_url` as a URL, and an
    empty string is not one."""
    bodies = mc.bodies(
        _lines(
            _check("linked"),
            _check("unlinked", html_url=None),
            _check("blank", html_url=""),
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
    monkeypatch.setattr(mc.bm, "gh_json", lambda path: {"check_suite_id": SUITE})
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    mc.main()
    return capsys.readouterr()


def test_a_mirror_that_copied_nothing_says_so(monkeypatch, capsys):
    """Silence here means the reviewer sees no per-cell checks and nothing
    anywhere says why."""
    out = _run_main(monkeypatch, capsys, "")
    assert out.out == ""
    assert out.err.startswith("::warning::")


def test_main_emits_one_json_body_per_mirrored_check(monkeypatch, capsys):
    out = _run_main(monkeypatch, capsys, "\n".join(_lines(_check("dns / dev-eu"))) + "\n")
    assert [json.loads(line)["name"] for line in out.out.splitlines()] == ["dns / dev-eu"]
    assert out.err == ""


#: The summary action's whole step-name list, hand-written and in order. The
#: mirror must run BEFORE `Build comment + gate state`, which resolves each
#: cell's comment link against the checks on the head; after it, every link on a
#: dispatched plan's comment degrades to the workflow-run URL.
EXPECTED_STEP_NAMES = [
    "Verify the App key arrived",
    "Mint App installation token",
    "Mint an environments-scoped token for doctor's plan-env secret probe",
    "Create apply checks (pending / no-changes)",
    "Mirror this run's per-cell plan checks onto the head",
    "Build comment + gate state",
    "Decide gate state and comment mode",
    "Doctor — settings-drift warnings (annotations only, never blocks)",
    "Upsert sticky comment",
    "Create/refresh gate",
]


def test_the_mirror_step_runs_before_the_comment_is_built():
    assert [s["name"] for s in action_steps("summary")] == EXPECTED_STEP_NAMES


def _mirror_step():
    steps = [
        s
        for s in action_steps("summary")
        if s.get("name") == "Mirror this run's per-cell plan checks onto the head"
    ]
    assert len(steps) == 1
    return steps[0]


def test_the_mirror_runs_only_for_an_on_demand_plan():
    """Unconditional would put a second producer behind every plan check name on
    a pull-request run, where those checks are already on the head."""
    assert _mirror_step()["if"] == "${{ inputs.on-demand == 'true' }}"


def test_the_action_declares_the_on_demand_input_it_decides_on():
    """An undeclared action input is merely ignored with a warning, so the step's
    `if:` would read empty on every run and the mirror would never fire."""
    declared = action_yaml("summary")["inputs"]["on-demand"]
    assert (declared["required"], declared["default"]) == (False, "")


# Dispatches the step's two `gh` calls: the listing (whose stdout the step
# redirects into run-checks.jsonl) and each POST, recording every body it was
# handed and refusing the first. `python3` stands in for mirror-checks -- bash
# resolves a function before searching PATH, so this needs no fake executables.
_GH_STUB = """
gh() {
  case "$*" in
    *"/check-runs?filter=all"*) printf '' ;;
    *"/check-runs"*) body=$(cat)
      printf '%s\\n' "$body" >> "$POSTED"
      case "$body" in *"first cell"*) return 1 ;; esac ;;
    *) printf 'unexpected gh call: %s\\n' "$*" >&2 ; return 1 ;;
  esac
}
python3() { cat > /dev/null ; printf '%s\\n' "$FAKE_BODIES" ; }
"""


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_failed_post_still_attempts_the_rest_and_names_what_it_lost(tmp_path):
    """The listing is unordered, so abandoning the remaining bodies drops an
    arbitrary suffix of them -- which can be exactly the failed cell the mirror
    exists to put on the pull request. The step still exits 0."""
    assert _BASH is not None
    script = tmp_path / "step.sh"
    script.write_text(_GH_STUB + _mirror_step()["run"], encoding="utf-8", newline="\n")
    posted = tmp_path / "posted"
    env = dict(os.environ)
    env.update(
        {
            "GH_TOKEN": "x",
            "GITHUB_REPOSITORY": "acme/demo",
            "GITHUB_SHA": RUN_COMMIT,
            "GITHUB_ACTION_PATH": str(tmp_path),
            "SHIPMATE_HEAD_SHA": HEAD,
            "FAKE_BODIES": "\n".join(
                json.dumps({"name": name, "head_sha": HEAD}) for name in ("first cell", "second")
            ),
            "POSTED": str(posted),
        }
    )
    proc = subprocess.run(
        [_BASH, str(script)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    attempted = [json.loads(line)["name"] for line in posted.read_text().splitlines()]
    assert attempted == ["first cell", "second"], f"stdout={proc.stdout!r}"
    assert '::warning::could not mirror the check "first cell"' in proc.stdout
    assert "second" not in proc.stdout

"""`pr-facts` is the single producer of the plan path's pull-request facts.

The properties worth pinning are all about where a fact comes from. A dispatched plan may state
a pull-request number and nothing else: the moment a head SHA or head repository could arrive as
a dispatch input, anyone holding `actions: write` could name a fork's head while claiming this
repository, and `build-matrix`'s fork refusal, which keys on the head repository stated to it,
would pass.

So the payload leg spends no API call, the dispatch leg resolves everything from the number
through one hand-written API path, and every partial answer is refused rather than emitted as an
empty fact.
"""

import json
import sys

import pytest
from _loader import action_yaml, load_script

pf = load_script("pr-facts")

_PAYLOAD_PR = {
    "number": 7,
    "state": "open",
    "draft": False,
    "head": {"sha": "a" * 40, "repo": {"full_name": "own/repo"}},
    "base": {"sha": "b" * 40},
}

_API_PR = {
    "number": 42,
    "state": "open",
    "draft": True,
    "head": {"sha": "c" * 40, "repo": {"full_name": "own/repo"}},
    "base": {"sha": "d" * 40},
}


def _no_api(path):
    raise RuntimeError(f"the payload leg must make no API call (got {path!r})")


def _main(monkeypatch, tmp_path, payload, gh=_no_api, repo="own/repo", module=pf):
    """main() over `payload`, with the API stubbed. Returns (raw GITHUB_OUTPUT text, api paths
    called): raw text so key order and one-per-line are observable, and the call list so a
    refusal before any API call is observable too."""
    event = tmp_path / "event.json"
    event.write_text(json.dumps(payload), encoding="utf-8")
    out = tmp_path / "out.txt"
    out.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_REPOSITORY", repo)
    calls = []

    def fake(path):
        calls.append(path)
        return gh(path)

    # The alias binds at import, so patching `pf.bm.gh_json` would never be seen.
    monkeypatch.setattr(module, "_gh_json", fake)
    try:
        module.main()
    finally:
        text = out.read_text(encoding="utf-8")
    return text, calls


def _api(pr):
    return lambda path: pr


def test_payload_leg_reads_the_payload_and_makes_no_api_call(monkeypatch, tmp_path):
    """A `pull_request` payload supplies all six facts, with zero API calls."""
    text, calls = _main(monkeypatch, tmp_path, {"pull_request": _PAYLOAD_PR})
    assert calls == []
    assert text.splitlines() == [
        f"head_sha={'a' * 40}",
        "head_repo=own/repo",
        f"base_sha={'b' * 40}",
        "pr_number=7",
        "is_draft=false",
        "on_demand=false",
    ]


def test_pr_facts_needs_no_yaml_parser(monkeypatch, tmp_path):
    """`pr-facts` never reads the config, so no step installs a parser before it. Mutation:
    add `import yaml` at the top of `env-config`, which `pr-facts` loads through
    `build-matrix` -- the load raises `ImportError`."""
    monkeypatch.setitem(sys.modules, "yaml", None)
    module = load_script("pr-facts")
    text, _ = _main(monkeypatch, tmp_path, {"pull_request": _PAYLOAD_PR}, module=module)
    assert text.splitlines()[0] == f"head_sha={'a' * 40}"


def test_dispatch_leg_resolves_the_inputs_number_through_one_api_path(monkeypatch, tmp_path):
    """No pull request in the payload: the `pr_number` input is looked up once, at the pulls
    path, and every fact comes from the answer.

    The inputs also state a head SHA and a head repository, as anyone holding `actions: write`
    could. Neither reaches an output: `build-matrix`'s fork refusal keys on the head repository
    this action emits, so a stated one would run fork-authored code on the consumer's
    runners."""
    text, calls = _main(
        monkeypatch,
        tmp_path,
        {"inputs": {"pr_number": "42", "head_sha": "e" * 40, "head_repo": "evil/fork"}},
        gh=_api(_API_PR),
    )
    assert calls == ["repos/own/repo/pulls/42"]
    assert text.splitlines() == [
        f"head_sha={'c' * 40}",
        "head_repo=own/repo",
        f"base_sha={'d' * 40}",
        "pr_number=42",
        "is_draft=true",
        "on_demand=true",
    ]


def test_a_run_with_neither_source_is_refused(monkeypatch, tmp_path):
    """Mutation: change one word of the remedy sentence in `scripts/pr-facts` (red)."""
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, tmp_path, {"inputs": {}})
    assert str(exc.value) == (
        "::error::this run has neither a pull request nor a pr_number input, so there is nothing "
        "to plan. A pull-request event supplies the first and a dispatched plan the second. "
        "Declare a `pr_number` input under `.github/workflows/shipmate.yml`'s "
        "`workflow_dispatch` trigger, and dispatch with `verb: plan` "
        "(see `docs/getting-started.md`)."
    )


def test_on_demand_marks_the_dispatch_leg_only(monkeypatch):
    """`on_demand` says which leg produced the facts, which is what lets an explicitly requested
    plan override the draft skip, so the two legs must not answer it the same way."""
    monkeypatch.setattr(pf, "_gh_json", _api(_API_PR))
    assert pf.from_payload(_PAYLOAD_PR)["on_demand"] == "false"
    assert pf.from_api("42", "own/repo")["on_demand"] == "true"


@pytest.mark.parametrize(
    "number",
    ["", "0", "abc", "12a", "1e3", "-1", "1/../../repos/other/repo/pulls/1", "12345678901", " 1"],
)
def test_a_number_that_is_not_a_pull_request_number_is_refused_before_the_api_call(
    monkeypatch, number
):
    """The number is interpolated into an API path, so it is validated whole: an 11-digit value
    and a path-traversing one are both refused, and the refusal lands before the call."""
    monkeypatch.setattr(pf, "_gh_json", _no_api)
    with pytest.raises(SystemExit) as exc:
        pf.from_api(number, "own/repo")
    assert "must be a pull-request number" in str(exc.value)


@pytest.mark.parametrize("state", ["closed", "merged", None])
def test_a_pull_request_that_is_not_open_is_refused(monkeypatch, state):
    """Planning a closed pull request would queue apply checks on a head the
    deploy path has already been through."""
    monkeypatch.setattr(pf, "_gh_json", _api(dict(_API_PR, state=state)))
    with pytest.raises(SystemExit) as exc:
        pf.from_api("42", "own/repo")
    assert "not open" in str(exc.value)


def test_an_unset_repository_is_refused(monkeypatch):
    monkeypatch.setattr(pf, "_gh_json", _no_api)
    with pytest.raises(SystemExit) as exc:
        pf.from_api("42", "")
    assert "GITHUB_REPOSITORY is unset" in str(exc.value)


def test_a_deleted_fork_head_is_refused_as_such():
    """A null `head.repo` is the deleted-fork case: refused with that name, not passed on as an
    empty head repository for the fork refusal to read."""
    pr = dict(_PAYLOAD_PR, head={"sha": "a" * 40, "repo": None})
    with pytest.raises(SystemExit) as exc:
        pf.from_payload(pr)
    assert "deleted fork" in str(exc.value)


@pytest.mark.parametrize(
    ("patch", "named", "unnamed"),
    [
        ({"head": {"repo": {"full_name": "own/repo"}}}, ["head_sha"], ["base_sha", "pr_number"]),
        ({"base": {}}, ["base_sha"], ["head_sha", "pr_number"]),
        ({"number": 0}, ["pr_number"], ["head_sha", "base_sha"]),
    ],
)
def test_a_missing_fact_is_refused_and_named(patch, named, unnamed):
    """Each absent fact is refused by name: an empty head SHA would leave the checkout on the
    base and green a gate over a pull request nobody planned."""
    with pytest.raises(SystemExit) as exc:
        pf.from_payload(dict(_PAYLOAD_PR, **patch))
    message = str(exc.value)
    assert [key for key in named if key in message] == named
    assert [key for key in unnamed if key in message] == []


@pytest.mark.parametrize(
    ("draft", "expected"),
    [(True, "true"), (False, "false"), (None, "false"), ("yes", "false")],
)
def test_is_draft_is_the_string_the_guards_compare_against(draft, expected):
    """The summary job's guard compares against 'false'; a Python bool renders as
    'True' and matches neither."""
    value = pf.from_payload(dict(_PAYLOAD_PR, draft=draft))["is_draft"]
    assert value == expected
    assert isinstance(value, str)


def test_the_actions_outputs_are_exactly_the_six_step_outputs():
    """Hand-written, whole-mapping: the action's kebab-case outputs and the script's snake_case
    GITHUB_OUTPUT keys are two spellings of one list, and a rename on either side leaves the
    other reading an empty string."""
    spec = action_yaml("pr-facts")
    assert {name: out["value"] for name, out in spec["outputs"].items()} == {
        "head-sha": "${{ steps.facts.outputs.head_sha }}",
        "head-repo": "${{ steps.facts.outputs.head_repo }}",
        "base-sha": "${{ steps.facts.outputs.base_sha }}",
        "pr-number": "${{ steps.facts.outputs.pr_number }}",
        "is-draft": "${{ steps.facts.outputs.is_draft }}",
        "on-demand": "${{ steps.facts.outputs.on_demand }}",
    }
    # No inputs at all: `github.token` is available inside a composite action, so an input for
    # it would be a consumer surface with no decision behind it.
    assert "inputs" not in spec


def test_event_payload_degrades_to_empty_dict(tmp_path):
    """Each of these reaches main() as {}, which has neither a pull request nor a `pr_number`
    and so refuses rather than emitting empty facts.

    Mutation: return `payload` without the `isinstance(payload, dict)` check -- the list case
    returns `[1, 2]`."""
    assert pf._event_payload("") == {}
    assert pf._event_payload(str(tmp_path / "absent.json")) == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert pf._event_payload(str(bad)) == {}
    listy = tmp_path / "list.json"
    listy.write_text("[1, 2]", encoding="utf-8")
    assert pf._event_payload(str(listy)) == {}

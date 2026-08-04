"""Unit tests for `scripts/wiring` — the consumer plan/summary wiring checks.

The three conditions under test are stated in CONTRACT.md §Post-plan topology:
the plan workflow lives at `.github/workflows/plan.yml`, its top-level `name:`
is exactly `shipmate · plan`, and some workflow triggers on `workflow_run` for
that name and calls the engine's reusable `summary.yml`.
"""

import pytest
from _loader import ENGINE, load_script

wi = load_script("wiring")

ENGINE_REPO = "acme/shipmate"

GOOD_PLAN = """name: shipmate · plan
on:
  pull_request:
    types: [opened, synchronize]
jobs:
  detect:
    runs-on: ubuntu-latest
""".encode()

GOOD_SUMMARY = """name: shipmate · summary
on:
  workflow_run:
    workflows: ["shipmate · plan"]
    types: [completed]
jobs:
  summary:
    uses: acme/shipmate/.github/workflows/summary.yml@1efbee8fae48417bee1f634bcd6721289c060027 # v1
    secrets: inherit
""".encode()


def good_files(**overrides):
    files = {"plan.yml": GOOD_PLAN, "summary.yml": GOOD_SUMMARY}
    files.update(overrides)
    return {k: v for k, v in files.items() if v is not ...}


def states(found):
    return [s for s, _ in found]


def joined(found):
    return "\n".join(m for _, m in found)


def test_correct_wiring_reports_nothing():
    assert wi.findings(good_files(), ENGINE_REPO) == []


def test_missing_plan_file_is_broken():
    found = wi.findings(good_files(**{"plan.yml": ...}), ENGINE_REPO)
    assert states(found) == [wi.BROKEN]
    assert wi.PLAN_PATH in joined(found)


def test_plan_yaml_suffix_does_not_satisfy_the_path():
    """The engine guard byte-matches `.github/workflows/plan.yml`, so a
    `plan.yaml` is a break, not an alias."""
    found = wi.findings(good_files(**{"plan.yml": ..., "plan.yaml": GOOD_PLAN}), ENGINE_REPO)
    assert wi.BROKEN in states(found)


@pytest.mark.parametrize(
    "name_line",
    [
        "name: shipmate - plan",  # hyphen, not a middot
        "name: shipmate ‧ plan",  # U+2027 HYPHENATION POINT look-alike
        "name: shipmate ・ plan",  # U+30FB KATAKANA MIDDLE DOT look-alike
        "name: shipmate � plan",  # what a latin-1 middot decodes to
        "name: Shipmate · plan",  # case differs
        "name: shipmate  ·  plan",  # extra whitespace
    ],
)
def test_a_look_alike_name_is_broken(name_line):
    plan = GOOD_PLAN.decode().replace("name: shipmate · plan", name_line).encode()
    found = wi.findings(good_files(**{"plan.yml": plan}), ENGINE_REPO)
    assert wi.BROKEN in states(found)


def test_the_mismatch_message_escapes_the_found_name():
    """A look-alike middot renders identically to the real one, so a message
    quoting it verbatim would appear to compare two identical strings."""
    plan = GOOD_PLAN.decode().replace("·", "・").encode()
    found = wi.findings(good_files(**{"plan.yml": plan}), ENGINE_REPO)
    assert "\\u30fb" in joined(found)


def test_a_quoted_exact_name_is_accepted():
    plan = GOOD_PLAN.decode().replace("name: shipmate · plan", 'name: "shipmate · plan"').encode()
    assert wi.findings(good_files(**{"plan.yml": plan}), ENGINE_REPO) == []


def test_a_missing_name_key_is_broken():
    """GitHub defaults a nameless workflow's name to its path, which can never
    match the consumer trigger."""
    plan = GOOD_PLAN.decode().replace("name: shipmate · plan\n", "").encode()
    found = wi.findings(good_files(**{"plan.yml": plan}), ENGINE_REPO)
    assert wi.BROKEN in states(found)


def test_a_block_scalar_name_degrades_rather_than_warns():
    """`>-` can legitimately fold to the correct name; warning would be a false
    positive on a correctly-wired repository."""
    plan = (
        GOOD_PLAN.decode().replace("name: shipmate · plan", "name: >-\n  shipmate · plan").encode()
    )
    found = wi.findings(good_files(**{"plan.yml": plan}), ENGINE_REPO)
    assert states(found) == [wi.UNKNOWN]


def test_a_backslash_escape_in_a_quoted_name_degrades():
    """This module does not process YAML escape sequences, so it must not claim
    `"shipmate \\u00b7 plan"` is wrong."""
    plan = (
        GOOD_PLAN.decode()
        .replace("name: shipmate · plan", 'name: "shipmate \\u00b7 plan"')
        .encode()
    )
    found = wi.findings(good_files(**{"plan.yml": plan}), ENGINE_REPO)
    assert states(found) == [wi.UNKNOWN]


def test_a_backslash_escape_in_an_unquoted_name_degrades():
    """A plain scalar carries no escapes, so this repository is probably broken --
    but "probably" is not what BROKEN means here, and the sequence is still the
    correct name spelled the long way."""
    plan = (
        GOOD_PLAN.decode().replace("name: shipmate · plan", "name: shipmate \\u00b7 plan").encode()
    )
    found = wi.findings(good_files(**{"plan.yml": plan}), ENGINE_REPO)
    assert states(found) == [wi.UNKNOWN]


def test_a_backslash_in_a_single_quoted_name_is_not_an_escape():
    """Single quotes carry no escapes in YAML, so `'shipmate \\u00b7 plan'` really
    is that literal text and really is the wrong name. The double-quoted spelling
    degrades; this one must not, or the degrade swallows a genuine break."""
    plan = (
        GOOD_PLAN.decode()
        .replace("name: shipmate · plan", "name: 'shipmate \\u00b7 plan'")
        .encode()
    )
    found = wi.findings(good_files(**{"plan.yml": plan}), ENGINE_REPO)
    assert states(found) == [wi.BROKEN]


def test_a_name_continued_on_the_following_line_degrades():
    """`plan_name`'s docstring counts "a value continued on following lines" as
    block, not just a `|`/`>` header -- and that spelling folds to the correct
    name just as legitimately."""
    plan = GOOD_PLAN.decode().replace("name: shipmate · plan", "name:\n  shipmate · plan").encode()
    found = wi.findings(good_files(**{"plan.yml": plan}), ENGINE_REPO)
    assert states(found) == [wi.UNKNOWN]


def test_a_byte_order_mark_does_not_hide_the_plan_name():
    """A BOM'd `plan.yml` carries U+FEFF in front of its column-0 `name:`, which
    reads as no top-level `name:` at all -- a BROKEN on a correctly-named
    workflow. Only `plan.yml` is exposed: its `name:` is the first line."""
    plan = b"\xef\xbb\xbf" + GOOD_PLAN
    assert wi.findings(good_files(**{"plan.yml": plan}), ENGINE_REPO) == []


def test_decode_drops_a_leading_byte_order_mark():
    assert wi.decode(b"\xef\xbb\xbfname: x") == "name: x"


def test_an_indented_name_is_not_the_top_level_one():
    """A job's `name:` is indented; only column 0 is the workflow's."""
    plan = ("on:\n  pull_request:\njobs:\n  detect:\n    name: shipmate · plan\n").encode()
    found = wi.findings(good_files(**{"plan.yml": plan}), ENGINE_REPO)
    assert wi.BROKEN in states(found)


def test_an_indented_on_is_not_the_top_level_one():
    """`_ON_KEY`'s column-0 anchor, the `_NAME_KEY` guard above's twin. Any key
    ending in `on` is an unanchored match -- `python-version: 3.12` is `versi` +
    `on:` -- and matching it retargets the block both the wiring checks and
    doctor's `pull_request_target` probe read, on a repository that is wired
    correctly."""
    summary = GOOD_SUMMARY.decode().replace(
        "on:\n  workflow_run:",
        "env:\n  python-version: 3.12\non:\n  workflow_run:",
    )
    assert wi.findings(good_files(**{"summary.yml": summary.encode()}), ENGINE_REPO) == []


def test_a_commented_name_does_not_count():
    plan = ("# name: shipmate · plan\n" + GOOD_PLAN.decode().split("\n", 1)[1]).encode()
    found = wi.findings(good_files(**{"plan.yml": plan}), ENGINE_REPO)
    assert wi.BROKEN in states(found)


def test_missing_summary_wiring_is_broken():
    found = wi.findings(good_files(**{"summary.yml": ...}), ENGINE_REPO)
    assert states(found) == [wi.BROKEN]
    assert "workflow_run" in joined(found)


@pytest.mark.parametrize(
    "workflows_line",
    [
        '    workflows: ["shipmate · plan"]',
        "    workflows: ['shipmate · plan']",
        "    workflows:\n      - shipmate · plan",
        '    workflows: [\n      "shipmate · plan",\n    ]',
        '    workflows: ["other", "shipmate · plan"]',
    ],
)
def test_every_workflows_spelling_is_accepted(workflows_line):
    """A flow sequence, a multi-line flow sequence and a block sequence are the
    same value to GitHub; a check that accepted only one shape would warn on
    correctly-wired repositories."""
    summary = (
        GOOD_SUMMARY.decode().replace('    workflows: ["shipmate · plan"]', workflows_line).encode()
    )
    assert wi.findings(good_files(**{"summary.yml": summary}), ENGINE_REPO) == []


def test_the_wiring_file_need_not_be_named_summary_yml():
    """GitHub matches nothing on the consumer's filename, so requiring it would
    manufacture a warning for a working repository."""
    files = good_files(**{"summary.yml": ...})
    files["gate.yml"] = GOOD_SUMMARY
    assert wi.findings(files, ENGINE_REPO) == []


def test_a_workflow_run_trigger_without_the_plan_name_does_not_qualify():
    summary = GOOD_SUMMARY.decode().replace("shipmate · plan", "something else").encode()
    found = wi.findings(good_files(**{"summary.yml": summary}), ENGINE_REPO)
    assert wi.BROKEN in states(found)


def test_the_plan_name_without_a_workflow_run_trigger_does_not_qualify():
    summary = GOOD_SUMMARY.decode().replace("workflow_run", "workflow_dispatch").encode()
    found = wi.findings(good_files(**{"summary.yml": summary}), ENGINE_REPO)
    assert wi.BROKEN in states(found)


def test_a_wiring_file_that_calls_another_orgs_summary_does_not_qualify():
    summary = GOOD_SUMMARY.decode().replace("acme/shipmate/", "someone/else/").encode()
    found = wi.findings(good_files(**{"summary.yml": summary}), ENGINE_REPO)
    assert wi.BROKEN in states(found)


def test_an_unknown_engine_repo_accepts_any_slug():
    """`github.action_repository` is empty when the action runs from a local
    path; the check errs toward passing rather than warning. It does not follow
    that such a repository passes overall -- a vendored engine's wrapper uses a
    relative `./.github/workflows/summary.yml` with no `@ref`, which this
    matcher misses whatever the slug pattern is. Vendoring is unsupported; this
    fixture is a slug-bearing wrapper with the engine unknown."""
    summary = GOOD_SUMMARY.decode().replace("acme/shipmate/", "someone/else/").encode()
    assert wi.findings(good_files(**{"summary.yml": summary}), "") == []


def test_a_commented_out_uses_line_does_not_qualify():
    summary = (
        GOOD_SUMMARY.decode()
        .replace("    uses: acme/shipmate", "    # uses: acme/shipmate")
        .encode()
    )
    found = wi.findings(good_files(**{"summary.yml": summary}), ENGINE_REPO)
    assert wi.BROKEN in states(found)


def test_the_plan_name_inside_a_comment_does_not_qualify():
    summary = (
        GOOD_SUMMARY.decode()
        .replace(
            '    workflows: ["shipmate · plan"]',
            '    workflows: ["nope"]  # was shipmate · plan',
        )
        .encode()
    )
    found = wi.findings(good_files(**{"summary.yml": summary}), ENGINE_REPO)
    assert wi.BROKEN in states(found)


def test_a_blank_line_or_a_column_zero_comment_does_not_end_the_on_block():
    """`_on_block` promises neither ends the block, and the sample wrappers put a
    column-0 comment block immediately above `on:` -- the same author style one
    line lower would otherwise read as an empty trigger block and warn."""
    summary = (
        GOOD_SUMMARY.decode()
        .replace(
            "on:\n  workflow_run:",
            "on:\n\n# runs at the default-branch ref\n  workflow_run:",
        )
        .encode()
    )
    assert wi.findings(good_files(**{"summary.yml": summary}), ENGINE_REPO) == []


def test_an_escaped_plan_name_in_the_wiring_degrades_rather_than_warns():
    """The name half degrades on `"shipmate \\u00b7 plan"` because it is the plan
    name spelled the long way; the wiring half compares the same literal against
    the same spelling and must reach the same answer."""
    summary = GOOD_SUMMARY.decode().replace('"shipmate · plan"', '"shipmate \\u00b7 plan"').encode()
    found = wi.findings(good_files(**{"summary.yml": summary}), ENGINE_REPO)
    assert states(found) == [wi.UNKNOWN]


def test_a_mixed_case_engine_slug_is_accepted():
    """GitHub resolves a `uses:` owner/repo case-insensitively, so `Acme/Shipmate`
    is the engine and warning on it would fail every pull request in a repository
    whose wiring works."""
    summary = GOOD_SUMMARY.decode().replace("acme/shipmate/", "Acme/Shipmate/").encode()
    assert wi.findings(good_files(**{"summary.yml": summary}), ENGINE_REPO) == []


def test_a_mixed_case_workflow_path_is_not_accepted():
    """Only the slug is case-insensitive. The path after it is a file in the
    engine repository, and `Summary.yml` is not that file."""
    summary = GOOD_SUMMARY.decode().replace("/summary.yml@", "/Summary.yml@").encode()
    found = wi.findings(good_files(**{"summary.yml": summary}), ENGINE_REPO)
    assert wi.BROKEN in states(found)


def test_the_no_wiring_remediation_points_at_something_that_exists():
    """This is the sentence a consumer reads when every one of their pull requests
    has just started failing. README.md carries no `uses:` snippet to copy, so the
    message points at the sample repositories and at the contract section that
    states the wrapper's two halves -- both of which have to be there."""
    _, message = wi.NO_WIRING
    readme = (ENGINE / "README.md").read_text(encoding="utf-8")
    contract = (ENGINE / "CONTRACT.md").read_text(encoding="utf-8")
    samples = "repo-example-{stacks,folders,workspaces}"
    assert samples in message and samples in readme
    assert "README.md §Example repositories" in message
    assert "\n## Example repositories\n" in readme
    assert "CONTRACT.md §Post-plan topology" in message
    assert "\n## Post-plan topology\n" in contract


def test_the_plan_name_literal_appears_in_every_document_that_states_it():
    """The other half of row 33's coupling. `PLAN_PATH` is source-derived
    against the engine workflow above; `PLAN_NAME` has no such counterpart in
    engine code, so its only other sides are the three documents that tell a
    consumer what to write. A look-alike separator renders identically, so a
    document drifting from the constant is invisible on the page."""
    for name in ("CONTRACT.md", "README.md", "docs/branch-protection.md"):
        text = (ENGINE / name).read_text(encoding="utf-8")
        assert wi.PLAN_NAME in text, f"{name} no longer spells wiring.PLAN_NAME"


def test_an_empty_workflows_directory_is_broken_twice():
    """A readable but empty `.github/workflows` is not a degrade: the listing
    was read, and it really does hold no plan workflow and no wrapper. Both
    findings must be reported -- a consumer told only about the missing
    `plan.yml` would add it and get a second failing pull request."""
    found = wi.findings({}, ENGINE_REPO)
    assert found == [wi.BAD_PATH, wi.NO_WIRING]


def test_an_unreadable_listing_degrades():
    found = wi.findings(None, ENGINE_REPO)
    assert states(found) == [wi.UNKNOWN]


def test_an_unreadable_plan_file_degrades():
    found = wi.findings(good_files(**{"plan.yml": None}), ENGINE_REPO)
    assert wi.BROKEN not in states(found)
    assert wi.UNKNOWN in states(found)


def test_an_unreadable_other_file_degrades_the_wiring_check():
    """The unreadable file might have been the one that wired the summary."""
    files = good_files(**{"summary.yml": ...})
    files["mystery.yml"] = None
    found = wi.findings(files, ENGINE_REPO)
    assert states(found) == [wi.UNKNOWN]


def test_an_unreadable_file_does_not_mask_a_satisfied_wiring_check():
    files = good_files()
    files["mystery.yml"] = None
    assert wi.findings(files, ENGINE_REPO) == []


def test_invalid_utf8_decodes_to_a_replacement_character():
    """`errors="replace"` is what turns a latin-1 middot into U+FFFD, which is
    what makes the byte comparison honest. `errors="ignore"` would drop the byte
    and leave `shipmate  plan`, which is still not PLAN_NAME -- but it would also
    silently swallow the evidence the message is supposed to show."""
    assert wi.decode(b"shipmate \xb7 plan") == "shipmate � plan"


def test_strip_comment_ends_a_line_at_a_comment():
    assert wi._strip_comment('workflows: ["x"]  # was shipmate · plan') == 'workflows: ["x"]  '
    assert wi._strip_comment("# whole line") == ""


def test_strip_comment_keeps_a_hash_inside_a_token():
    """The docstring promises a `#` inside a token is not a comment marker.
    Nothing else in the suite exercises that half, and a matcher that stripped
    at every `#` would still pass every other test here."""
    assert wi._strip_comment("branches: [release#1]") == "branches: [release#1]"


def test_the_plan_path_matches_the_engine_summary_guard():
    """Source-derived coupling guard. The engine's reusable `summary.yml` skips
    every job unless `github.event.workflow_run.path` equals this exact string;
    if that guard and this module's literal drift apart, the probe passes a
    repository the engine will refuse.

    Reads the `if:` expression out of the workflow rather than grepping the
    file, so the literal appearing only in a comment cannot satisfy it.
    """
    import yaml

    spec = yaml.safe_load(
        (ENGINE / ".github" / "workflows" / "summary.yml").read_text(encoding="utf-8")
    )
    guard = spec["jobs"]["summary"]["if"]
    assert f"workflow_run.path == '{wi.PLAN_PATH}'" in " ".join(guard.split()), (
        "the engine summary.yml path guard no longer matches wiring.PLAN_PATH"
    )

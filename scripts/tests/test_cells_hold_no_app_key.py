"""The cell actions run tofu over pull-request branch content (providers,
`external` data sources, modules), so they must hold no App key -- the
credentialed work lives in the trusted trailing jobs.

One parsed-assertion implementation covers both cells: `apply-cell` (which runs
`tofu apply`) and `drift-cell` (which runs `tofu plan` from a policy-free plan
environment reachable off any branch).

Every assertion is on the PARSED action.yml. The raw-text form this replaces
(`"private-key" not in text`, `"create-github-app-token" not in text`) was
vacuous twice over: a comment naming either one satisfied it, and the actual way
a cell would regain the key -- a direct `secrets.SHIPMATE_APP_PRIVATE_KEY`
reference in an `env:` value -- contains neither substring.
"""

import pytest
import yaml
from _loader import WORKFLOWS, action_steps, action_yaml

CELLS = ("apply-cell", "drift-cell")

#: `uses:` repos that mint a GitHub App installation token. Matched on the repo
#: part alone, so a version bump or a SHA re-pin cannot slip past.
TOKEN_MINTERS = (
    "actions/create-github-app-token",
    "tibdex/github-app-token",
    "getsentry/action-github-app-token",
    "peter-murray/workflow-application-token-action",
)

APP_KEY_ENV = "SHIPMATE_APP_PRIVATE_KEY"

LEVEL = WORKFLOWS / "apply-env-level.yml"


def _strings(node):
    """Every scalar string reachable in a parsed subtree, mapping keys included.

    Walking the parsed tree rather than the file text is the point: an `env:`
    value, a `with:` value and a `run:` body are all reached, while a YAML
    comment -- which `safe_load` discards -- can no longer satisfy the guard.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _strings(key)
            yield from _strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item)
    elif isinstance(node, str):
        yield node


@pytest.mark.parametrize("action", CELLS)
def test_cell_declares_no_private_key_input(action):
    inputs = action_yaml(action).get("inputs") or {}
    assert "private-key" not in inputs, f"{action} inputs: {sorted(inputs)}"


@pytest.mark.parametrize("action", CELLS)
def test_cell_uses_no_token_minting_action(action):
    steps = action_steps(action)
    assert steps, f"{action} parsed to zero steps -- this guard would assert nothing"
    for step in steps:
        repo = str(step.get("uses", "")).split("@")[0].strip().lower()
        assert repo not in TOKEN_MINTERS, f"{action} step {step.get('name')!r} uses {repo}"


@pytest.mark.parametrize("action", CELLS)
def test_cell_never_references_the_app_private_key(action):
    steps = action_steps(action)
    assert steps, f"{action} parsed to zero steps -- this guard would assert nothing"
    for text in _strings(steps):
        assert APP_KEY_ENV not in text, f"{action} references {APP_KEY_ENV}: {text!r}"


def test_only_the_completer_job_reads_the_app_key():
    # Parse, don't string-split: the secret stays DECLARED at the top of the
    # file (the complete job consumes it, and test_gate_name_consistency
    # requires reusable targets called with `secrets: inherit` to declare it),
    # so a textual "not in" assertion would be checking the wrong thing.
    doc = yaml.safe_load(LEVEL.read_text(encoding="utf-8"))
    for name, job in doc["jobs"].items():
        body = yaml.safe_dump(job)
        if name == "complete":
            assert APP_KEY_ENV in body
        else:
            assert APP_KEY_ENV not in body, name


def test_the_completer_is_the_only_job_naming_the_engine_environment():
    # Parsed, not a `text.count(...) == 1` textual check: that form is
    # satisfied by ANY single job naming the environment, so moving it onto
    # e.g. wave0 (which runs tofu over branch content) would keep the count at
    # 1 and the test green while releasing the key to consumer code -- the
    # exact thing this task exists to prevent. Assert the specific job, not
    # just the count.
    doc = yaml.safe_load(LEVEL.read_text(encoding="utf-8"))
    named = {n for n, j in doc["jobs"].items() if j.get("environment") == "shipmate-engine"}
    assert named == {"complete"}

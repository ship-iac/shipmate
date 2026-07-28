"""Every App-token mint identifies the App with `client-id`, never `app-id`.

`actions/create-github-app-token` declares `app-id` with a `deprecationMessage`,
so the runner logs a warning for every mint that passes that key — and, because
`shipmate doctor` harvests the warning annotations GitHub records on a commit's
workflow runs, those warnings surface in the doctor report on every commit.

The rename is key-only, not value-only. Upstream resolves the two inputs as
`getInput("client-id") || getInput("app-id")` and passes the result straight
through as the JWT `iss` claim, which GitHub accepts as either the App ID or the
Client ID. So `client-id: <numeric App ID>` mints exactly the token `app-id:
<numeric App ID>` minted, and consumers keep supplying one App-identity value.

Two things must therefore hold together, and this module pins both:

1. No mint step carries the `app-id` key at all. The runner warns on the
   *presence* of a deprecated key in `with:`, not on its value, so blanking it
   while leaving the key in place would still log the warning.
2. Every mint step passes `client-id`, threading the App-identity value the
   engine already has. A mint with neither key fails at runtime with
   upstream's "must be set to a non-empty string".

`app-id` remains the spelling of the engine's *own* public action input, which
consumers pass as `SHIPMATE_APP_ID`; that name is shipmate's, not the
third-party action's, and `test_gate_name_consistency` pins its threading.

**Read this before bumping the `actions/create-github-app-token` pin.** What
makes the rename safe is upstream's *internal* resolution, not its documented
contract: `client-id` is described upstream as the App's Client ID (`Iv23li…`),
and the engine supplies the numeric App ID under that key. Upstream could add a
format check on `client-id`, or resolve `app-id` first, without considering it a
breaking change — and every mint in the engine would fail at once (no
`shipmate / gate` status, so every pull request is unmergeable; no apply checks;
`shipmate apply` and `doctor` silent). These tests cannot catch that: they read
the engine's YAML, not the pinned action's code, and the deprecation warning that
used to be the early signal is gone precisely because of this change. So on any
bump, re-read the new version's input resolution and confirm the value still
reaches the JWT `iss` claim unvalidated before merging.
"""

import pathlib

import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_ACTIONS = _ROOT / "actions"
_GITHUB = _ROOT / ".github"

_MINT = "actions/create-github-app-token"

# The App-identity expressions the engine legitimately threads into a mint: a
# composite action reads its own `app-id` input, a workflow reads the repository
# variable consumers set. Pinned as a closed set so a mint cannot quietly start
# identifying the App from some other, unreviewed source.
_IDENTITY_VALUES = frozenset(
    {
        "${{ inputs.app-id }}",
        "${{ vars.SHIPMATE_APP_ID }}",
    }
)


def _engine_yaml():
    """Every YAML file under `actions/` and `.github/`, recursively.

    Deliberately not a narrow `actions/*/action.yml` + `.github/workflows/*.yml`
    pair. GitHub accepts a mint in shapes those globs miss -- `action.yaml`, a
    nested `actions/<a>/<b>/action.yml`, `.github/actions/<a>/action.yml`, a
    `.yaml`-spelled workflow -- and a guard that silently skips a file is worse
    than no guard, since it reads as coverage. Sweeping every YAML in both trees
    costs nothing: a file with no mint step contributes nothing.
    """
    for root in (_ACTIONS, _GITHUB):
        yield from sorted(root.rglob("*.y*ml"))


def _mints_in(steps, label):
    return [(label, s) for s in steps or [] if _MINT in (s.get("uses") or "")]


def _mint_steps():
    """Every `create-github-app-token` step in the engine, as
    (source label, step dict) pairs.

    Reads both shapes out of each document -- a composite action's
    `runs.steps` and a workflow's `jobs.<name>.steps` -- so which shape a mint
    lives in never decides whether it is checked.
    """
    found = []
    for path in _engine_yaml():
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            continue
        label = path.relative_to(_ROOT).as_posix()
        found += _mints_in((doc.get("runs") or {}).get("steps"), label)
        for job_name, job in (doc.get("jobs") or {}).items():
            if isinstance(job, dict):
                found += _mints_in(job.get("steps"), f"{label}:{job_name}")
    return found


def test_the_engine_has_mint_sites_to_check():
    """A refactor that moved every mint somewhere this sweep cannot see would
    otherwise make the guards below vacuously pass.

    Asserts non-emptiness only. A count floor would also red when an action
    legitimately stops needing an App token, under a message about the sweep
    being broken -- a false diagnosis, and the wrong test to edit in that PR.
    """
    assert _mint_steps(), "the mint sweep found no App-token mints at all"


def test_no_mint_passes_the_deprecated_app_id_key():
    offenders = [label for label, step in _mint_steps() if "app-id" in (step.get("with") or {})]
    assert not offenders, (
        "mint step(s) still pass the deprecated `app-id` key, which logs a "
        f"runner warning that doctor then harvests: {offenders}"
    )


def test_every_mint_identifies_the_app_with_client_id():
    offenders = []
    for label, step in _mint_steps():
        got = (step.get("with") or {}).get("client-id")
        if got not in _IDENTITY_VALUES:
            offenders.append(f"{label}: client-id is {got!r}")
    assert not offenders, (
        "every mint must identify the App via `client-id`, threading an "
        f"App-identity value from {sorted(_IDENTITY_VALUES)}: {offenders}"
    )

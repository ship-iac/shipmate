"""Shared test-side helpers: load a ``scripts/`` helper, read an engine YAML file or a docs code
fence, or run a shipped shell body.

Four jobs: ``load_script`` for the extension-less helpers;
``ENGINE``/``ACTIONS``/``WORKFLOWS`` plus ``action_yaml``, ``workflow_yaml``, ``action_steps`` and
``step_by`` for the YAML-shape guards; ``doc_fences`` and ``assert_every_fence_discovered`` for
the docs fence guards; and ``bash_only``, ``run_step``, ``write_python3_shim`` and
``run_with_gh_recorder`` for the tests that execute a step's bash. The parser is load-bearing,
because a guard that silently parses to ``[]`` asserts nothing, so it has one definition.

Loading a helper script
-----------------------

``load_script`` is the production loader ``scripts/_shipmate.py`` under the name the test
modules use; its docstring states why the loader is shaped as it is. The helpers carry no
``.py`` suffix and cannot be imported by name.

Importable from every test module because ``scripts/tests`` is on the pytest ``pythonpath``
(``pyproject.toml``). Prepend import mode would put this directory on ``sys.path`` anyway, there
being no ``__init__.py`` here, but that is pytest's default behaviour rather than a declared
invariant, and 20 modules failing collection is a poor way to discover someone changed the import
mode.
"""

import copy
import functools
import os
import pathlib
import shutil
import subprocess
import sys
import textwrap

import pytest
from _shipmate import _load, load_workflow_text

load_script = _load
_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]

#: The engine repo root, and the trees the source-derived guards read.
ENGINE = _SCRIPTS.parent
SCRIPTS = _SCRIPTS
ACTIONS = ENGINE / "actions"
WORKFLOWS = ENGINE / ".github" / "workflows"


def local_action(name):
    """The `uses:` an engine workflow or composite action writes for engine action `name`."""
    return f"$/actions/{name}"


_APP_KEY = {"SHIPMATE_APP_PRIVATE_KEY": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}"}
_CONSUMER_SECRETS = {"SHIPMATE_SECRETS": "${{ secrets.SHIPMATE_SECRETS }}"}
_APP_KEY_AND_SECRETS = {**_APP_KEY, **_CONSUMER_SECRETS}
_APP_KEY_AND_PASSPHRASE = {
    "SHIPMATE_APP_PRIVATE_KEY": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
    "SHIPMATE_PLAN_PASSPHRASE": "${{ secrets.SHIPMATE_PLAN_PASSPHRASE }}",
}
_APP_KEY_PASSPHRASE_AND_SECRETS = {**_APP_KEY_AND_PASSPHRASE, **_CONSUMER_SECRETS}

#: The whole `secrets:` block every caller of an engine reusable workflow must write, keyed by
#: callee file name. Hand-written, never derived from the callee's own declarations: a guard that
#: reads them back passes whatever the file says --
#: `test_docs_yaml_parses.py` compares this hand-written registry *against* each callee, which
#: is the opposite direction. Two rules meet here. `secrets: inherit`
#: delivers nothing across an organization boundary and suppresses what the callee's
#: `environment:` would otherwise supply. Mapping a secret the callee does not declare is a
#: load-time failure, which is why each entry is the callee's exact declaration set: `plan.yml`
#: encrypts plan artifacts and mints the gate, so it takes both engine secrets; `drift.yml`
#: mints but encrypts nothing, so it takes no passphrase; `comment-ops.yml` runs no cell, so it
#: and `apply-review.yml`, which only reads a review decision, are the entries taking no consumer
#: envelope. Every other callee runs a cell, and
#: `SHIPMATE_SECRETS` is how a consumer's own secrets reach it.
ENGINE_CALL_SECRETS = {
    "plan.yml": _APP_KEY_PASSPHRASE_AND_SECRETS,
    "drift.yml": _APP_KEY_AND_SECRETS,
    "comment-ops.yml": _APP_KEY,
    "apply.yml": _APP_KEY_PASSPHRASE_AND_SECRETS,
    "deploy.yml": _APP_KEY_PASSPHRASE_AND_SECRETS,
    "apply-env-level.yml": _APP_KEY_PASSPHRASE_AND_SECRETS,
    "apply-review.yml": _APP_KEY,
    # Unlock reads no plan artifact and mints no App token, so the consumer envelope is the
    # whole block: `unlock-cell` runs `tofu init`, which a consumer's backend may configure
    # from a plain TF_VAR_*.
    "unlock.yml": _CONSUMER_SECRETS,
}


@functools.cache
def _parse_yaml(path):
    """Parsed engine YAML file, cached: nothing in the suite rewrites these files."""
    spec = load_workflow_text(path.read_text(encoding="utf-8"))
    # Never fall back to ``{}``: a file that parses to None, emptied by a bad merge or fully
    # commented out, would hand every guard zero steps, and a guard over zero steps passes while
    # asserting nothing.
    assert isinstance(spec, dict), (
        f"{path} did not parse to a mapping ({spec!r}) -- guards derived from it "
        "would assert nothing"
    )
    return spec


def action_yaml(action):
    """Parsed ``action.yml`` for ``action``: a composite action's name under ``actions/``, or a
    path to the file itself, which is what a parametrized guard iterating
    ``ACTIONS.glob("*/action.yml")`` already holds.

    A deep copy per call. The parse is cached, so handing out the cached object would let the
    first guard that normalizes what it was given -- dedenting a `run:`, sorting steps --
    silently rewrite what every later module asserts against, a false green that only reproduces
    in a full-suite run.
    """
    path = action if isinstance(action, pathlib.Path) else ACTIONS / action / "action.yml"
    return copy.deepcopy(_parse_yaml(path))


def workflow_yaml(workflow):
    """Parsed engine workflow: a file name under ``.github/workflows/``, or a path to it. A deep
    copy per call, for the reason ``action_yaml`` gives."""
    path = workflow if isinstance(workflow, pathlib.Path) else WORKFLOWS / workflow
    return copy.deepcopy(_parse_yaml(path))


def action_steps(action):
    """``runs.steps`` for ``action``, or ``[]`` for one that declares none. A non-composite
    action is legal, and has no bash for a guard to read."""
    return (action_yaml(action).get("runs") or {}).get("steps") or []


def step_by(action, *, name=None, id=None):
    """The one step of ``action`` whose ``name``, or ``id``, equals the value given.

    Exactly one, asserted: a guard that takes the first of two same-named steps pins one and
    leaves the other free, and one that finds none must fail rather than check nothing.
    """
    assert (name is None) != (id is None), "pass exactly one of name= and id="
    key, value = ("name", name) if id is None else ("id", id)
    matches = [s for s in action_steps(action) if s.get(key) == value]
    assert len(matches) == 1, (
        f"{action}: expected exactly one step with {key} {value!r}, got {len(matches)}"
    )
    return matches[0]


def run_lines(step):
    """Stripped, non-empty, non-comment lines of ``step``'s ``run:``.

    Match a whole line against this rather than a substring of the raw block: a substring is
    satisfied by a commented-out invocation, which is the accidental regression a
    "the step still calls the script" guard exists to catch.
    """
    return [
        ln.strip()
        for ln in (step.get("run") or "").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def doc_fences(pages, fence):
    """Every code fence ``fence`` pairs in ``pages``, as (page, 1-based line of its opener,
    dedented body).

    ``fence`` is the caller's compiled pattern with a ``body`` group, because what counts as a
    fence differs per language and, for ```yaml, is owned by ``scripts/onboard``. Indented
    fences, those inside a list item, are dedented rather than skipped: a fence this misses is a
    fence nothing checks.
    """
    for page in pages:
        text = page.read_text(encoding="utf-8")
        for m in fence.finditer(text):
            yield page, text[: m.start()].count("\n") + 1, textwrap.dedent(m.group("body"))


#: The first line of a ```yaml fence that holds a `.github/shipmate-config.yml` example.
CONFIG_FENCE_MARKER = "# .github/shipmate-config.yml"


def is_config_fence(body):
    """Whether a dedented ```yaml fence body is a config example rather than a workflow."""
    return body.partition("\n")[0] == CONFIG_FENCE_MARKER


def assert_every_fence_discovered(pages, fences, opener, lang):
    """Fail unless ``fences`` holds one entry per ``opener`` match in ``pages``.

    A fence pattern skips whatever it cannot pair, so a relabelled opener, an info string or a
    mangled closing delimiter would drop a fence out of a parametrization and leave the suite
    green over an unchecked example. ``opener`` counts openers as a reader sees them, not as the
    fence pattern pairs them.
    """
    where = " + ".join(p.relative_to(ENGINE).as_posix() for p in pages)
    openers = sum(len(opener.findall(p.read_text(encoding="utf-8"))) for p in pages)
    # A discovery bug that finds nothing parametrizes zero cases and checks nothing, which is
    # green either way without this.
    assert openers > 0, f"found no ```{lang} fence openers in {where}"
    assert len(fences) == openers, (
        f"{openers} ```{lang} fence openers but only {len(fences)} paired into checkable "
        "fences -- the unpaired ones are not parsed by anything (relabelled opener, info "
        f"string, or a broken closing delimiter); pages: {where}"
    )


@functools.cache
def usable_bash():
    """Path to a bash that actually runs, or None: what the tests that execute an action's shell
    body skip on.

    ``which("bash")`` alone is not enough on Windows. The Store and WSL ``bash.exe`` execution
    alias sits on PATH by default and answers every invocation with a UTF-16 error on stdout and
    a non-zero exit, which a test trusting it reads as the shell body misbehaving, so each
    candidate is probed before it is used. Linux CI takes the first candidate and never reaches
    the fallback.
    """
    for cand in (shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe"):
        if not cand or not pathlib.Path(cand).exists():
            continue
        try:
            probe = subprocess.run(
                [cand, "-c", "printf ok"], capture_output=True, text=True, timeout=30
            )
        except OSError:
            continue
        if probe.returncode == 0 and probe.stdout.strip() == "ok":
            return cand
    return None


#: Marks a test that executes a shipped shell body. `conftest.py` skips it on a host with no
#: working bash, probing only when a marked test runs rather than at every import.
bash_only = pytest.mark.bash_only


#: The Python half of `write_python3_shim`'s `python3`; `{bash}` and `{gh}` are path literals.
_PYTHON3_SHIM = """\
import os, runpy, subprocess, sys

_run = subprocess.run


def _gh_through_bash(args, *rest, **kwargs):
    if isinstance(args, (list, tuple)) and args and args[0] == "gh":
        args = [{bash}, {gh}, *args[1:]]
    return _run(args, *rest, **kwargs)


subprocess.run = _gh_through_bash
script = sys.argv[1]
sys.argv = [script, *sys.argv[2:]]
sys.path[0] = os.path.dirname(os.path.abspath(script))
runpy.run_path(script, run_name="__main__")
"""


def write_python3_shim(bin_dir):
    """Write a ``python3`` into ``bin_dir`` that runs a script with every ``subprocess.run``
    argv starting ``"gh"`` sent to ``bash bin_dir/gh`` instead.

    The caller puts ``bin_dir`` first on PATH and writes its own bash ``gh`` stub there. A PATH
    stub alone cannot reach a ``gh`` that Python spawns on Windows: the spawn resolves the real
    ``gh.EXE`` past it, and an extensionless stub run by full path raises FileNotFoundError.
    ``sys.argv`` and ``sys.path[0]`` are set as ``python3 script`` sets them, so the script reads
    only its own arguments and imports its siblings with no PYTHONPATH. Only that
    ``python3 <script> args...`` form runs; ``python3 -c`` and ``python3 -`` do not.
    """
    bash = usable_bash()
    assert bash is not None, "callers are bash_only-gated"
    shim = bin_dir / "python3_shim.py"
    shim.write_text(
        _PYTHON3_SHIM.format(bash=repr(bash), gh=repr(str(bin_dir / "gh"))), encoding="utf-8"
    )
    python3 = bin_dir / "python3"
    python3.write_text(
        f'#!/bin/bash\nexec "{sys.executable}" "{shim}" "$@"\n', encoding="utf-8", newline="\n"
    )
    python3.chmod(0o755)


#: A bash ``gh`` for ``run_with_gh_recorder``: it appends each call's argv to ``$CALLS``, one line
#: per argument and ``--`` after each call, answers a ``--paginate`` listing with
#: ``$FAKE_LISTING`` and exit ``$FAKE_LIST_RC``, and any other call with JSON and
#: ``$FAKE_WRITE_RC``.
_GH_RECORDER = """\
#!/bin/bash
printf '%s\\n' "$@" -- >> "$CALLS"
if [ "$2" = "--paginate" ]; then
  printf '%s' "${FAKE_LISTING:-}"
  exit "${FAKE_LIST_RC:-0}"
fi
echo '{"id": 99}'
exit "${FAKE_WRITE_RC:-0}"
"""


def run_with_gh_recorder(tmp_path, body, env, *, listing="", list_rc=0, write_rc=0):
    """``run_step`` ``body`` with ``write_python3_shim``'s ``python3`` and a recording ``gh`` first
    on PATH; return (process, the argv after ``gh`` of each call, in order).

    ``listing`` is what a ``--paginate`` call prints before exiting ``list_rc``; every other call
    exits ``write_rc``. ``env`` is extended, not replaced, with PATH and the stub's variables.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    write_python3_shim(bin_dir)
    (bin_dir / "gh").write_text(_GH_RECORDER, encoding="utf-8", newline="\n")
    calls = tmp_path / "gh-calls"
    env = {
        **env,
        "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
        "CALLS": str(calls),
        "FAKE_LISTING": listing,
        "FAKE_LIST_RC": str(list_rc),
        "FAKE_WRITE_RC": str(write_rc),
    }
    proc = run_step(tmp_path, body, env)
    recorded, current = [], []
    for line in calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []:
        if line == "--":
            recorded.append(current)
            current = []
        else:
            current.append(line)
    return proc, recorded


def run_step(tmp_path, body, env, *, cwd=None, timeout=30):
    """Run ``body`` as a bash script written under ``tmp_path``; return the CompletedProcess.

    ``env`` is the whole environment, passed as given: a test that wants the host's variables
    spreads ``os.environ`` into it, and a hermetic one does not. The script is written with LF
    endings, because bash reads a CR as part of the command. The run's cwd is ``cwd``, or
    ``tmp_path`` when that is not given. Output is decoded as UTF-8, not the locale default: the
    shipped messages carry em dashes, and a cp1252 decode mangles them into a mismatch that looks
    like a real diff.
    """
    bash = usable_bash()
    assert bash is not None, "callers are bash_only-gated"
    script = tmp_path / "step.sh"
    script.write_text(body, encoding="utf-8", newline="\n")
    return subprocess.run(
        [bash, str(script)],
        cwd=cwd or tmp_path,
        env=env,
        capture_output=True,
        encoding="utf-8",
        timeout=timeout,
    )

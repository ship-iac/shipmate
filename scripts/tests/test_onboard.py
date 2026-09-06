"""Unit tests for `scripts/onboard`, the per-repository reconciler.

Threat model: accidental regression -- a dropped API call, an inverted dry-run
branch, a case-sensitive comparison against an API that uppercases names. Not a
hostile edit to this repository, which is reviewed on every pull request.
"""

import ast
import json
import subprocess
import sys

import pytest
from _loader import ENGINE, load_script

onboard = load_script("onboard")


def make_gh(routes):
    """A fake `_run`: records every invocation, answers reads from `routes`,
    returns "" for every write.

    A read is `gh api <path>` with no `-X`; anything carrying `-X` is a write and is
    only recorded. An unrouted read raises `AssertionError`, deliberately NOT
    `SystemExit`: `_gh_json_or_none` catches `SystemExit` as "absent", so a
    `SystemExit` here would be swallowed into the absent branch and the test would
    pass while the implementation wrote against a read nobody stubbed.

    A route whose value is a `SystemExit` instance is the 404 case.
    """
    calls = []

    def _run(args, secrets=(), stdin=None):
        calls.append(list(args))
        if args[:2] == ["gh", "api"] and "-X" not in args:
            path = args[2]
            if path not in routes:
                raise AssertionError(f"unrouted read: {path}")
            answer = routes[path]
            if isinstance(answer, SystemExit):
                raise answer
            return json.dumps(answer)
        return ""

    _run.calls = calls
    return _run


@pytest.fixture(autouse=True)
def _reset_module_state():
    """`REPORT` and `_DRY` are module-level. Without this, one test's dry-run flag or
    report lines leak into the next and the suite passes or fails on order."""
    onboard.REPORT.clear()
    onboard._DRY = False
    yield
    onboard.REPORT.clear()
    onboard._DRY = False


def test_engine_pin_refuses_an_untagged_head(monkeypatch, tmp_path):
    """A consumer must never be pinned to a commit with no release: doctor's pin
    probe reports that as staleness and tells the consumer to re-pin backwards.

    Mutation: make `_engine_pin` fall through to returning the SHA with an empty
    version when `git tag --points-at HEAD` prints nothing.
    """
    monkeypatch.setattr(
        onboard,
        "_run",
        lambda args, secrets=(), stdin=None: "a" * 40 if "rev-parse" in args else "\n",
    )
    with pytest.raises(SystemExit) as e:
        onboard._engine_pin(tmp_path)
    assert "docs/releasing.md" in str(e.value)


def test_engine_pin_takes_the_v_tag(monkeypatch, tmp_path):
    """A tagged HEAD yields (sha, tag). Mutation: return the first line of
    `git tag --points-at` unconditionally, so a repository carrying a non-release
    tag first is pinned with that tag as its version comment.
    """
    monkeypatch.setattr(
        onboard,
        "_run",
        lambda args, secrets=(), stdin=None: (
            "b" * 40 if "rev-parse" in args else "nightly\nv0.26.0\n"
        ),
    )
    assert onboard._engine_pin(tmp_path) == ("b" * 40, "v0.26.0")


def test_empty_key_file_is_refused(tmp_path):
    """An empty PEM must fail before any write, not after half the repository is
    configured. Mutation: drop the `.strip()` so a whitespace-only file passes.
    """
    pem = tmp_path / "key.pem"
    pem.write_text("   \n", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        onboard._read_key(pem)
    assert "empty" in str(e.value)


def test_shared_flag_conflicting_with_the_variable_is_refused():
    """Two sources for one value, silently disagreeing, would bind the wrong
    environments. Mutation: make the conflict a report instead of a refusal.
    """
    with pytest.raises(SystemExit) as e:
        onboard._resolve_shared("dev-eu", {"SHIPMATE_SHARED_ENVS": "dev-us"}, ["dev-eu", "dev-us"])
    assert "SHIPMATE_SHARED_ENVS" in str(e.value)


def test_shared_falls_back_to_the_variable():
    """Mutation: return the flag value when it is empty, so an already-shared
    repository silently reverts to split mode."""
    assert onboard._resolve_shared(
        "", {"SHIPMATE_SHARED_ENVS": "dev-us, dev-eu"}, ["dev-eu", "dev-us"]
    ) == {"dev-eu", "dev-us"}


def test_zero_environments_is_refused(monkeypatch):
    """A repository whose stacks carry no env tag has nothing to bind; creating
    zero environments and reporting success is the fail-open form.

    Mutation: return an empty list instead of raising.
    """
    monkeypatch.setattr(onboard, "_env_membership", lambda: ({}, {}))
    with pytest.raises(SystemExit) as e:
        onboard._derive_envs()
    assert "no environment" in str(e.value)


def test_dry_run_issues_no_write(monkeypatch):
    """`--dry-run` reads and reports; it must not run a single write command.

    Mutation: make `write` fall through to `_run` when `_DRY` is set.
    """
    fake = make_gh({})
    monkeypatch.setattr(onboard, "_run", fake)
    monkeypatch.setattr(onboard, "_DRY", True)
    onboard.REPORT.clear()
    assert onboard.write("create", "thing", ["gh", "api", "-X", "PUT", "x"]) is False
    assert fake.calls == []
    assert onboard.REPORT == [("would create", "thing", "")]


def test_shared_name_outside_the_derived_environments_is_refused():
    """A typo in --shared binds nothing and reports success. Mutation: drop the
    membership check, so `dev-ue` is returned and every reconciler skips it.
    """
    with pytest.raises(SystemExit) as e:
        onboard._resolve_shared("dev-ue", {}, ["dev-eu"])
    assert "dev-ue" in str(e.value)


def test_shared_name_failing_the_environment_regex_is_refused():
    """Shared names reach the same API paths as derived ones. Mutation: drop the
    `_ENV_RE` check, so `../x` is interpolated into an environment path.
    """
    with pytest.raises(SystemExit) as e:
        onboard._resolve_shared("../x", {}, ["../x"])
    assert "unusable" in str(e.value)


def test_derived_environment_failing_the_regex_is_refused(monkeypatch):
    """An env/* tag becomes an API path segment and a `gh --env` argument.

    Mutation: drop the `_ENV_RE` loop, so `../admin` is returned as an environment.
    """
    monkeypatch.setattr(onboard, "_env_membership", lambda: ({"../admin": ["s"]}, {}))
    with pytest.raises(SystemExit) as e:
        onboard._derive_envs()
    assert "../admin" in str(e.value)


def test_only_the_exact_verb_differs_sets_exit_code_2():
    """The report verbs are an open set -- each reconciler names its own -- so the
    predicate is an equality on one string.

    Mutation: `verb == "differs"` to `"differs" in verb`, or to `verb != "differs"`;
    either reddens on the second case below.
    """
    onboard.REPORT.extend([("differs", "a", ""), ("would create", "b", ""), ("pin-only", "c", "")])
    assert onboard._exit_code() == 2
    onboard.REPORT.clear()
    onboard.REPORT.extend(
        [
            ("ok", "a", ""),
            ("would create", "b", ""),
            ("pin-only", "c", ""),
            ("no-differs-found", "d", ""),
        ]
    )
    assert onboard._exit_code() == 0


def test_absent_read_returns_none(monkeypatch):
    """A 404 is how every reconciler asks "does this exist yet".

    Mutation: let `_gh_json_or_none` propagate the `SystemExit` instead of
    returning None, so a fresh repository aborts on its first probe.
    """
    fake = make_gh({"repos/o/r/environments/dev-eu": SystemExit("gh: Not Found (HTTP 404)")})
    monkeypatch.setattr(onboard, "_run", fake)
    assert onboard._gh_json_or_none("repos/o/r/environments/dev-eu") is None
    assert fake.calls == [["gh", "api", "repos/o/r/environments/dev-eu"]]


def test_repo_facts_refuses_a_missing_default_branch(monkeypatch):
    """`gh repo view` outside a repository answers with nulls, and every later
    write would target the wrong place.

    Mutation: `.get("name") or ""` to `.get("name", "main")`, defaulting the branch.
    """
    monkeypatch.setattr(
        onboard,
        "_run",
        lambda args, secrets=(), stdin=None: json.dumps(
            {"nameWithOwner": "o/r", "defaultBranchRef": None}
        ),
    )
    with pytest.raises(SystemExit) as e:
        onboard._repo_facts()
    assert "default branch" in str(e.value)


def test_repo_facts_refuses_an_unusable_slug(monkeypatch):
    """The slug is interpolated into API paths. Mutation: drop the `_REPO_RE`
    check, so `../../o/r` reaches them.
    """
    monkeypatch.setattr(
        onboard,
        "_run",
        lambda args, secrets=(), stdin=None: json.dumps(
            {"nameWithOwner": "../../o/r", "defaultBranchRef": {"name": "main"}}
        ),
    )
    with pytest.raises(SystemExit) as e:
        onboard._repo_facts()
    assert "unusable repository slug" in str(e.value)


def test_run_passes_stdin_as_bytes_with_text_mode_off(monkeypatch):
    r"""`text=True` wraps the child's stdin in a `TextIOWrapper` that rewrites every
    \n to `os.linesep`, so the PEM stored on Windows is not the PEM that was read.

    The whole kwargs mapping is compared against a hand-written dict rather than the
    bytes a child observes: the translation only happens where `os.linesep` is
    `\r\n`, so an observed-bytes assertion is inert on the Linux runner CI uses.

    Mutation: add `text=True` back to `_run`'s `subprocess.run` call.
    """
    seen = {}

    def fake(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, b"out", b"")

    monkeypatch.setattr(onboard.subprocess, "run", fake)
    assert onboard._run(["gh", "secret", "set", "X"], stdin="-----BEGIN-----\nabc\n") == "out"
    assert seen["args"] == (["gh", "secret", "set", "X"],)
    assert seen["kwargs"] == {
        "capture_output": True,
        "input": b"-----BEGIN-----\nabc\n",
    }


def test_run_scrubs_secrets_from_a_failure():
    """The App key fails exactly when it has been minted but not yet stored, so the
    failure path must not echo it.

    Two mutations redden it: drop the `_scrub` call around the stderr, so the token
    appears verbatim; or drop the `raise` and return `p.stdout`, so a failed write is
    reported as done.
    """
    with pytest.raises(SystemExit) as e:
        onboard._run(
            [sys.executable, "-c", "import sys; sys.stderr.write('tok-abc'); sys.exit(3)"],
            secrets=("tok-abc",),
        )
    assert "tok-abc" not in str(e.value)
    assert onboard.REDACTED in str(e.value)
    assert "command failed (3)" in str(e.value)


def test_run_prints_nothing_of_its_own(capsys):
    """`_gh_json_or_none` swallows the exception; if `_run` also wrote to stderr, a
    fresh repository's probes would bury the report in 404s.

    Mutation: restore `sys.stderr.write(...)` before the raise.
    """
    with pytest.raises(SystemExit):
        onboard._run([sys.executable, "-c", "import sys; sys.stderr.write('noise'); sys.exit(1)"])
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_main_refuses_a_bad_team_slug():
    """Mutation: drop the `_TEAM_RE` check, so a value with a space reaches
    `gh api` as two arguments."""
    with pytest.raises(SystemExit) as e:
        onboard.main(["--team", "bad team", "--app-id", "1", "--key", "k"])
    assert "--team" in str(e.value)


def test_main_refuses_a_non_numeric_app_id():
    """Mutation: `_APP_ID_RE.fullmatch` to `args.app_id.isdigit()`, which accepts
    the superscript digit below."""
    with pytest.raises(SystemExit) as e:
        onboard.main(["--team", "ops", "--app-id", "²", "--key", "k"])
    assert "--app-id" in str(e.value)


def test_write_forwards_secrets_to_run(monkeypatch):
    """A write carrying the App key must reach `_run` with it in `secrets`, or the
    failure path echoes the PEM.

    Mutation: drop `secrets=secrets` from `write`'s `_run` call.
    """
    seen = {}

    def fake(args, secrets=(), stdin=None):
        seen["secrets"] = secrets
        return ""

    monkeypatch.setattr(onboard, "_run", fake)
    assert (
        onboard.write("set", "key", ["gh", "secret", "set", "X"], stdin="pem", secrets=("pem",))
        is True
    )
    assert seen["secrets"] == ("pem",)


def test_main_exits_through_the_report_predicate():
    """Extracting the predicate into `_exit_code` made it testable and left its call
    site unguarded; without this, deleting the call from `main` is green.

    Mutation: drop the `sys.exit(_exit_code())` line from `main`.
    """
    tree = ast.parse((ENGINE / "scripts" / "onboard").read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    exits = [
        ast.unparse(n)
        for n in ast.walk(main)
        if isinstance(n, ast.Call) and ast.unparse(n).startswith("sys.exit")
    ]
    assert exits == ["sys.exit(_exit_code())"]

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

    A read is `gh api <path>` with no `-X`, keyed by the path, or `gh secret list ...` /
    `gh variable list ...`, keyed by its whole command line; anything else is a write and
    is only recorded. An
    unrouted read raises `AssertionError`, deliberately NOT `SystemExit`:
    `_gh_json_or_none` reads a `SystemExit` naming HTTP 404 as "absent", so a routing
    mistake phrased that way would be swallowed into the absent branch and the test
    would pass while the implementation wrote against a read nobody stubbed.

    A route whose value is a `SystemExit` is a failed read; one whose message names
    HTTP 404 is the absent case, any other is a real failure the caller must not
    mistake for absence.

    `_run.stdin` and `_run.secrets` are the per-call stdin and scrub list, positionally
    parallel to `_run.calls`, so a body can be parsed and compared rather than matched as
    text and a credential's placement can be asserted.
    """
    calls = []
    stdins = []
    scrubbed = []

    def _run(args, secrets=(), stdin=None):
        calls.append(list(args))
        stdins.append(stdin)
        scrubbed.append(secrets)
        if args[:2] == ["gh", "api"] and "-X" not in args:
            key = args[2]
        elif args[:3] in (["gh", "secret", "list"], ["gh", "variable", "list"]):
            key = " ".join(args)
        else:
            return ""
        if key not in routes:
            raise AssertionError(f"unrouted read: {key}")
        answer = routes[key]
        if isinstance(answer, SystemExit):
            raise answer
        return json.dumps(answer)

    _run.calls = calls
    _run.stdin = stdins
    _run.secrets = scrubbed
    return _run


ENGINE_PATH = "repos/o/r/environments/shipmate-engine"
ENGINE_POLICIES = f"{ENGINE_PATH}/deployment-branch-policies"
ENGINE_SECRETS = f"{ENGINE_PATH}/secrets?per_page=100"
# Named without the word ruff S105 flags: this is a command line, not a credential.
REPO_KEY_LIST = "gh secret list --json name"
VARIABLE_LIST = "gh variable list --json name,value"
RULES = "repos/o/r/rules/branches/main?per_page=100"
CUSTOM_POLICY = {"protected_branches": False, "custom_branch_policies": True}


def ctx(**over):
    base = {
        "repo": "o/r",
        "default_branch": "main",
        "app_id": "1",
        "team": "ops",
        "key": "-----BEGIN-----\npem\n",
        "envs": ["dev-eu"],
        "shared": set(),
        "state_suffix": "",
        "engine": None,
        "versions": {"terramate": "9.9.9", "tofu": "8.8.8"},
        "sha": "a" * 40,
        "version": "v0.26.0",
    }
    base.update(over)
    return base


def body_of(fake, argv):
    """The parsed stdin of the one recorded call whose argv is `argv`."""
    hits = [i for i, c in enumerate(fake.calls) if c == argv]
    assert len(hits) == 1, f"{argv} recorded {len(hits)} times"
    return json.loads(fake.stdin[hits[0]])


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

    The child concatenates the token from two halves, so the whole value appears only in
    its stderr and never in the argv: scrubbing the argv alone cannot satisfy the
    assertions, and the stderr clause is load-bearing rather than incidental.

    Three mutations redden it: drop the `_scrub` call around the stderr, so the token
    appears verbatim; drop the `raise` and return `p.stdout`, so a failed write is
    reported as done; or drop the `+ f"\\n{_scrub(stderr, secrets)}"` clause, which also
    takes `_gh_json_or_none`'s only channel for spotting an `HTTP 404`.
    """
    with pytest.raises(SystemExit) as e:
        onboard._run(
            [sys.executable, "-c", "import sys; sys.stderr.write('tok-' + 'abc'); sys.exit(3)"],
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


def _calls_in_order(node):
    """`ast.unparse` of every call inside `node`, in source order, depth first."""
    out = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.Call):
            out.append(ast.unparse(child))
        out.extend(_calls_in_order(child))
    return out


def test_main_calls_every_stage_in_order():
    """Each reconciler is exercised directly by its own test, which leaves `main`'s
    call sites unguarded: deleting both `_reconcile_*` lines left the whole suite green.
    The refusals and `sys.exit(_exit_code())` have the same exposure.

    One hand-written ordered list, compared with `==`, so a call to a private helper or
    to `sys.exit` that is dropped, reordered or added has to be reflected here
    deliberately. Ceiling: the filter is exactly that -- a stage added as `report(...)`
    or `write(...)` directly in `main` is not seen.

    Mutations, each proven: delete `_reconcile_engine_env(ctx)`; delete
    `_reconcile_envs(ctx)`; delete `_reconcile_variables(ctx)`; delete
    `_reconcile_ruleset(ctx)`; delete `sys.exit(_exit_code())`; swap two reconcilers.
    """
    tree = ast.parse((ENGINE / "scripts" / "onboard").read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    stages = [c for c in _calls_in_order(main) if c.startswith("_") or c.startswith("sys.exit")]
    assert stages == [
        "_TEAM_RE.fullmatch(args.team)",
        "_APP_ID_RE.fullmatch(args.app_id)",
        "_read_key(args.key)",
        "_versions(engine)",
        "_engine_pin(engine)",
        "_repo_facts()",
        "_derive_envs()",
        "_resolve_shared(args.shared, _variables(), envs)",
        "_variables()",
        "_reconcile_engine_env(ctx)",
        "_reconcile_envs(ctx)",
        "_reconcile_variables(ctx)",
        "_reconcile_ruleset(ctx)",
        "sys.exit(_exit_code())",
        "_exit_code()",
    ]


_CONFORMING_ENGINE = {
    ENGINE_PATH: {"deployment_branch_policy": CUSTOM_POLICY},
    ENGINE_POLICIES: {"total_count": 1, "branch_policies": [{"name": "main"}]},
    ENGINE_SECRETS: {"total_count": 1, "secrets": [{"name": "SHIPMATE_APP_PRIVATE_KEY"}]},
    REPO_KEY_LIST: [],
}


def test_fresh_engine_environment_is_created_with_the_policy_and_the_key(monkeypatch):
    """A repository with no `shipmate-engine` gets the environment, a custom branch
    policy naming the default branch, and the key -- in that order, because a secret
    cannot be written to an environment that does not exist.

    The recorded call list is compared whole against a hand-written constant: a
    membership check would pass a run that also issued a call nobody intended.

    Mutation: drop the deployment-branch-policies POST from `_ensure_policy`.
    """
    fake = make_gh(
        {
            ENGINE_PATH: SystemExit("gh: Not Found (HTTP 404)"),
            ENGINE_POLICIES: {"total_count": 0, "branch_policies": []},
            ENGINE_SECRETS: {"total_count": 0, "secrets": []},
            REPO_KEY_LIST: [],
        }
    )
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_engine_env(ctx())
    assert fake.calls == [
        ["gh", "api", "repos/o/r/environments/shipmate-engine"],
        ["gh", "api", "-X", "PUT", "repos/o/r/environments/shipmate-engine", "--input", "-"],
        ["gh", "api", "repos/o/r/environments/shipmate-engine/deployment-branch-policies"],
        [
            "gh",
            "api",
            "-X",
            "POST",
            "repos/o/r/environments/shipmate-engine/deployment-branch-policies",
            "-f",
            "name=main",
        ],
        ["gh", "api", "repos/o/r/environments/shipmate-engine/secrets?per_page=100"],
        ["gh", "secret", "set", "SHIPMATE_APP_PRIVATE_KEY", "--env", "shipmate-engine"],
        ["gh", "secret", "list", "--json", "name"],
    ]


def test_the_key_reaches_gh_secret_set_on_stdin_and_as_a_secret(monkeypatch):
    """`gh secret set` has no `--body` sentinel for stdin -- `--body -` stores the
    literal one-character string `-` -- and the PEM must be in `secrets` so a failed
    write cannot echo it.

    Mutation: pass the PEM as `["--body", ctx["key"]]` instead of on stdin; or drop
    `secrets=(ctx["key"],)`.
    """
    fake = make_gh(
        {
            ENGINE_SECRETS: {"total_count": 0, "secrets": []},
            REPO_KEY_LIST: [],
        }
    )
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_key(ctx())
    sets = [
        (argv, fake.stdin[i], fake.secrets[i])
        for i, argv in enumerate(fake.calls)
        if argv[:3] == ["gh", "secret", "set"]
    ]
    assert sets == [
        (
            ["gh", "secret", "set", "SHIPMATE_APP_PRIVATE_KEY", "--env", "shipmate-engine"],
            "-----BEGIN-----\npem\n",
            ("-----BEGIN-----\npem\n",),
        )
    ]


def test_null_policy_on_an_existing_engine_environment_is_repaired(monkeypatch):
    """An environment created without a branch policy lets a workflow on any branch
    claim the App key. The repair is a PUT naming only `deployment_branch_policy`,
    which is additive -- reviewers and `wait_timer` survive it.

    No `gh secret set`: the key is already listed, and its value is unreadable, so
    overwriting could only destroy a working placement.

    Mutation: change `_APPLY_BODY` to
    `{"protected_branches": True, "custom_branch_policies": False}`.
    """
    fake = make_gh(
        {
            ENGINE_PATH: {"deployment_branch_policy": None},
            ENGINE_POLICIES: {"total_count": 0, "branch_policies": []},
            ENGINE_SECRETS: {"total_count": 1, "secrets": [{"name": "SHIPMATE_APP_PRIVATE_KEY"}]},
            REPO_KEY_LIST: [],
        }
    )
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_engine_env(ctx())
    assert fake.calls == [
        ["gh", "api", "repos/o/r/environments/shipmate-engine"],
        ["gh", "api", "-X", "PUT", "repos/o/r/environments/shipmate-engine", "--input", "-"],
        ["gh", "api", "repos/o/r/environments/shipmate-engine/deployment-branch-policies"],
        [
            "gh",
            "api",
            "-X",
            "POST",
            "repos/o/r/environments/shipmate-engine/deployment-branch-policies",
            "-f",
            "name=main",
        ],
        ["gh", "api", "repos/o/r/environments/shipmate-engine/secrets?per_page=100"],
        ["gh", "secret", "list", "--json", "name"],
    ]
    assert body_of(
        fake, ["gh", "api", "-X", "PUT", "repos/o/r/environments/shipmate-engine", "--input", "-"]
    ) == {"deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}}


def test_conforming_engine_environment_writes_nothing(monkeypatch):
    """A second run over a configured repository must change nothing: four reads, no
    write, and every report line `ok` so the exit code stays 0.

    Mutation: drop the `custom_branch_policies` test in `_reconcile_env`, so a
    conforming environment is PUT again.
    """
    fake = make_gh(dict(_CONFORMING_ENGINE))
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_engine_env(ctx())
    assert fake.calls == [
        ["gh", "api", "repos/o/r/environments/shipmate-engine"],
        ["gh", "api", "repos/o/r/environments/shipmate-engine/deployment-branch-policies"],
        ["gh", "api", "repos/o/r/environments/shipmate-engine/secrets?per_page=100"],
        ["gh", "secret", "list", "--json", "name"],
    ]
    assert [verb for verb, _subject, _detail in onboard.REPORT] == ["ok", "ok", "ok", "ok"]
    assert onboard._exit_code() == 0


def test_repository_level_key_is_deleted(monkeypatch):
    """A repository-level copy of the App key defeats the environment scoping
    entirely -- a workflow on any branch can read it -- so it is removed, not
    reported.

    Mutation: downgrade the deletion to `report("differs", ...)`.
    """
    fake = make_gh(
        dict(_CONFORMING_ENGINE, **{REPO_KEY_LIST: [{"name": "SHIPMATE_APP_PRIVATE_KEY"}]})
    )
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_engine_env(ctx())
    assert fake.calls == [
        ["gh", "api", "repos/o/r/environments/shipmate-engine"],
        ["gh", "api", "repos/o/r/environments/shipmate-engine/deployment-branch-policies"],
        ["gh", "api", "repos/o/r/environments/shipmate-engine/secrets?per_page=100"],
        ["gh", "secret", "list", "--json", "name"],
        ["gh", "secret", "delete", "SHIPMATE_APP_PRIVATE_KEY"],
    ]
    assert [(verb, subject) for verb, subject, _detail in onboard.REPORT] == [
        ("ok", "shipmate-engine"),
        ("ok", "shipmate-engine branch policy"),
        ("ok", "shipmate-engine SHIPMATE_APP_PRIVATE_KEY"),
        ("delete", "repository secret SHIPMATE_APP_PRIVATE_KEY"),
    ]


def test_shared_mode_binds_one_bare_environment(monkeypatch):
    """An environment listed in `--shared` / SHIPMATE_SHARED_ENVS is one bare
    `<env>` on both paths; no `<env>-plan` is created for it.

    Mutation: make `_env_names` ignore `shared` and always return the split pair.
    """
    assert onboard._env_names("dev-eu", {"dev-eu"}) == [("dev-eu", "apply")]
    fake = make_gh(
        {
            "repos/o/r/environments/dev-eu": {"deployment_branch_policy": CUSTOM_POLICY},
            "repos/o/r/environments/dev-eu/deployment-branch-policies": {
                "total_count": 1,
                "branch_policies": [{"name": "main"}],
            },
        }
    )
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_envs(ctx(shared={"dev-eu"}))
    assert fake.calls == [
        ["gh", "api", "repos/o/r/environments/dev-eu"],
        ["gh", "api", "repos/o/r/environments/dev-eu/deployment-branch-policies"],
    ]


def test_plan_environment_gets_no_branch_policy(monkeypatch):
    """A plan environment must admit every branch: a branch policy there stops the
    autoplan on a pull request. Its PUT body sets `deployment_branch_policy` to null
    and no POST follows it.

    Mutation: reuse `_APPLY_BODY` for the plan half.
    """
    fake = make_gh(
        {
            "repos/o/r/environments/dev-eu": SystemExit("gh: Not Found (HTTP 404)"),
            "repos/o/r/environments/dev-eu-plan": SystemExit("gh: Not Found (HTTP 404)"),
            "repos/o/r/environments/dev-eu-apply": SystemExit("gh: Not Found (HTTP 404)"),
            "repos/o/r/environments/dev-eu-apply/deployment-branch-policies": {
                "total_count": 0,
                "branch_policies": [],
            },
        }
    )
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_envs(ctx())
    assert fake.calls == [
        ["gh", "api", "repos/o/r/environments/dev-eu"],
        ["gh", "api", "repos/o/r/environments/dev-eu-plan"],
        ["gh", "api", "-X", "PUT", "repos/o/r/environments/dev-eu-plan", "--input", "-"],
        ["gh", "api", "repos/o/r/environments/dev-eu-apply"],
        ["gh", "api", "-X", "PUT", "repos/o/r/environments/dev-eu-apply", "--input", "-"],
        ["gh", "api", "repos/o/r/environments/dev-eu-apply/deployment-branch-policies"],
        [
            "gh",
            "api",
            "-X",
            "POST",
            "repos/o/r/environments/dev-eu-apply/deployment-branch-policies",
            "-f",
            "name=main",
        ],
    ]
    assert body_of(
        fake, ["gh", "api", "-X", "PUT", "repos/o/r/environments/dev-eu-plan", "--input", "-"]
    ) == {"deployment_branch_policy": None}
    assert body_of(
        fake, ["gh", "api", "-X", "PUT", "repos/o/r/environments/dev-eu-apply", "--input", "-"]
    ) == {"deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}}


def test_existing_policy_naming_another_branch_is_reported_not_edited(monkeypatch):
    """The spec is *exactly* the default branch, not *at least*: with `release/*` also
    named, a workflow on a release branch can still claim the App key, and `doctor`
    warns on it. Adding the default branch is additive, so the POST is issued; removing
    the other name is the consumer's call, so it is a `differs` line and no DELETE.

    Both halves are pinned whole -- the calls and the report -- because a run that only
    POSTs and exits 0 disagrees with `doctor` over the same repository.

    Mutations: delete the policies that do not name the default branch; drop the `extra`
    report so the run is silently `ok`.
    """
    fake = make_gh(
        dict(
            _CONFORMING_ENGINE,
            **{ENGINE_POLICIES: {"total_count": 1, "branch_policies": [{"name": "release/*"}]}},
        )
    )
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_engine_env(ctx())
    assert fake.calls == [
        ["gh", "api", "repos/o/r/environments/shipmate-engine"],
        ["gh", "api", "repos/o/r/environments/shipmate-engine/deployment-branch-policies"],
        [
            "gh",
            "api",
            "-X",
            "POST",
            "repos/o/r/environments/shipmate-engine/deployment-branch-policies",
            "-f",
            "name=main",
        ],
        ["gh", "api", "repos/o/r/environments/shipmate-engine/secrets?per_page=100"],
        ["gh", "secret", "list", "--json", "name"],
    ]
    assert onboard.REPORT == [
        ("ok", "shipmate-engine", ""),
        ("create", "shipmate-engine branch policy", "main"),
        (
            "differs",
            "shipmate-engine branch policy",
            "also permits release/* — a workflow on those branches can still claim what "
            "`shipmate-engine` scopes. Remove them by hand if that is not intended.",
        ),
        ("ok", "shipmate-engine SHIPMATE_APP_PRIVATE_KEY", ""),
        ("ok", "no repository-level SHIPMATE_APP_PRIVATE_KEY", ""),
    ]
    assert onboard._exit_code() == 2


def test_plan_environment_carrying_a_policy_is_reported_not_stripped(monkeypatch):
    """Removing a consumer's protection is not this script's call, and doctor already
    warns on it: the plan half is reported as differing and left untouched.

    Mutation: PUT `_PLAN_BODY` over it instead of reporting.
    """
    fake = make_gh(
        {
            "repos/o/r/environments/dev-eu": SystemExit("gh: Not Found (HTTP 404)"),
            "repos/o/r/environments/dev-eu-plan": {"deployment_branch_policy": CUSTOM_POLICY},
            "repos/o/r/environments/dev-eu-apply": {"deployment_branch_policy": CUSTOM_POLICY},
            "repos/o/r/environments/dev-eu-apply/deployment-branch-policies": {
                "total_count": 1,
                "branch_policies": [{"name": "main"}],
            },
        }
    )
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_envs(ctx())
    assert fake.calls == [
        ["gh", "api", "repos/o/r/environments/dev-eu"],
        ["gh", "api", "repos/o/r/environments/dev-eu-plan"],
        ["gh", "api", "repos/o/r/environments/dev-eu-apply"],
        ["gh", "api", "repos/o/r/environments/dev-eu-apply/deployment-branch-policies"],
    ]
    assert onboard.REPORT == [
        (
            "differs",
            "dev-eu-plan",
            "it carries a deployment branch policy, which blocks every plan cell whose "
            "pull request targets a branch the policy does not name. Removing a "
            "protection a consumer set is not this script's call, so it is reported "
            "and left alone.",
        ),
        ("ok", "dev-eu-apply", ""),
        ("ok", "dev-eu-apply branch policy", "main"),
    ]
    assert onboard._exit_code() == 2


def test_a_bare_env_alongside_an_apply_env_is_reported_as_ambiguous(monkeypatch):
    """Which of `dev-eu` and `dev-eu-apply` the engine binds depends on
    SHIPMATE_SHARED_ENVS, so a repository holding both while `--shared` is empty is a
    state the script must not resolve by guessing: it reports and writes nothing.

    Mutation: fall through to reconciling `dev-eu-apply` and say nothing.
    """
    fake = make_gh(
        {
            "repos/o/r/environments/dev-eu": {"deployment_branch_policy": CUSTOM_POLICY},
            # Routed, though a conforming run never reads it: without it the mutation
            # below reddens on an unrouted read rather than on the property named here.
            "repos/o/r/environments/dev-eu-plan": SystemExit("gh: Not Found (HTTP 404)"),
            "repos/o/r/environments/dev-eu-apply": {"deployment_branch_policy": CUSTOM_POLICY},
            "repos/o/r/environments/dev-eu-apply/deployment-branch-policies": {
                "total_count": 1,
                "branch_policies": [{"name": "main"}],
            },
        }
    )
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_envs(ctx())
    assert fake.calls == [
        ["gh", "api", "repos/o/r/environments/dev-eu"],
        ["gh", "api", "repos/o/r/environments/dev-eu-apply"],
    ]
    assert onboard.REPORT == [
        (
            "differs",
            "dev-eu",
            "both `dev-eu` and `dev-eu-apply` exist; which one the engine binds depends "
            "on SHIPMATE_SHARED_ENVS. Delete one, or list `dev-eu` in --shared.",
        )
    ]


def test_plan_environment_with_a_protection_rule_is_reported(monkeypatch):
    """A required reviewer or a wait timer on a plan environment stalls every plan cell
    and the nightly drift run. It is drift, not something to strip: removing a
    protection a consumer set is not this script's call.

    Mutation: drop the `protection_rules` arm of `_plan_drift`, so the environment
    reports `ok` and the run exits 0.
    """
    fake = make_gh(
        {
            "repos/o/r/environments/dev-eu": SystemExit("gh: Not Found (HTTP 404)"),
            "repos/o/r/environments/dev-eu-plan": {
                "deployment_branch_policy": None,
                "protection_rules": [{"type": "required_reviewers"}, {"type": "wait_timer"}],
            },
            "repos/o/r/environments/dev-eu-apply": {"deployment_branch_policy": CUSTOM_POLICY},
            "repos/o/r/environments/dev-eu-apply/deployment-branch-policies": {
                "total_count": 1,
                "branch_policies": [{"name": "main"}],
            },
        }
    )
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_envs(ctx())
    assert fake.calls == [
        ["gh", "api", "repos/o/r/environments/dev-eu"],
        ["gh", "api", "repos/o/r/environments/dev-eu-plan"],
        ["gh", "api", "repos/o/r/environments/dev-eu-apply"],
        ["gh", "api", "repos/o/r/environments/dev-eu-apply/deployment-branch-policies"],
    ]
    assert onboard.REPORT == [
        (
            "differs",
            "dev-eu-plan",
            "it carries protection rules (required_reviewers, wait_timer), which stall "
            "plan cells. Removing a protection a consumer set is not this script's "
            "call, so it is reported and left alone.",
        ),
        ("ok", "dev-eu-apply", ""),
        ("ok", "dev-eu-apply branch policy", "main"),
    ]
    assert onboard._exit_code() == 2


def test_a_read_failure_that_is_not_a_404_refuses(monkeypatch):
    """`_gh_json_or_none` treating every nonzero exit as absent would make a transient
    read failure on `<env>-plan` take the create branch and PUT a null
    `deployment_branch_policy` over a policy the consumer set -- the exact write
    `test_plan_environment_carrying_a_policy_is_reported_not_stripped` exists to
    forbid, reached by a different route.

    Mutation: `if "HTTP 404" not in str(e): raise` back to a bare `return None`; the
    refusal becomes a `PUT` and both assertions below fail.
    """
    fake = make_gh(
        {"repos/o/r/environments/dev-eu-plan": SystemExit("command failed (1): gh api\nHTTP 403")}
    )
    monkeypatch.setattr(onboard, "_run", fake)
    with pytest.raises(SystemExit) as e:
        onboard._reconcile_env(ctx(), "dev-eu-plan", "plan")
    assert "HTTP 403" in str(e.value)
    assert fake.calls == [["gh", "api", "repos/o/r/environments/dev-eu-plan"]]


def test_dry_run_reaches_every_write_path_and_issues_only_reads(monkeypatch):
    """`test_dry_run_issues_no_write` pins `write` itself; it says nothing about whether
    a reconciler routes through it. This drives every reconciler over a repository shaped
    so that all seven `write(...)` sites are reached -- create, update, the branch-policy
    POST, the key, the destructive repository-secret delete, the variable set and the
    ruleset POST -- and compares the whole recorded call list against a hand-written
    constant of reads.

    The engine environment exists with a null policy (the update path) and a
    repository-level copy of the key exists (the delete path); everything else is absent,
    and the repository has no variable and no rule.

    Mutation: swap any one of the seven `write(...)` calls for a direct `_run(...)`. Each
    one appears in the call list below and reddens it.
    """
    absent = SystemExit("gh: Not Found (HTTP 404)")
    fake = make_gh(
        {
            ENGINE_PATH: {"deployment_branch_policy": None},
            ENGINE_POLICIES: {"total_count": 0, "branch_policies": []},
            ENGINE_SECRETS: absent,
            REPO_KEY_LIST: [{"name": "SHIPMATE_APP_PRIVATE_KEY"}],
            "repos/o/r/environments/dev-eu": absent,
            "repos/o/r/environments/dev-eu-plan": absent,
            "repos/o/r/environments/dev-eu-apply": absent,
            "repos/o/r/environments/dev-eu-apply/deployment-branch-policies": absent,
            VARIABLE_LIST: [],
            RULES: [],
        }
    )
    monkeypatch.setattr(onboard, "_run", fake)
    monkeypatch.setattr(onboard, "_DRY", True)
    onboard._reconcile_engine_env(ctx())
    onboard._reconcile_envs(ctx())
    onboard._reconcile_variables(ctx())
    onboard._reconcile_ruleset(ctx())
    assert fake.calls == [
        ["gh", "api", "repos/o/r/environments/shipmate-engine"],
        ["gh", "api", "repos/o/r/environments/shipmate-engine/deployment-branch-policies"],
        ["gh", "api", "repos/o/r/environments/shipmate-engine/secrets?per_page=100"],
        ["gh", "secret", "list", "--json", "name"],
        ["gh", "api", "repos/o/r/environments/dev-eu"],
        ["gh", "api", "repos/o/r/environments/dev-eu-plan"],
        ["gh", "api", "repos/o/r/environments/dev-eu-apply"],
        ["gh", "api", "repos/o/r/environments/dev-eu-apply/deployment-branch-policies"],
        ["gh", "variable", "list", "--json", "name,value"],
        ["gh", "api", RULES],
    ]
    assert [verb for verb, _subject, _detail in onboard.REPORT] == [
        "would update",
        "would create",
        "would set",
        "would delete",
        "would create",
        "would create",
        "would create",
        "would set",
        "would set",
        "would set",
        "would set",
        "would create",
    ]


def test_absent_variables_are_set_from_versions_and_flags(monkeypatch):
    """A repository with no variables gets all four the workflows read, the two version
    pins taken from the engine checkout's VERSIONS file rather than from anything the
    operator retypes.

    The whole recorded call list is compared against a hand-written constant: a
    membership check would pass a run that also set a variable nobody intended.

    Mutation: read `TOFU_VERSION` from `ctx["version"]` -- the engine release tag --
    instead of from VERSIONS.
    """
    fake = make_gh({VARIABLE_LIST: []})
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_variables(ctx())
    assert fake.calls == [
        ["gh", "variable", "list", "--json", "name,value"],
        ["gh", "variable", "set", "SHIPMATE_APP_ID", "--body", "1"],
        ["gh", "variable", "set", "SHIPMATE_APPROVERS_TEAM", "--body", "ops"],
        ["gh", "variable", "set", "TERRAMATE_VERSION", "--body", "9.9.9"],
        ["gh", "variable", "set", "TOFU_VERSION", "--body", "8.8.8"],
    ]


def test_variables_that_exist_with_another_value_are_reported(monkeypatch):
    """A consumer pinning an older tested pair, or another App, is making a deliberate
    choice: the reconciler names the disagreement and writes nothing over it. Each
    `differs` line names where the value it would have written came from, so two
    variables from different sources are driven here -- a swap of the `--app-id` and
    `--team` labels reddens the first line.

    Mutation: overwrite the existing value instead of reporting -- both `differs` tuples
    disappear and `gh variable set` calls for both appear.
    """
    fake = make_gh(
        {
            VARIABLE_LIST: [
                {"name": "SHIPMATE_APP_ID", "value": "123"},
                {"name": "TERRAMATE_VERSION", "value": "0.16.0"},
            ]
        }
    )
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_variables(ctx(app_id="456"))
    assert fake.calls == [
        ["gh", "variable", "list", "--json", "name,value"],
        ["gh", "variable", "set", "SHIPMATE_APPROVERS_TEAM", "--body", "ops"],
        ["gh", "variable", "set", "TOFU_VERSION", "--body", "8.8.8"],
    ]
    assert onboard.REPORT == [
        ("differs", "SHIPMATE_APP_ID", "repository has 123, --app-id is 456"),
        ("set", "SHIPMATE_APPROVERS_TEAM", "ops"),
        ("differs", "TERRAMATE_VERSION", "repository has 0.16.0, VERSIONS is 9.9.9"),
        ("set", "TOFU_VERSION", "8.8.8"),
    ]
    assert onboard._exit_code() == 2


def test_variable_names_are_matched_uppercased(monkeypatch):
    """The API returns variable names uppercased whatever case they were created in, so
    a case-sensitive lookup would set a variable that is already there.

    Mutation: drop the `.upper()` in `_variables`.
    """
    fake = make_gh({VARIABLE_LIST: [{"name": "tofu_version", "value": "8.8.8"}]})
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_variables(ctx())
    assert fake.calls == [
        ["gh", "variable", "list", "--json", "name,value"],
        ["gh", "variable", "set", "SHIPMATE_APP_ID", "--body", "1"],
        ["gh", "variable", "set", "SHIPMATE_APPROVERS_TEAM", "--body", "ops"],
        ["gh", "variable", "set", "TERRAMATE_VERSION", "--body", "9.9.9"],
    ]
    assert onboard.REPORT == [
        ("set", "SHIPMATE_APP_ID", "1"),
        ("set", "SHIPMATE_APPROVERS_TEAM", "ops"),
        ("set", "TERRAMATE_VERSION", "9.9.9"),
        ("ok", "TOFU_VERSION", "8.8.8"),
    ]


def test_shared_environments_are_written_as_one_sorted_variable(monkeypatch):
    """SHIPMATE_SHARED_ENVS is a set written as a list, so the value written is sorted
    and a repository whose variable lists the same names in another order is not drift.

    Three names, not two: `PYTHONHASHSEED` randomises string hashing, so an unsorted
    join of two names lands in sorted order about half the time. Three shortens that to
    one run in six -- measured red on 9 of 12 seeds, so the unsorted mutation below is
    the one probabilistic claim here, not a certain one.

    Mutations: join the set unsorted (`",".join(ctx["shared"])`), or drop the
    SHIPMATE_SHARED_ENVS entry from `_wanted_variables` -- both redden the first case;
    drop the `_matches` set comparison, which turns the second into a `differs` and a
    rewrite of a variable that already says what it should.
    """
    shared = {"dev-us", "dev-eu", "dev-ap"}
    fake = make_gh({VARIABLE_LIST: []})
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_variables(ctx(shared=shared))
    assert fake.calls == [
        ["gh", "variable", "list", "--json", "name,value"],
        ["gh", "variable", "set", "SHIPMATE_APP_ID", "--body", "1"],
        ["gh", "variable", "set", "SHIPMATE_APPROVERS_TEAM", "--body", "ops"],
        ["gh", "variable", "set", "TERRAMATE_VERSION", "--body", "9.9.9"],
        ["gh", "variable", "set", "TOFU_VERSION", "--body", "8.8.8"],
        ["gh", "variable", "set", "SHIPMATE_SHARED_ENVS", "--body", "dev-ap,dev-eu,dev-us"],
    ]

    onboard.REPORT.clear()
    fake = make_gh(
        {VARIABLE_LIST: [{"name": "SHIPMATE_SHARED_ENVS", "value": "dev-us, dev-ap, dev-eu"}]}
    )
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_variables(ctx(shared=shared))
    assert onboard.REPORT == [
        ("set", "SHIPMATE_APP_ID", "1"),
        ("set", "SHIPMATE_APPROVERS_TEAM", "ops"),
        ("set", "TERRAMATE_VERSION", "9.9.9"),
        ("set", "TOFU_VERSION", "8.8.8"),
        ("ok", "SHIPMATE_SHARED_ENVS", "dev-ap,dev-eu,dev-us"),
    ]
    assert onboard._exit_code() == 0


def test_an_unusable_versions_file_refuses_before_the_first_call(monkeypatch, tmp_path):
    """`_versions` reads a local file and depends on nothing the reconcilers do, so its
    refusal belongs among `main`'s local reads: a repository must not end up with half
    its environments created over a typo in a file that was readable at startup. Zero
    recorded calls is the assertion -- the message alone would be satisfied by a refusal
    from the wrong place.

    Mutation: move the `_versions(engine)` call back below the reconcilers (into
    `_wanted_variables`, where it started), which records `git` and `gh` calls before
    the refusal.
    """
    fake = make_gh({})
    monkeypatch.setattr(onboard, "_run", fake)

    def boom(engine):
        raise SystemExit("bad VERSIONS")

    monkeypatch.setattr(onboard, "_versions", boom)
    pem = tmp_path / "key.pem"
    pem.write_text("-----BEGIN-----\npem\n", encoding="utf-8", newline="\n")
    with pytest.raises(SystemExit) as e:
        onboard.main(["--team", "ops", "--app-id", "1", "--key", str(pem)])
    assert "bad VERSIONS" in str(e.value)
    assert fake.calls == []


def test_a_missing_versions_file_is_refused(tmp_path):
    """Every other refusal in this script is a `SystemExit` naming what to fix;
    `test_an_unusable_versions_file_refuses_before_the_first_call` pins where it fires.

    Mutation: drop the `is_file()` check, so a `FileNotFoundError` traceback replaces it.
    """
    with pytest.raises(SystemExit) as e:
        onboard._versions(tmp_path)
    assert "VERSIONS" in str(e.value)


def test_a_versions_file_missing_a_key_is_refused(tmp_path):
    """`terramate=` and `tofu=` are this script's only coupling to that file's shape, and
    nothing else in the repository parses it. A renamed key must name itself, not raise a
    `KeyError` two frames away.

    Mutation: drop the `missing` check, so `_wanted_variables` raises `KeyError: 'tofu'`.
    """
    (tmp_path / "VERSIONS").write_text("terramate=9.9.9\n", encoding="utf-8", newline="\n")
    with pytest.raises(SystemExit) as e:
        onboard._versions(tmp_path)
    assert "tofu" in str(e.value)


def test_missing_gate_rule_creates_the_ruleset(monkeypatch):
    """A repository with no gate requirement gets exactly the gate rule, pinned to the
    App id, with `strict` on -- and nothing else, because a second ruleset carrying a
    `pull_request` rule would conflict with one the repository may already have.

    The POST body is compared whole against a hand-written dict.

    Mutation: drop `strict_required_status_checks_policy` from the body.
    """
    fake = make_gh({RULES: []})
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_ruleset(ctx(app_id="4326562"))
    post = ["gh", "api", "-X", "POST", "repos/o/r/rulesets", "--input", "-"]
    assert fake.calls == [["gh", "api", RULES], post]
    assert body_of(fake, post) == {
        "name": "shipmate-gate",
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [
                        {"context": "shipmate / gate", "integration_id": 4326562}
                    ],
                    "strict_required_status_checks_policy": True,
                },
            }
        ],
    }


def _rules(integration_id=1, strict=True):
    """The effective branch rules of a repository whose gate is already required."""
    return [
        {
            "type": "required_status_checks",
            "parameters": {
                "required_status_checks": [
                    {"context": "other / check", "integration_id": 15368},
                    {"context": "shipmate / gate", "integration_id": integration_id},
                ],
                "strict_required_status_checks_policy": strict,
            },
        }
    ]


def test_gate_under_another_integration_id_is_reported_not_edited(monkeypatch):
    """A gate pinned to another identity is satisfied by a status that identity writes,
    so it is real drift -- but editing a ruleset this script did not create is a policy
    change it must not make silently.

    Mutation: make the wrong-`integration_id` branch fall through to the POST; the call
    list gains `repos/o/r/rulesets` and the report loses its `differs` line.
    """
    fake = make_gh({RULES: _rules(integration_id=15368)})
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_ruleset(ctx(app_id="4326562"))
    assert fake.calls == [["gh", "api", RULES]]
    assert onboard.REPORT == [
        (
            "differs",
            "gate ruleset",
            "`shipmate / gate` is required under integration_id 15368, not the shipmate "
            "App (4326562) — a status from another identity satisfies it. Change it by hand.",
        )
    ]
    assert onboard._exit_code() == 2


def test_conforming_gate_is_ok(monkeypatch):
    """A second run over a configured repository reads the rules and writes nothing.

    Mutation: compare `integration_id` against `ctx["app_id"]` without `int(...)`, so a
    conforming repository is reported as differing on every run.
    """
    fake = make_gh({RULES: _rules()})
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_ruleset(ctx())
    assert fake.calls == [["gh", "api", RULES]]
    assert onboard.REPORT == [("ok", "gate ruleset", "")]
    assert onboard._exit_code() == 0


def test_a_gate_without_strict_is_reported(monkeypatch):
    """Without `strict`, GitHub never re-tests the merge result and a plan can go stale
    against the base before merge.

    Mutation: drop the `strict_required_status_checks_policy` arm, so the repository is
    reported `ok` and the run exits 0.
    """
    fake = make_gh({RULES: _rules(strict=False)})
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_ruleset(ctx())
    assert fake.calls == [["gh", "api", RULES]]
    assert onboard.REPORT == [
        (
            "differs",
            "gate ruleset",
            "it does not require branches to be up to date (strict), so plans can go "
            "stale against the base before merge. Change it by hand.",
        )
    ]


@pytest.mark.parametrize("status", ["HTTP 403", "HTTP 404"])
def test_ruleset_post_forbidden_is_reported_with_the_plan_sentence(monkeypatch, status):
    """Rulesets need a paid plan or a public repository. Which status a repository
    without that plan answers the POST with is not established -- 403 and 404 are both
    plausible and neither was observed here -- so both are tolerated, and each is driven
    below rather than reasoned about. Every other setting is reconciled by then, so it
    is a report line, not a crash the operator has to re-run past.

    Mutations: drop the `except SystemExit` arm, so the failure propagates and the run
    dies with no report line; or drop either half of the 403/404 test, which reddens
    that half's case alone.
    """

    def fake(args, secrets=(), stdin=None):
        if "-X" in args:
            raise SystemExit(f"command failed (1): gh api\ngh: refused ({status})")
        return json.dumps([])

    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_ruleset(ctx())
    assert onboard.REPORT == [
        (
            "differs",
            "gate ruleset",
            "rulesets need GitHub Pro, Team, Enterprise, or a public repository "
            "(docs/branch-protection.md §Reproducible ruleset). Configure the gate by "
            "hand there.",
        )
    ]
    assert onboard._exit_code() == 2


def test_an_invisible_shipmate_gate_ruleset_is_reported_not_fatal(monkeypatch):
    """A `shipmate-gate` ruleset whose enforcement is `evaluate` or `disabled` requires
    nothing, so the effective-rules read cannot see it and the create branch is taken --
    where the POST answers 422 `name already in use`. Letting that propagate breaks the
    promise that a second run over a configured repository changes nothing.

    Mutation: drop the `HTTP 422` arm of `_post_failure`, so the run dies here.
    """

    def fake(args, secrets=(), stdin=None):
        if "-X" in args:
            raise SystemExit("command failed (1): gh api\ngh: Validation Failed (HTTP 422)")
        return json.dumps([])

    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_ruleset(ctx())
    assert onboard.REPORT == [
        (
            "differs",
            "gate ruleset",
            "a `shipmate-gate` ruleset already exists but requires nothing on this "
            "branch, so its enforcement is `evaluate` or `disabled` — the effective-rules "
            "read cannot see it. Set it to active, or delete it and run this again.",
        )
    ]
    assert onboard._exit_code() == 2


def test_an_unrecognised_ruleset_post_failure_propagates(monkeypatch):
    """A 500 or an expired token is not a plan-tier limitation: reporting `differs` over
    it would tell the operator to configure the gate by hand when the truth is that the
    call never landed.

    Mutation: `return None` in `_post_failure` to `return _PLAN_TIER`.
    """

    def fake(args, secrets=(), stdin=None):
        if "-X" in args:
            raise SystemExit("command failed (1): gh api\ngh: Server Error (HTTP 500)")
        return json.dumps([])

    monkeypatch.setattr(onboard, "_run", fake)
    with pytest.raises(SystemExit) as e:
        onboard._reconcile_ruleset(ctx())
    assert "HTTP 500" in str(e.value)

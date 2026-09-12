"""Unit tests for `scripts/onboard`, the per-repository reconciler.

Threat model: accidental regression -- a dropped API call, an inverted dry-run
branch, a case-sensitive comparison against an API that uppercases names. Not a
hostile edit to this repository, which is reviewed on every pull request.
"""

import ast
import json
import pathlib
import subprocess
import sys

import pytest
import yaml
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
        "unresolved": set(),
        "state_suffix": "",
        "root": None,
        "engine": None,
        "variables": {},
        "sha": "a" * 40,
        "version": "v0.26.0",
        "at_org": set(),
        "is_private": False,
        "org_plan": "",
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
    wrote = onboard.write("create", "thing", ["gh", "api", "-X", "PUT", "x"])
    assert wrote is False
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


def test_repo_root_is_the_checkout_root_not_the_cwd(monkeypatch):
    """The shims land at `<root>/.github/workflows/`. `gh repo view` resolves the same
    repository from any subdirectory and `terramate list` enumerates every stack from one, so
    a run started in a stack directory would otherwise report six files created where GitHub
    never looks -- exit 0 over a repository nothing was set up in.

    Mutation: `pathlib.Path.cwd()`, which records no call at all.
    """
    calls = []

    def fake(args, secrets=(), stdin=None):
        calls.append(list(args))
        return "/w/consumer\n"

    monkeypatch.setattr(onboard, "_run", fake)
    assert onboard._repo_root() == pathlib.Path("/w/consumer")
    assert calls == [["git", "rev-parse", "--show-toplevel"]]


def test_repo_facts_refuses_a_missing_default_branch(monkeypatch):
    """`gh repo view` outside a repository answers with nulls, and every later
    write would target the wrong place.

    Mutation: `.get("name") or ""` to `.get("name", "main")`, defaulting the branch.
    """
    monkeypatch.setattr(
        onboard,
        "_run",
        lambda args, secrets=(), stdin=None: json.dumps(
            {"nameWithOwner": "o/r", "defaultBranchRef": None, "isPrivate": False}
        ),
    )
    with pytest.raises(SystemExit) as e:
        onboard._repo_facts()
    assert "default branch" in str(e.value)


def test_repo_facts_reports_a_private_repository_and_asks_gh_for_the_field(monkeypatch):
    """`is_private` decides whether the GitHub Free refusal runs at all, and nothing else reads
    `_repo_facts`'s third element -- every other test either refuses before the return or stubs the
    function. Both halves are needed: the tuple compared whole catches a wrong key, and the argv
    compared whole catches `isPrivate` dropping out of the `--json` list, which the payload alone
    cannot, because a recording fake answers the same JSON whatever it is asked for.

    A recording lambda, not `make_gh`: `gh repo view` matches neither of its read branches, so it
    falls through to the write branch and returns "", which `json.loads` rejects.

    Mutations: `facts.get("is_private")`, which reds the tuple; drop `isPrivate` from the `--json`
    argument, which reds the argv alone.
    """
    seen = []

    def fake(args, secrets=(), stdin=None):
        seen.append(list(args))
        return json.dumps(
            {"nameWithOwner": "o/r", "defaultBranchRef": {"name": "main"}, "isPrivate": True}
        )

    monkeypatch.setattr(onboard, "_run", fake)
    assert onboard._repo_facts() == ("o/r", "main", True)
    assert seen == [["gh", "repo", "view", "--json", "nameWithOwner,defaultBranchRef,isPrivate"]]


def test_repo_facts_refuses_an_unusable_slug(monkeypatch):
    """The slug is interpolated into API paths. Mutation: drop the `_REPO_RE`
    check, so `../../o/r` reaches them.
    """
    monkeypatch.setattr(
        onboard,
        "_run",
        lambda args, secrets=(), stdin=None: json.dumps(
            {"nameWithOwner": "../../o/r", "defaultBranchRef": {"name": "main"}, "isPrivate": False}
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
    wrote = onboard.write("set", "key", ["gh", "secret", "set", "X"], stdin="pem", secrets=("pem",))
    assert wrote is True
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
    `_reconcile_ruleset(ctx)`; delete `_reconcile_shims(ctx)`; delete `_checklist(ctx)`;
    `_repo_root()` back to `pathlib.Path.cwd()`; delete
    `_report_superseded_variables(ctx)`, which leaves a repository carrying an inert version
    pin with nothing saying so; delete
    `_refuse_diverging_app_id(args.app_id, variables)`, which is the only guard against a
    ruleset pinned to an App the workflows do not use; delete `sys.exit(_exit_code())`;
    swap two reconcilers; delete `_report_org_leftovers(ctx)`; delete the
    `at_org = _at_org(args.vars_at_org)` line; move that line below `_read_key(args.key)`,
    which pays a filesystem read before a pure string check can refuse; delete the
    `ctx["org_plan"] = _org_plan(...)` line; delete `_refuse_unreachable_org_variables(ctx)`,
    which leaves a private Free repository onboarded with names that resolve to empty; delete
    `_refuse_org_assertion_mismatch(ctx)`, which leaves every `--vars-at-org` name filtered
    with nothing verifying the assertion behind it.
    """
    tree = ast.parse((ENGINE / "scripts" / "onboard").read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    stages = [c for c in _calls_in_order(main) if c.startswith("_") or c.startswith("sys.exit")]
    assert stages == [
        "_TEAM_RE.fullmatch(args.team)",
        "_APP_ID_RE.fullmatch(args.app_id)",
        "_SUFFIX_RE.fullmatch(args.state_suffix)",
        "_at_org(args.vars_at_org)",
        "_read_key(args.key)",
        "_engine_pin(engine)",
        "_repo_root()",
        "_repo_facts()",
        "_derive_envs()",
        "_variables()",
        "_refuse_diverging_app_id(args.app_id, variables)",
        "_resolve_shared(args.shared, variables, envs)",
        "_org_plan(ctx['repo'].split('/', 1)[0])",
        "_refuse_unreachable_org_variables(ctx)",
        "_refuse_org_assertion_mismatch(ctx)",
        "_reconcile_engine_env(ctx)",
        "_reconcile_envs(ctx)",
        "_reconcile_variables(ctx)",
        "_report_org_leftovers(ctx)",
        "_report_superseded_variables(ctx)",
        "_reconcile_ruleset(ctx)",
        "_reconcile_shims(ctx)",
        "_checklist(ctx)",
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

    The two suffixed reads are the naming-conflict probe, which in shared mode looks for
    the split pair: they 404 here, so the bare environment is reconciled.

    Mutation: make `_env_names` ignore `shared` and always return the split pair.
    """
    assert onboard._env_names("dev-eu", {"dev-eu"}) == [("dev-eu", "apply")]
    fake = make_gh(
        {
            "repos/o/r/environments/dev-eu": {"deployment_branch_policy": CUSTOM_POLICY},
            "repos/o/r/environments/dev-eu-plan": SystemExit("gh: Not Found (HTTP 404)"),
            "repos/o/r/environments/dev-eu-apply": SystemExit("gh: Not Found (HTTP 404)"),
            "repos/o/r/environments/dev-eu/deployment-branch-policies": {
                "total_count": 1,
                "branch_policies": [{"name": "main"}],
            },
        }
    )
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_envs(ctx(shared={"dev-eu"}))
    assert fake.calls == [
        ["gh", "api", "repos/o/r/environments/dev-eu-plan"],
        ["gh", "api", "repos/o/r/environments/dev-eu-apply"],
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


#: The one `differs` line `_naming_conflict` writes, per mode. Hand-written, not built
#: from the module: a detail derived from the code it checks says whatever the code says.
SPLIT_CONFLICT = (
    "differs",
    "dev-eu",
    "the engine binds `dev-eu-plan` / `dev-eu-apply` for `dev-eu`, and `dev-eu` is also "
    "present. Holding both namings for one logical environment is the state `shipmate doctor` "
    "calls ambiguous, where which naming each path binds is undetermined, so nothing was "
    "created or changed for `dev-eu`. Delete `dev-eu`, or pass `--shared dev-eu` so the "
    "engine binds the bare `dev-eu` instead.",
)
SHARED_CONFLICT = (
    "differs",
    "dev-eu",
    "the engine binds `dev-eu` for `dev-eu`, and `dev-eu-plan` and `dev-eu-apply` are "
    "also present. Holding both namings for one logical environment is the state `shipmate "
    "doctor` calls ambiguous, where which naming each path binds is undetermined, so "
    "nothing was created or changed for `dev-eu`. Delete `dev-eu-plan` and "
    "`dev-eu-apply`, or drop `dev-eu` from `--shared` and SHIPMATE_SHARED_ENVS.",
)


def test_a_bare_env_alongside_an_apply_env_is_reported_as_ambiguous(monkeypatch):
    """Which naming the engine binds depends on SHIPMATE_SHARED_ENVS, so a repository
    holding both is a state the script must not resolve by guessing: it reports and
    writes nothing.

    Mutation: fall through to reconciling `dev-eu-apply` and say nothing.
    """
    fake = make_gh(
        {
            "repos/o/r/environments/dev-eu": {"deployment_branch_policy": CUSTOM_POLICY},
            # Routed though a conforming run never reads them: the split naming's own
            # halves are what the mutation above would go on to reconcile, and without
            # these it would redden on an unrouted read rather than on the property here.
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
    assert fake.calls == [["gh", "api", "repos/o/r/environments/dev-eu"]]
    assert onboard.REPORT == [SPLIT_CONFLICT]


def test_a_bare_env_alone_is_refused_before_the_split_pair_is_created(monkeypatch):
    """The conflict is probed before the create, so this script never manufactures the
    state its own next run refuses. A repository carrying a plain `dev-eu` from before it
    adopted shipmate would otherwise have `dev-eu-plan` / `dev-eu-apply` created beside
    it, exit 0, and then be reported ambiguous by every later run and by `shipmate
    doctor` -- with the pair it just wrote never reconciled again.

    Mutation: `_naming_conflict` back to returning False unless a suffixed half already
    exists, which creates both halves here and reports nothing.
    """
    fake = make_gh(
        {
            "repos/o/r/environments/dev-eu": {"deployment_branch_policy": CUSTOM_POLICY},
            "repos/o/r/environments/dev-eu-plan": SystemExit("gh: Not Found (HTTP 404)"),
            "repos/o/r/environments/dev-eu-apply": SystemExit("gh: Not Found (HTTP 404)"),
            "repos/o/r/environments/dev-eu-apply/deployment-branch-policies": SystemExit(
                "gh: Not Found (HTTP 404)"
            ),
        }
    )
    monkeypatch.setattr(onboard, "_run", fake)
    context = ctx()
    onboard._reconcile_envs(context)
    assert fake.calls == [["gh", "api", "repos/o/r/environments/dev-eu"]]
    assert onboard.REPORT == [SPLIT_CONFLICT]
    assert context["unresolved"] == {"dev-eu"}


def test_the_split_pair_alone_is_refused_before_the_bare_env_is_created(monkeypatch):
    """The mirror of the case above, reached by migrating a split repository to
    `--shared`: creating the bare `dev-eu` beside the pair is the same self-inflicted
    ambiguity, so it is refused rather than written.

    Mutation: `_unused_naming` returning `[env]` unconditionally, which reads the bare
    name, finds it absent, and creates it beside the pair.
    """
    fake = make_gh(
        {
            "repos/o/r/environments/dev-eu": SystemExit("gh: Not Found (HTTP 404)"),
            "repos/o/r/environments/dev-eu-plan": {"deployment_branch_policy": None},
            "repos/o/r/environments/dev-eu-apply": {"deployment_branch_policy": CUSTOM_POLICY},
            "repos/o/r/environments/dev-eu/deployment-branch-policies": SystemExit(
                "gh: Not Found (HTTP 404)"
            ),
        }
    )
    monkeypatch.setattr(onboard, "_run", fake)
    context = ctx(shared={"dev-eu"})
    onboard._reconcile_envs(context)
    assert fake.calls == [
        ["gh", "api", "repos/o/r/environments/dev-eu-plan"],
        ["gh", "api", "repos/o/r/environments/dev-eu-apply"],
    ]
    assert onboard.REPORT == [SHARED_CONFLICT]
    assert context["unresolved"] == {"dev-eu"}


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


def test_dry_run_reaches_every_write_path_and_issues_only_reads(monkeypatch, tmp_path):
    """`test_dry_run_issues_no_write` pins `write` itself; it says nothing about whether
    a reconciler routes through it. This drives every reconciler over a repository shaped
    so that all seven `write(...)` sites and the one `write_file(...)` site are reached --
    create, update, the branch-policy POST, the key, the destructive repository-secret
    delete, the variable set, the ruleset POST and the absent workflow file -- and compares
    the whole recorded call list against a hand-written constant of reads.

    The engine environment exists with a null policy (the update path) and a
    repository-level copy of the key exists (the delete path); everything else is absent,
    and the repository has no variable, no rule and no workflow file.

    Mutations, each proven: swap any one of the seven `write(...)` calls for a direct
    `_run(...)`, which appears in the call list below; and make `write_file` fall through to
    `write_text` under `_DRY`, which puts a file in a checkout the operator was promised
    would not be touched. `_checklist` is driven here too: it only prints, so any `gh`
    call it grew would land in the list below.
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
    onboard._reconcile_shims(ctx(root=tmp_path, engine=ENGINE))
    onboard._checklist(ctx())
    assert list(tmp_path.iterdir()) == []
    assert fake.calls == [
        ["gh", "api", "repos/o/r/environments/shipmate-engine"],
        ["gh", "api", "repos/o/r/environments/shipmate-engine/deployment-branch-policies"],
        ["gh", "api", "repos/o/r/environments/shipmate-engine/secrets?per_page=100"],
        ["gh", "secret", "list", "--json", "name"],
        ["gh", "api", "repos/o/r/environments/dev-eu"],
        ["gh", "api", "repos/o/r/environments/dev-eu-plan"],
        ["gh", "api", "repos/o/r/environments/dev-eu-apply"],
        ["gh", "api", "repos/o/r/environments/dev-eu-apply/deployment-branch-policies"],
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
        "would create",
        "would create",
    ]


def test_absent_variables_are_set_from_the_flags_and_no_tool_version_is_written(monkeypatch):
    """A repository with no variables gets exactly the two the workflows still read. The
    tool versions are not among them: `actions/setup` takes both from the release's own
    VERSIONS file, so a repository copy would only be a second source of truth.

    The whole recorded call list is compared against a hand-written constant: an assertion
    that TERRAMATE_VERSION is absent is satisfied by a run that wrote nothing at all.

    Mutation: restore either version entry to `_writable_variables`.
    """
    fake = make_gh({VARIABLE_LIST: []})
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_variables(ctx(variables=onboard._variables()))
    assert fake.calls == [
        ["gh", "variable", "list", "--json", "name,value"],
        ["gh", "variable", "set", "SHIPMATE_APP_ID", "--body", "1"],
        ["gh", "variable", "set", "SHIPMATE_APPROVERS_TEAM", "--body", "ops"],
    ]


def test_variables_that_exist_with_another_value_are_reported(monkeypatch):
    """A consumer naming another App, or another approvers team, is making a deliberate
    choice: the reconciler names the disagreement and writes nothing over it. Each
    `differs` line names where the value it would have written came from, so two
    variables from different sources are driven here -- a swap of the `--app-id` and
    `--team` labels reddens both lines.

    Mutation: overwrite the existing value instead of reporting -- both `differs` tuples
    disappear and `gh variable set` calls for both appear.
    """
    fake = make_gh(
        {
            VARIABLE_LIST: [
                {"name": "SHIPMATE_APP_ID", "value": "123"},
                {"name": "SHIPMATE_APPROVERS_TEAM", "value": "platform"},
            ]
        }
    )
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_variables(
        ctx(app_id="456", shared={"dev-eu"}, variables=onboard._variables())
    )
    assert fake.calls == [
        ["gh", "variable", "list", "--json", "name,value"],
        ["gh", "variable", "set", "SHIPMATE_SHARED_ENVS", "--body", "dev-eu"],
    ]
    assert onboard.REPORT == [
        ("differs", "SHIPMATE_APP_ID", "repository has 123, --app-id is 456"),
        ("differs", "SHIPMATE_APPROVERS_TEAM", "repository has platform, --team is ops"),
        ("set", "SHIPMATE_SHARED_ENVS", "dev-eu"),
    ]
    assert onboard._exit_code() == 2


def test_variable_names_are_matched_uppercased(monkeypatch):
    """The API returns variable names uppercased whatever case they were created in, so
    a case-sensitive lookup would set a variable that is already there.

    Mutation: drop the `.upper()` in `_variables`.
    """
    fake = make_gh({VARIABLE_LIST: [{"name": "shipmate_approvers_team", "value": "ops"}]})
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_variables(ctx(variables=onboard._variables()))
    assert fake.calls == [
        ["gh", "variable", "list", "--json", "name,value"],
        ["gh", "variable", "set", "SHIPMATE_APP_ID", "--body", "1"],
    ]
    assert onboard.REPORT == [
        ("set", "SHIPMATE_APP_ID", "1"),
        ("ok", "SHIPMATE_APPROVERS_TEAM", "ops"),
    ]


def test_shared_environments_are_written_as_one_sorted_variable(monkeypatch):
    """SHIPMATE_SHARED_ENVS is a set written as a list, so the value written is sorted
    and a repository whose variable lists the same names in another order is not drift.

    Three names, not two: `PYTHONHASHSEED` randomises string hashing, so an unsorted
    join of two names lands in sorted order about half the time. Three shortens that to
    one run in six -- measured red on 9 of 12 seeds, so the unsorted mutation below is
    the one probabilistic claim here, not a certain one.

    Mutations: join the set unsorted (`",".join(ctx["shared"])`), or drop the
    SHIPMATE_SHARED_ENVS entry from `_writable_variables` -- both redden the first case;
    drop the `_matches` set comparison, which turns the second into a `differs` and a
    rewrite of a variable that already says what it should.
    """
    shared = {"dev-us", "dev-eu", "dev-ap"}
    fake = make_gh({VARIABLE_LIST: []})
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_variables(ctx(shared=shared, variables=onboard._variables()))
    assert fake.calls == [
        ["gh", "variable", "list", "--json", "name,value"],
        ["gh", "variable", "set", "SHIPMATE_APP_ID", "--body", "1"],
        ["gh", "variable", "set", "SHIPMATE_APPROVERS_TEAM", "--body", "ops"],
        ["gh", "variable", "set", "SHIPMATE_SHARED_ENVS", "--body", "dev-ap,dev-eu,dev-us"],
    ]

    onboard.REPORT.clear()
    fake = make_gh(
        {VARIABLE_LIST: [{"name": "SHIPMATE_SHARED_ENVS", "value": "dev-us, dev-ap, dev-eu"}]}
    )
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_variables(ctx(shared=shared, variables=onboard._variables()))
    assert onboard.REPORT == [
        ("set", "SHIPMATE_APP_ID", "1"),
        ("set", "SHIPMATE_APPROVERS_TEAM", "ops"),
        ("ok", "SHIPMATE_SHARED_ENVS", "dev-ap,dev-eu,dev-us"),
    ]
    assert onboard._exit_code() == 0


def test_at_org_uppercases_the_names_it_returns():
    """The API uppercases variable names and every lookup here is on an uppercased key, so
    an operator typing the lowercase name must still reach the same entry. The whole set is
    compared against a hand-written literal.

    Mutation: drop the `.upper()` in `_at_org` -- `shipmate_app_id` then matches no key and
    is silently ignored.
    """
    assert onboard._at_org("shipmate_app_id, SHIPMATE_APPROVERS_TEAM") == {
        "SHIPMATE_APP_ID",
        "SHIPMATE_APPROVERS_TEAM",
    }


def test_at_org_refuses_a_name_it_does_not_accept():
    """A name the flag does not accept filters nothing: the repository copy keeps being
    written and the run still reports success. The whole message is compared, because it is
    the only thing that tells the operator which name was wrong.

    Mutation: return the names without checking them against `AT_ORG_NAMES`.
    """
    with pytest.raises(SystemExit) as excinfo:
        onboard._at_org("not_a_shipmate_variable")
    assert str(excinfo.value) == (
        "--vars-at-org names NOT_A_SHIPMATE_VARIABLE, which it does not accept. "
        "It accepts SHIPMATE_APP_ID and SHIPMATE_APPROVERS_TEAM."
    )


def test_at_org_refuses_a_version_pin():
    """TERRAMATE_VERSION is not assertable at organization level, because nothing writes or
    reads it any more: the release's own VERSIONS file decides the version. Accepting the
    name would take `_refuse_org_assertion_mismatch` into a `KeyError` on a
    `_writable_variables` entry that no longer exists.

    Mutation: add "TERRAMATE_VERSION" to `AT_ORG_NAMES`.
    """
    with pytest.raises(SystemExit) as excinfo:
        onboard._at_org("TERRAMATE_VERSION")
    assert str(excinfo.value) == (
        "--vars-at-org names TERRAMATE_VERSION, which it does not accept. "
        "It accepts SHIPMATE_APP_ID and SHIPMATE_APPROVERS_TEAM."
    )


def test_wanted_variables_removes_exactly_the_names_asserted_at_org():
    """The whole filtered dict is compared against a hand-written constant: a
    `"SHIPMATE_APP_ID" not in result` assertion is satisfied by a filter that drops
    everything.

    Mutations: ignore `ctx["at_org"]` and return the unfiltered dict; filter out every
    entry.
    """
    assert onboard._wanted_variables(ctx(at_org={"SHIPMATE_APP_ID"}, shared={"dev-eu"})) == {
        "SHIPMATE_APPROVERS_TEAM": ("ops", "--team"),
        "SHIPMATE_SHARED_ENVS": ("dev-eu", "--shared"),
    }


def test_a_repository_copy_of_an_org_variable_is_reported_and_never_written(monkeypatch):
    """Repository resolution beats organization, so a leftover repository copy silently
    overrides the organization value the operator asserted. It is reported as drift and
    never deleted -- removing a value this script did not write exceeds its mandate.

    Mutation: report `"ok"` instead of `"differs"` -- the exit code drops to 0 while the
    message still reads as informative.
    """
    fake = make_gh({})
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._report_org_leftovers(
        ctx(at_org={"SHIPMATE_APP_ID"}, variables={"SHIPMATE_APP_ID": "123"})
    )
    assert fake.calls == []
    assert onboard.REPORT == [
        (
            "differs",
            "SHIPMATE_APP_ID",
            "repository has 123, asserted at organization level — delete it with "
            "`gh variable delete SHIPMATE_APP_ID` or drop the name from --vars-at-org",
        )
    ]
    assert onboard._exit_code() == 2


def test_a_superseded_tool_version_variable_is_reported_and_never_deleted(monkeypatch):
    """`actions/setup` reads the release's own VERSIONS file, so a repository copy of either
    pin is inert: an operator bumping it would otherwise get silence. Both names are driven
    in one call, because a loop reporting only the first passes a one-name fixture. The
    whole REPORT is compared, and zero recorded calls is the other half -- the remedy is the
    operator's to run, never this script's.

    Mutation: report `"ok"` instead of `"differs"` -- the exit code drops to 0 while the
    message still reads as informative.
    """
    fake = make_gh({})
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._report_superseded_variables(
        ctx(variables={"TERRAMATE_VERSION": "0.16.0", "TOFU_VERSION": "1.8.0"})
    )
    assert fake.calls == []
    assert onboard.REPORT == [
        (
            "differs",
            "TERRAMATE_VERSION",
            "repository has 0.16.0, superseded by the version the engine release pins — "
            "nothing reads it; delete it with `gh variable delete TERRAMATE_VERSION`",
        ),
        (
            "differs",
            "TOFU_VERSION",
            "repository has 1.8.0, superseded by the version the engine release pins — "
            "nothing reads it; delete it with `gh variable delete TOFU_VERSION`",
        ),
    ]
    assert onboard._exit_code() == 2


def test_a_repository_without_the_superseded_variables_reports_nothing(monkeypatch):
    """The other half: a clean repository gets no line at all, or every run of a correctly
    configured repository exits 2 and the report stops meaning anything.

    Mutation: report unconditionally, using `ctx["variables"].get(name, "")`.
    """
    fake = make_gh({})
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._report_superseded_variables(ctx(variables={}))
    assert fake.calls == []
    assert onboard.REPORT == []
    assert onboard._exit_code() == 0


ORG_VARS = "repos/o/r/actions/organization-variables"
ORG_VARS_READ = ["gh", "api", ORG_VARS, "--paginate", "--slurp"]
ORG_PLAN_READ = ["gh", "api", "orgs/o"]
ABSENT = SystemExit("gh: Not Found (HTTP 404)")
NO_ORG_VARS = [{"variables": [], "total_count": 0}]
ORG_APP_ID_MATCHES = [{"variables": [{"name": "SHIPMATE_APP_ID", "value": "1"}], "total_count": 1}]
ORG_TEAM_MATCHES = [
    {"variables": [{"name": "SHIPMATE_APPROVERS_TEAM", "value": "ops"}], "total_count": 1}
]

# Hand-written, and compared whole wherever these refusals are asserted: the message is the
# only thing that tells the operator which of the two fixes applies.
UNREACHED_APP_ID = (
    "--vars-at-org names SHIPMATE_APP_ID, but no organization variable of that name "
    "reaches o/r -- it is unset, or its visibility excludes this repository, so it "
    "would resolve to empty and every run would fail. Set it with `gh variable set "
    "SHIPMATE_APP_ID --org o --body 1 --visibility all`, add this repository to its "
    "selected list, or drop SHIPMATE_APP_ID from --vars-at-org."
)
FREE_APP_ID = (
    "o/r is private and o's plan reads free. Organization variables do not reach private "
    "repositories on GitHub Free, so SHIPMATE_APP_ID would resolve to empty and every run "
    "would fail. Upgrade the organization, keep them as repository variables (drop them "
    "from --vars-at-org), or -- if the plan reads 'unknown' -- re-run as an organization "
    "owner, because `gh api orgs/<org>` reports no plan to anyone else."
)
FREE_TEAM = (
    "o/r is private and o's plan reads free. Organization variables do not reach private "
    "repositories on GitHub Free, so SHIPMATE_APPROVERS_TEAM would resolve to empty and every "
    "run would fail. Upgrade the organization, keep them as repository variables (drop them "
    "from --vars-at-org), or -- if the plan reads 'unknown' -- re-run as an organization "
    "owner, because `gh api orgs/<org>` reports no plan to anyone else."
)
UNKNOWN_APP_ID = (
    "o/r is private and o's plan reads unknown. Organization variables do not reach private "
    "repositories on GitHub Free, so SHIPMATE_APP_ID would resolve to empty and every run "
    "would fail. Upgrade the organization, keep them as repository variables (drop them "
    "from --vars-at-org), or -- if the plan reads 'unknown' -- re-run as an organization "
    "owner, because `gh api orgs/<org>` reports no plan to anyone else."
)

FRESH_ROUTES = {
    ENGINE_PATH: ABSENT,
    ENGINE_POLICIES: ABSENT,
    ENGINE_SECRETS: ABSENT,
    REPO_KEY_LIST: [],
    "repos/o/r/environments/dev-eu": ABSENT,
    "repos/o/r/environments/dev-eu-plan": ABSENT,
    "repos/o/r/environments/dev-eu-apply": ABSENT,
    "repos/o/r/environments/dev-eu-apply/deployment-branch-policies": ABSENT,
    VARIABLE_LIST: [],
    RULES: [],
}


def run_main(monkeypatch, tmp_path, extra_routes, argv, is_private=False):
    """Drive `main()` over `FRESH_ROUTES` plus `extra_routes`, returning (fake, SystemExit).

    Only the reads `main` does before its first reconciler are stubbed -- the git, terramate
    and `gh repo view` reads, each with its own test. Everything below them runs for real
    against the fake, which is what makes the order of the organization checks observable.
    """
    fake = make_gh({**FRESH_ROUTES, **extra_routes})
    monkeypatch.setattr(onboard, "_run", fake)
    monkeypatch.setattr(onboard, "_engine_pin", lambda engine: ("a" * 40, "v0.26.0"))
    monkeypatch.setattr(onboard, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(onboard, "_repo_facts", lambda: ("o/r", "main", is_private))
    monkeypatch.setattr(onboard, "_derive_envs", lambda: ["dev-eu"])
    pem = tmp_path / "key.pem"
    pem.write_text("-----BEGIN-----\npem\n", encoding="utf-8", newline="\n")
    with pytest.raises(SystemExit) as excinfo:
        onboard.main(["--team", "ops", "--app-id", "1", "--key", str(pem), *argv])
    return fake, excinfo.value


def test_an_asserted_name_the_organization_does_not_reach_refuses(monkeypatch):
    """`--vars-at-org X` stops X being written here, so an X that is unset at organization
    level -- or set with a visibility excluding this repository -- leaves every workflow
    resolving it to empty. Both names are driven against the same empty response: a check
    scoped to SHIPMATE_APP_ID passes the second fixture.

    Mutations: delete the `raise`; gate the check on `SHIPMATE_APP_ID` alone.
    """
    fake = make_gh({ORG_VARS: NO_ORG_VARS})
    monkeypatch.setattr(onboard, "_run", fake)
    with pytest.raises(SystemExit) as excinfo:
        onboard._refuse_org_assertion_mismatch(ctx(at_org={"SHIPMATE_APP_ID"}))
    assert str(excinfo.value) == UNREACHED_APP_ID
    with pytest.raises(SystemExit) as excinfo:
        onboard._refuse_org_assertion_mismatch(ctx(at_org={"SHIPMATE_APPROVERS_TEAM"}))
    assert str(excinfo.value) == (
        "--vars-at-org names SHIPMATE_APPROVERS_TEAM, but no organization variable of that "
        "name reaches o/r -- it is unset, or its visibility excludes this repository, so it "
        "would resolve to empty and every run would fail. Set it with `gh variable set "
        "SHIPMATE_APPROVERS_TEAM --org o --body ops --visibility all`, add this "
        "repository to its selected list, or drop SHIPMATE_APPROVERS_TEAM from "
        "--vars-at-org."
    )


def test_an_organization_value_disagreeing_with_this_run_refuses_naming_both(monkeypatch):
    """The assertion is that the name is already set *correctly*: a value that is not the one
    this run would have written means this run configures one thing and every later run
    resolves another. Both names are driven, because one proves nothing about the loop.

    Mutations: delete the `raise`; compare against the wrong side of the pair.
    """
    fake = make_gh(
        {ORG_VARS: [{"variables": [{"name": "SHIPMATE_APP_ID", "value": "222"}], "total_count": 1}]}
    )
    monkeypatch.setattr(onboard, "_run", fake)
    with pytest.raises(SystemExit) as excinfo:
        onboard._refuse_org_assertion_mismatch(ctx(at_org={"SHIPMATE_APP_ID"}, app_id="111"))
    assert str(excinfo.value) == (
        "SHIPMATE_APP_ID reaches o/r as 222, but --app-id says 111. The workflows read the "
        "resolved value, so this run would configure one thing and every later run would use "
        "another. Re-run with --app-id matching, correct the organization variable first, "
        "or drop SHIPMATE_APP_ID from --vars-at-org."
    )
    fake = make_gh(
        {
            ORG_VARS: [
                {
                    "variables": [{"name": "SHIPMATE_APPROVERS_TEAM", "value": "platform"}],
                    "total_count": 1,
                }
            ]
        }
    )
    monkeypatch.setattr(onboard, "_run", fake)
    with pytest.raises(SystemExit) as excinfo:
        onboard._refuse_org_assertion_mismatch(ctx(at_org={"SHIPMATE_APPROVERS_TEAM"}, team="ops"))
    assert str(excinfo.value) == (
        "SHIPMATE_APPROVERS_TEAM reaches o/r as platform, but --team says ops. The workflows "
        "read the resolved value, so this run would configure one thing and every later run "
        "would use another. Re-run with --team matching, correct the organization variable "
        "first, or drop SHIPMATE_APPROVERS_TEAM from --vars-at-org."
    )


def test_a_repository_copy_does_not_rescue_a_disagreeing_organization_value(monkeypatch):
    """A repository copy agreeing with --app-id is what runs today, so this looks like a
    working configuration; it breaks the moment the leftover is deleted, which is what
    `_report_org_leftovers` tells the operator to do.

    Mutation: resolve the effective value as `ctx["variables"].get(name, org_value)` -- the
    precedence-aware reading -- which makes this fixture pass.
    """
    fake = make_gh(
        {ORG_VARS: [{"variables": [{"name": "SHIPMATE_APP_ID", "value": "222"}], "total_count": 1}]}
    )
    monkeypatch.setattr(onboard, "_run", fake)
    with pytest.raises(SystemExit) as excinfo:
        onboard._refuse_org_assertion_mismatch(
            ctx(at_org={"SHIPMATE_APP_ID"}, app_id="111", variables={"SHIPMATE_APP_ID": "111"})
        )
    assert str(excinfo.value) == (
        "SHIPMATE_APP_ID reaches o/r as 222, but --app-id says 111. The workflows read the "
        "resolved value, so this run would configure one thing and every later run would use "
        "another. Re-run with --app-id matching, correct the organization variable first, "
        "or drop SHIPMATE_APP_ID from --vars-at-org."
    )


def test_matching_organization_values_pass_and_write_no_variable(monkeypatch, tmp_path):
    """The whole recorded call list is compared against a hand-written constant: an assertion
    that SHIPMATE_APP_ID was not set is satisfied by a run that set nothing at all, and a
    variable write that should have been filtered cannot hide in a membership check. Both
    names this script writes are asserted at organization level, so no variable is written.

    Mutation: refuse unconditionally, which reds this while the two refusal properties stay
    green.
    """
    fake, exit_ = run_main(
        monkeypatch,
        tmp_path,
        {
            ORG_VARS: [
                {
                    "variables": [
                        {"name": "SHIPMATE_APP_ID", "value": "1"},
                        {"name": "SHIPMATE_APPROVERS_TEAM", "value": "ops"},
                    ],
                    "total_count": 2,
                }
            ]
        },
        ["--vars-at-org", "SHIPMATE_APP_ID,SHIPMATE_APPROVERS_TEAM"],
    )
    assert exit_.code == 0
    assert fake.calls == [
        ["gh", "variable", "list", "--json", "name,value"],
        ["gh", "api", "repos/o/r/actions/organization-variables", "--paginate", "--slurp"],
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
        ["gh", "api", RULES],
        ["gh", "api", "-X", "POST", "repos/o/r/rulesets", "--input", "-"],
    ]


def test_a_private_repository_on_free_refuses_every_asserted_name(monkeypatch, tmp_path):
    """GitHub Free excludes private repositories from organization variables outright, so the
    tier bounds every name rather than any one of them. The first two fixtures carry a
    *matching* organization value, so only the plan check can produce a refusal; the third
    carries none, which is the realistic Free shape and the only one that can tell the two
    refusals apart when they are reordered. Its endpoint route is dead under the correct
    order, and exists so that reordering reds on the message rather than on an unrouted read.

    Mutations: delete the `raise`; order the plan check after the endpoint read, which makes
    the third fixture refuse with "no organization variable of that name reaches".
    """
    for name, org_vars, expected in (
        ("SHIPMATE_APP_ID", ORG_APP_ID_MATCHES, FREE_APP_ID),
        ("SHIPMATE_APPROVERS_TEAM", ORG_TEAM_MATCHES, FREE_TEAM),
        ("SHIPMATE_APP_ID", NO_ORG_VARS, FREE_APP_ID),
    ):
        _fake, exit_ = run_main(
            monkeypatch,
            tmp_path,
            {"orgs/o": {"plan": {"name": "free"}}, ORG_VARS: org_vars},
            ["--vars-at-org", name],
            is_private=True,
        )
        assert str(exit_) == expected


def test_an_unreadable_organization_plan_is_refused_like_free(monkeypatch, tmp_path):
    """`plan` is absent from `GET /orgs/{org}` for anyone but an organization owner, and an
    unreadable plan cannot be told from Free. Refusing it is the fail-safe direction.

    Mutation: pass an unknown plan instead of refusing it.
    """
    _fake, exit_ = run_main(
        monkeypatch,
        tmp_path,
        {"orgs/o": {}, ORG_VARS: ORG_APP_ID_MATCHES},
        ["--vars-at-org", "SHIPMATE_APP_ID"],
        is_private=True,
    )
    assert str(exit_) == UNKNOWN_APP_ID


def test_a_personal_account_is_refused_by_name(monkeypatch, tmp_path):
    """`gh api orgs/<owner>` answers 404 for a personal account, which is not an unreadable
    plan: no organization variable can exist there at all. Compared whole, because the raw
    `command failed (1): gh api orgs/o` this replaces names an endpoint the operator never
    typed and no fix at all.

    Mutation: read the plan with `json.loads(_run([...]))` again, which reds on gh's message.
    """
    _fake, exit_ = run_main(
        monkeypatch,
        tmp_path,
        {"orgs/o": ABSENT, ORG_VARS: ORG_APP_ID_MATCHES},
        ["--vars-at-org", "SHIPMATE_APP_ID"],
        is_private=True,
    )
    assert str(exit_) == (
        "o is a personal account, not an organization, so no organization variable can "
        "reach this repository and every asserted name would resolve to empty. Drop "
        "--vars-at-org."
    )


def test_a_legacy_business_plan_is_not_refused(monkeypatch, tmp_path):
    """The check is a deny-list on "free" plus the unreadable case: an allow-list of
    ("team", "enterprise") would refuse the legacy `business` plans, which GitHub does not
    exclude from organization variables.

    Mutation: turn the deny-list into that allow-list.
    """
    _fake, exit_ = run_main(
        monkeypatch,
        tmp_path,
        {"orgs/o": {"plan": {"name": "business"}}, ORG_VARS: ORG_APP_ID_MATCHES},
        ["--vars-at-org", "SHIPMATE_APP_ID"],
        is_private=True,
    )
    assert exit_.code == 0


def test_a_public_repository_never_reads_the_organization_plan(monkeypatch, tmp_path):
    """`plan` is owner-only, so reading it on every run would make an owner-scoped permission
    part of the common path for a check that only bounds private repositories.

    Mutation: read the plan unconditionally -- `orgs/o` is unrouted here, so the fake raises
    as well.
    """
    fake, exit_ = run_main(
        monkeypatch,
        tmp_path,
        {ORG_VARS: ORG_APP_ID_MATCHES},
        ["--vars-at-org", "SHIPMATE_APP_ID"],
    )
    assert exit_.code == 0
    assert [c for c in fake.calls if c == ORG_PLAN_READ] == []


def test_a_private_repository_without_the_flag_reads_no_organization_plan(monkeypatch, tmp_path):
    """Without `--vars-at-org` there is nothing for the tier check to bound -- it returns on its
    first condition -- so the owner-only read buys nothing and its failure would abort the run
    before the first reconciler, over an organization fact this run never uses. `orgs/<owner>` is
    the only org-scoped call `onboard` makes, so a private repository owned by a personal account
    fails on it.

    Driven through `main()` on purpose: the same property at function level is what missed this,
    because a second endpoint on the common path is invisible to a check scoped to the first.

    Mutation: gate the ternary on `ctx["is_private"]` alone, which reads the plan here.
    """
    fake, exit_ = run_main(monkeypatch, tmp_path, {}, [], is_private=True)
    assert exit_.code == 0
    assert [c for c in fake.calls if c == ORG_PLAN_READ] == []


def test_the_organization_read_is_paginated_and_slurped(monkeypatch):
    """`make_gh` keys a `gh api` read on the URL alone, so the fake hands back both pages
    whether or not the flags are passed: resolving a name off the second page proves the loop
    over pages, and only the argv assertion proves the request that produces them.

    Mutations, three: drop `--paginate`; drop `--slurp` -- both red the argv assertion and
    leave the two-page resolution green, which is why that assertion exists; and read
    `pages[0]` alone, which reds the second-page fixture while the absent one stays green.
    """
    two_pages = [
        {"variables": [{"name": "SHIPMATE_APPROVERS_TEAM", "value": "ops"}], "total_count": 1},
        {"variables": [{"name": "SHIPMATE_APP_ID", "value": "1"}], "total_count": 1},
    ]
    fake = make_gh({ORG_VARS: two_pages})
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._refuse_org_assertion_mismatch(ctx(at_org={"SHIPMATE_APP_ID"}))
    assert fake.calls == [ORG_VARS_READ]
    fake = make_gh({ORG_VARS: [NO_ORG_VARS[0], NO_ORG_VARS[0]]})
    monkeypatch.setattr(onboard, "_run", fake)
    with pytest.raises(SystemExit) as excinfo:
        onboard._refuse_org_assertion_mismatch(ctx(at_org={"SHIPMATE_APP_ID"}))
    assert str(excinfo.value) == UNREACHED_APP_ID


def test_a_failed_organization_read_names_its_two_causes(monkeypatch):
    """A token without the repository Variables permission reads a 403, which is not absence:
    reporting it as "no such variable" would send the operator to set a variable that is
    already there. An old `gh` without `--slurp` is the other documented cause, so the hint
    names both. gh's stderr rides along, so a third cause still shows through it.

    Mutation: replace the `raise` with a print and return {}, which turns every failed read
    into "unset at organization level".
    """
    fake = make_gh({ORG_VARS: SystemExit("gh: Forbidden (HTTP 403)")})
    monkeypatch.setattr(onboard, "_run", fake)
    with pytest.raises(SystemExit) as excinfo:
        onboard._refuse_org_assertion_mismatch(ctx(at_org={"SHIPMATE_APP_ID"}))
    assert str(excinfo.value) == (
        "could not read the organization variables reaching o/r: gh: Forbidden (HTTP 403)\n"
        "A fine-grained token needs this repository's Variables read permission, and "
        "`--slurp` needs a recent `gh`. Drop the names from --vars-at-org to skip this check."
    )


def test_no_organization_read_happens_without_the_flag(monkeypatch):
    """Without `--vars-at-org`, `onboard` must behave exactly as it did before this check
    existed -- no extra call, no extra permission. Zero recorded calls is the assertion.

    Mutation: run the read unconditionally, which makes every run pay it.
    """
    fake = make_gh({})
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._refuse_org_assertion_mismatch(ctx())
    assert fake.calls == []


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
            "the rulesets POST was rejected (HTTP 422). Most likely a `shipmate-gate` "
            "ruleset already exists but requires nothing on this branch, because its "
            "enforcement is `evaluate` or `disabled` and the effective-rules read cannot "
            "see it — 422 has other causes, so read `gh api repos/OWNER/REPO/rulesets` "
            "before acting. Set it to active, or delete it and run this again.",
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


#: Owner-agnostic, like the docs guard's selector: the pages publish `<owner>/shipmate/...`
#: as well as this organization's own spelling.
_CALL_PATH = "/shipmate/.github/workflows/"

_EXPECTED_CALLEES = {
    "shipmate.yml": [
        "plan.yml",
        "comment-ops.yml",
        "deploy.yml",
        "drift.yml",
        "apply.yml",
        "apply-all.yml",
        "unlock.yml",
    ],
}


def _callees(text):
    """The engine reusable workflow each job of a rendered shim calls, in document order."""
    doc = yaml.safe_load(text)
    return [
        job["uses"].split(_CALL_PATH, 1)[1].split("@", 1)[0]
        for job in doc["jobs"].values()
        if _CALL_PATH in (job.get("uses") or "")
    ]


def test_every_shim_fence_is_found_and_calls_exactly_the_expected_engine_workflows():
    """The locator reads the workflow file's body out of the docs rather than carrying a
    copy. It must find exactly one fence, and that fence must call every engine reusable
    workflow the file routes to, in document order -- seven jobs, seven pin sites, and a
    locator that found only the first would ship six unpinned calls.

    The expected callee list is hand-written here, never read out of the docs, and the
    whole mapping is compared with `==`.

    Two claims, two mutations, each proved separately:
    - edit the fence's top-level `name:` line -> the locator matches zero fences and refuses;
    - edit a `uses:` filename in the fence -> the callee list differs.
    """
    found = {"shipmate.yml": _callees(onboard._render(ENGINE, "c" * 40, "v9.9.9", ""))}
    assert found == _EXPECTED_CALLEES


def test_the_rendered_pin_is_byte_identical_to_what_repin_consumer_writes(tmp_path):
    """Two writers produce one string. `dev/repin_consumer.py` re-pins a consumer at release
    time; this script writes the first copy. A spacing difference between them makes every
    re-pinned consumer report `differs` forever, and acceptance can never pass.

    Mutation: render the separator as two spaces before the `#`.
    """
    import repin_consumer

    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "shipmate.yml").write_text(
        onboard._render(ENGINE, "c" * 40, "v9.9.9", ""), encoding="utf-8", newline="\n"
    )
    # The real release writer, not an imitation of it. `docs/releasing.md` runs
    # `repin_consumer.main`, which reaches this planner through `_rewrite_and_report` and
    # writes the planned text unchanged.
    planned = repin_consumer._plan_consumer(tmp_path, "d" * 40, "v9.9.10")
    assert len(planned) == 1
    assert planned[0].text == onboard._render(ENGINE, "d" * 40, "v9.9.10", ""), (
        "onboard and repin_consumer disagree on the pin line, so a re-pinned consumer "
        "never reports `ok`"
    )


_EXPECTED_PINS = {"shipmate.yml": 7}


def test_every_shim_is_pinned_at_every_site():
    """One file, seven pins, and a file shipped still carrying `@<engine-sha>` resolves to
    nothing.

    Nothing else can see a missed rewrite. `_callees` splits before the `@`, and the
    byte-identity guard is blind by construction, because a surviving placeholder is not a
    40-hex pin on either side. Two live triggers make that silence expensive: `_DOC_PIN`
    requires the trailing `#` comment, so a docs edit dropping `# see the latest release`
    from one line stops that pin being rewritten; and it is anchored on `ship-iac`, so
    normalising an owner in the docs to `<owner>` would unpin all seven. `_DOC_PIN` stays
    anchored deliberately -- `dev/repin_consumer.py` is anchored the same way and the two
    writers must agree -- and this vector is what makes either edit loud.

    Hand-written, never derived from the docs.

    Mutations: `_DOC_PIN.sub(..., count=1)`, which rewrites one pin of the seven; and delete
    `  # see the latest release` from the `plan` job's `uses:` line in the docs, which
    leaves that one call on `@<engine-sha>`.
    """
    rendered = {"shipmate.yml": onboard._render(ENGINE, "c" * 40, "v9.9.9", "")}
    assert {name: text.count(f"@{'c' * 40} # v9.9.9") for name, text in rendered.items()} == (
        _EXPECTED_PINS
    )
    assert [name for name, text in rendered.items() if "<engine-sha>" in text] == []


def test_state_suffix_is_substituted_into_every_site():
    """Every documented `state_suffix: ""` becomes the operator's value, and the two jobs
    that carry none stay that way.

    The whole vector of (file, job, parsed value) is compared against a hand-written
    constant: asserting one site would leave the other four unpinned, and asserting on a
    substring would be satisfied by the same words appearing in a comment.

    Mutation: substitute into a copy that is then discarded.
    """
    doc = yaml.safe_load(onboard._render(ENGINE, "c" * 40, "v9.9.9", ".state"))
    found = [
        ("shipmate.yml", job_id, job["with"]["state_suffix"])
        for job_id, job in doc["jobs"].items()
        if "state_suffix" in (job.get("with") or {})
    ]
    assert found == [
        ("shipmate.yml", "plan", ".state"),
        ("shipmate.yml", "deploy", ".state"),
        ("shipmate.yml", "drift", ".state"),
        ("shipmate.yml", "targeted", ".state"),
        ("shipmate.yml", "all", ".state"),
    ]


def _shim_ctx(tmp_path):
    return ctx(root=tmp_path, engine=ENGINE, sha="c" * 40, version="v9.9.9")


def _plan_shim(tmp_path):
    """(path to the consumer's shipmate.yml, the text this script would render for it)."""
    path = tmp_path / ".github" / "workflows" / "shipmate.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path, onboard._render(ENGINE, "c" * 40, "v9.9.9", "")


def test_an_identical_file_reports_ok_through_crlf(tmp_path):
    """A CRLF checkout of an otherwise identical shim is not drift: git's autocrlf gives a
    Windows consumer one, and reporting it `differs` would tell every such repository it
    diverges from the published fence when it does not.

    Mutation: read the existing file with `newline=""`, which stops the translation.
    """
    path, text = _plan_shim(tmp_path)
    on_disk = text.replace("\n", "\r\n").encode("utf-8")
    path.write_bytes(on_disk)
    onboard._reconcile_shim(_shim_ctx(tmp_path))
    assert onboard.REPORT == [("ok", "shipmate.yml", "")]
    assert path.read_bytes() == on_disk


def test_a_file_differing_only_in_its_pin_reports_pin_only(tmp_path):
    """A consumer sitting on an older release differs only in its pin, and moving a pin is
    `dev/repin_consumer.py`'s job. Naming that remedy is the whole point of the verb, and it
    is not drift, so it must not set exit 2 and fail the operator's run.

    Mutation: make `_depin` leave the trailing comment in place, so the version comment alone
    reads as `differs`.
    """
    path, _text = _plan_shim(tmp_path)
    older = onboard._render(ENGINE, "d" * 40, "v9.9.8", "")
    path.write_text(older, encoding="utf-8", newline="\n")
    onboard._reconcile_shim(_shim_ctx(tmp_path))
    assert onboard.REPORT == [("pin-only", "shipmate.yml", "run dev/repin_consumer.py")]
    assert path.read_text(encoding="utf-8") == older
    assert onboard._exit_code() == 0


def test_a_locally_edited_file_is_reported_and_not_overwritten(tmp_path):
    """A consumer's own edit to a shim is theirs. Overwriting it is this script exceeding
    its mandate, and it is unrecoverable from the run output.

    Mutation: overwrite on the `differs` branch.
    """
    path, text = _plan_shim(tmp_path)
    edited = text + "# a local edit\n"
    path.write_text(edited, encoding="utf-8", newline="\n")
    onboard._reconcile_shim(_shim_ctx(tmp_path))
    assert onboard.REPORT == [
        ("differs", "shipmate.yml", "differs beyond its pin, not overwritten")
    ]
    assert path.read_text(encoding="utf-8") == edited
    assert onboard._exit_code() == 2


def test_an_absent_file_is_created_with_lf_endings(tmp_path, monkeypatch):
    """The shim is written LF-delimited whatever platform the operator runs on: every other
    copy of it -- the docs, `dev/repin_consumer.py`'s rewrite, the other consumers -- is LF.

    The whole kwargs mapping is compared against a hand-written dict rather than only the
    bytes on disk, for the reason `test_run_passes_stdin_as_bytes_with_text_mode_off` gives:
    translation only happens where the platform newline is `\r\n`, so a bytes-only assertion
    is inert on the Linux runner CI uses and the guard could not fail where it runs.

    Mutation: drop `newline="\\n"` from `write_file`.
    """
    seen = {}
    real = pathlib.Path.write_text

    def fake(self, data, **kwargs):
        seen["kwargs"] = kwargs
        return real(self, data, **kwargs)

    monkeypatch.setattr(pathlib.Path, "write_text", fake)
    path, text = _plan_shim(tmp_path)
    onboard._reconcile_shim(_shim_ctx(tmp_path))
    assert onboard.REPORT == [("created", "shipmate.yml", "")]
    assert seen["kwargs"] == {"encoding": "utf-8", "newline": "\n"}
    assert path.read_bytes().decode("utf-8") == text


def test_main_refuses_a_state_suffix_that_cannot_sit_in_a_yaml_scalar():
    """The suffix is interpolated into every `state_suffix: "<value>"` of the rendered
    workflow file, so a `"` in it writes a file GitHub cannot load.

    Mutation: drop the `_SUFFIX_RE` check. `--key k` does not exist, so `_read_key` raises
    `SystemExit` too -- the assertion is on the message, not on the exception.
    """
    with pytest.raises(SystemExit) as e:
        onboard.main(["--team", "ops", "--app-id", "1", "--key", "k", "--state-suffix", '." #'])
    assert "--state-suffix" in str(e.value)


def test_a_file_still_carrying_the_docs_placeholder_is_not_reported_pin_only(tmp_path):
    """A consumer who pasted the published fence by hand holds `@<engine-sha>`, which is not a
    pin `dev/repin_consumer.py` can move: its pattern requires 40 hex, so it would answer "no
    engine references found". Naming a remedy that cannot work is worse than naming none, so
    that file is `differs`.

    Mutation: widen `_ANY_PIN` from `[0-9a-f]{40}` to `\\S+`.
    """
    path, _text = _plan_shim(tmp_path)
    page = (ENGINE / "docs" / "getting-started.md").read_text(encoding="utf-8")
    path.write_text(onboard._fence(page, "shipmate"), encoding="utf-8", newline="\n")
    onboard._reconcile_shim(_shim_ctx(tmp_path))
    assert onboard.REPORT == [
        ("differs", "shipmate.yml", "the published fence, never pinned: delete it and run again")
    ]


#: Hand-written, not read back from `_reconcile_shims`: a detail string taken from the code
#: it checks passes whatever that code says.
_LEGACY_DETAIL = (
    "the retired six-file layout: `shipmate.yml` carries this file's job now, "
    "so a leftover still firing on its own trigger runs it twice. Delete it by hand."
)


def test_every_retired_filename_present_is_reported_and_never_deleted(tmp_path):
    """A consumer upgrading from the six-file layout keeps those files until they remove them
    by hand: each still fires on its own trigger and so runs a job `shipmate.yml` now runs as
    well, and deleting one for them would discard an edit that is theirs.

    Both halves in one test, because each is satisfied by the wrong reconciler alone: one
    that deleted the files would still emit the rows, and one that reported nothing would
    still leave the files. The whole REPORT is compared against a hand-written constant
    rather than filtered for `differs`, so a row that goes missing and a row for a file that
    is not there both fail.

    Mutation: delete the legacy loop from `_reconcile_shims`. The REPORT assertion reddens
    and the on-disk assertion stays green.
    """
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    for filename in onboard.LEGACY_SHIMS:
        (wf / filename).write_text("# left over\n", encoding="utf-8", newline="\n")
    onboard._reconcile_shims(_shim_ctx(tmp_path))
    assert onboard.REPORT == [
        ("created", "shipmate.yml", ""),
        ("differs", "plan.yml", _LEGACY_DETAIL),
        ("differs", "apply.yml", _LEGACY_DETAIL),
        ("differs", "comment-ops.yml", _LEGACY_DETAIL),
        ("differs", "unlock.yml", _LEGACY_DETAIL),
        ("differs", "deploy.yml", _LEGACY_DETAIL),
        ("differs", "drift.yml", _LEGACY_DETAIL),
    ]
    assert sorted(f.name for f in wf.iterdir()) == sorted(["shipmate.yml", *onboard.LEGACY_SHIMS])


#: Hand-written, not captured from the implementation: a constant pasted from the output
#: passes whatever the output says.
SPLIT_CHECKLIST = """
Still yours — these values are the consumer's, so this script cannot set them.

Per environment, the cloud role and the env identity your layout injects. A
DRY/dynamic backend needs TF_VAR_env and TF_VAR_region, workspace-per-env needs
TF_WORKSPACE, folder-per-env needs neither (CONTRACT.md §Env model). On an apply
environment a cell carrying a workload/<name> tag reads AWS_ROLE_ARN_<WORKLOAD>
first; the plan path has no such fallback (docs/aws.md §Environment variables).

  gh variable set AWS_ROLE_ARN --env dev-eu-plan --body <value>
  gh variable set AWS_REGION --env dev-eu-plan --body <value>
  gh variable set TF_VAR_env --env dev-eu-plan --body <value>
  gh variable set TF_VAR_region --env dev-eu-plan --body <value>
  gh variable set TF_WORKSPACE --env dev-eu-plan --body <value>

  gh variable set AWS_ROLE_ARN --env dev-eu-apply --body <value>
  gh variable set AWS_REGION --env dev-eu-apply --body <value>
  gh variable set TF_VAR_env --env dev-eu-apply --body <value>
  gh variable set TF_VAR_region --env dev-eu-apply --body <value>
  gh variable set TF_WORKSPACE --env dev-eu-apply --body <value>

Repository-wide, both optional:

  gh secret set SHIPMATE_PLAN_PASSPHRASE
  gh variable set SLACK_WEBHOOK --body <value>

By hand:

  Add o/r to the App installation's repository selection, at
  https://github.com/organizations/<org>/settings/apps/shipmate/installations
  — substitute your org and the App name you registered (docs/github-app.md §4).
  The add-repository endpoint accepts PAT-classic tokens only, so it stays a UI step.

  Required reviewers and `Prevent self-review` on dev-eu-apply
  (docs/getting-started.md §Environment setup).

  A CODEOWNERS entry covering /.github/workflows/.

  Commit the workflow file and open the pull request. `shipmate / gate` cannot be
  green on that one: the workflows that produce it are not on the default branch
  yet (CONTRACT.md §Post-plan topology). Merge it with an administrative bypass.
"""


def test_the_checklist_names_every_value_the_script_cannot_set(capsys):
    """The printed block is the only place a consumer learns which values remain.
    A name dropped from it is a repository that looks reconciled and cannot plan.

    The block is compared whole against a hand-written constant, because a membership
    check would pass a block that silently lost one -- and cannot see a wrong `--env`
    argument, a dropped `gh` prefix, or a line that lost its command.

    Mutations, each proven: delete SHIPMATE_PLAN_PASSPHRASE from the block; write
    `--env dev-eu` where the environment half belongs.
    """
    onboard._checklist(ctx(repo="o/r", envs=["dev-eu"], shared=set()))
    assert capsys.readouterr().out == SPLIT_CHECKLIST


#: The shared half of the same block. `_env_names` returns one bare `<env>` for a shared
#: environment, and a reviewer on it stalls every plan cell, so no reviewer line is due.
SHARED_CHECKLIST = """
Still yours — these values are the consumer's, so this script cannot set them.

Per environment, the cloud role and the env identity your layout injects. A
DRY/dynamic backend needs TF_VAR_env and TF_VAR_region, workspace-per-env needs
TF_WORKSPACE, folder-per-env needs neither (CONTRACT.md §Env model). On an apply
environment a cell carrying a workload/<name> tag reads AWS_ROLE_ARN_<WORKLOAD>
first; the plan path has no such fallback (docs/aws.md §Environment variables).

  gh variable set AWS_ROLE_ARN --env dev-eu --body <value>
  gh variable set AWS_REGION --env dev-eu --body <value>
  gh variable set TF_VAR_env --env dev-eu --body <value>
  gh variable set TF_VAR_region --env dev-eu --body <value>
  gh variable set TF_WORKSPACE --env dev-eu --body <value>

Repository-wide, both optional:

  gh secret set SHIPMATE_PLAN_PASSPHRASE
  gh variable set SLACK_WEBHOOK --body <value>

By hand:

  Add o/r to the App installation's repository selection, at
  https://github.com/organizations/<org>/settings/apps/shipmate/installations
  — substitute your org and the App name you registered (docs/github-app.md §4).
  The add-repository endpoint accepts PAT-classic tokens only, so it stays a UI step.

  A CODEOWNERS entry covering /.github/workflows/.

  Commit the workflow file and open the pull request. `shipmate / gate` cannot be
  green on that one: the workflows that produce it are not on the default branch
  yet (CONTRACT.md §Post-plan topology). Merge it with an administrative bypass.
"""


def test_the_checklist_asks_for_no_reviewer_on_a_shared_environment(capsys):
    """A shared env is bound by plan cells and the nightly drift run too, so a required
    reviewer on it stalls them rather than gating an apply: the reviewer line is due only
    for an `<env>-apply`. The split fixture above cannot reach this branch.

    Mutation: select the reviewer line on `role == "apply"` alone, which a bare shared
    environment satisfies.
    """
    onboard._checklist(ctx(repo="o/r", envs=["dev-eu"], shared={"dev-eu"}))
    assert capsys.readouterr().out == SHARED_CHECKLIST


def test_a_plan_environment_with_a_branch_policy_reports_the_policy_alone(monkeypatch):
    """GitHub synthesizes a `branch_policy` protection rule for any environment that has
    a deployment branch policy (`scripts/doctor` filters the same entry). Counted, it
    makes every policy-carrying plan environment also report "it carries protection
    rules (branch_policy), which stall plan cells" -- false, because a branch policy
    refuses the cell rather than delaying it, and it names a rule the consumer cannot
    find in the UI.

    Mutation: drop the `!= "branch_policy"` filter from `_plan_drift`.
    """
    fake = make_gh(
        {
            "repos/o/r/environments/dev-eu": SystemExit("gh: Not Found (HTTP 404)"),
            "repos/o/r/environments/dev-eu-plan": {
                "deployment_branch_policy": CUSTOM_POLICY,
                "protection_rules": [{"type": "branch_policy"}],
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
    assert onboard.REPORT == [
        (
            "differs",
            "dev-eu-plan",
            "it carries a deployment branch policy, which blocks every plan cell whose "
            "pull request targets a branch the policy does not name. Removing a "
            "protection a consumer set is not this script's call, so it is reported and "
            "left alone.",
        ),
        ("ok", "dev-eu-apply", ""),
        ("ok", "dev-eu-apply branch policy", "main"),
    ]


def test_the_checklist_skips_an_environment_the_reconciler_left_alone(capsys):
    """`_reconcile_envs` touches neither half of an environment holding both a bare
    `<env>` and an `<env>-apply`, because which the engine binds is undecided. Naming
    those halves in the checklist tells a consumer to set variables on environments the
    run refused to reconcile, one of which it may then delete.

    `dev-us` contributes nothing, so the expected block is the split constant unchanged.

    Mutation: drop the `env not in ctx["unresolved"]` filter from `_checklist`.
    """
    onboard._checklist(ctx(envs=["dev-eu", "dev-us"], unresolved={"dev-us"}))
    assert capsys.readouterr().out == SPLIT_CHECKLIST


def test_the_checklist_does_not_tell_a_dry_run_to_commit_a_file_it_did_not_write(capsys):
    """--dry-run writes no workflow file, so the closing step is a re-run, not a commit. The
    expected block is the split constant with that one line substituted by hand.

    Mutation: drop the `_DRY` branch from the closing line.
    """
    onboard._DRY = True
    onboard._checklist(ctx())
    assert capsys.readouterr().out == SPLIT_CHECKLIST.replace(
        "  Commit the workflow file",
        "  Re-run without --dry-run, then commit the workflow file",
    )


def test_a_bare_env_alongside_only_a_plan_env_is_reported_as_ambiguous(monkeypatch):
    """The half-migrated repository: `dev-eu` and `dev-eu-plan`, no `dev-eu-apply`. The
    probe reads the unused naming alone, so it refuses on `dev-eu` without ever looking
    at the half that is there -- and the remedy it names is deleting `dev-eu`, never the
    `dev-eu-plan` the engine binds.

    Mutation: probe the bound naming instead of the unused one
    (`[n for n, _r in _env_names(env, ctx["shared"])]` in the comprehension). It still
    refuses, on `dev-eu-plan`, and tells the operator to delete an environment the
    engine binds -- which the recorded reads and the message below both catch.
    """
    fake = make_gh(
        {
            "repos/o/r/environments/dev-eu": {"deployment_branch_policy": CUSTOM_POLICY},
            # Routed though a conforming run never reads them, for the mutation above.
            "repos/o/r/environments/dev-eu-plan": {"deployment_branch_policy": None},
            "repos/o/r/environments/dev-eu-apply": SystemExit("gh: Not Found (HTTP 404)"),
            "repos/o/r/environments/dev-eu-apply/deployment-branch-policies": {
                "total_count": 0,
                "branch_policies": [],
            },
        }
    )
    monkeypatch.setattr(onboard, "_run", fake)
    context = ctx()
    onboard._reconcile_envs(context)
    assert fake.calls == [["gh", "api", "repos/o/r/environments/dev-eu"]]
    assert onboard.REPORT == [SPLIT_CONFLICT]
    assert context["unresolved"] == {"dev-eu"}


def test_shared_mode_reports_the_ambiguity_too(monkeypatch):
    """Running once without `--shared` and once with it produces all three environments.
    The second run must not silently bind the bare one: the ambiguity is doctor's
    shared-mode warning, so the probe runs in shared mode as well.

    Mutation: guard the probe with `if env not in ctx["shared"]`, which reconciles
    `dev-eu` and reports nothing.
    """
    fake = make_gh(
        {
            "repos/o/r/environments/dev-eu": {"deployment_branch_policy": CUSTOM_POLICY},
            "repos/o/r/environments/dev-eu-plan": {"deployment_branch_policy": None},
            "repos/o/r/environments/dev-eu-apply": {"deployment_branch_policy": CUSTOM_POLICY},
            "repos/o/r/environments/dev-eu/deployment-branch-policies": {
                "total_count": 1,
                "branch_policies": [{"name": "main"}],
            },
        }
    )
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_envs(ctx(shared={"dev-eu"}))
    assert fake.calls == [
        ["gh", "api", "repos/o/r/environments/dev-eu-plan"],
        ["gh", "api", "repos/o/r/environments/dev-eu-apply"],
    ]
    assert onboard.REPORT == [SHARED_CONFLICT]


def test_a_shared_environment_carrying_protection_rules_is_reported(monkeypatch):
    """A shared env is one bare environment on both paths, so a required reviewer there
    gates the plan cells and the nightly drift run too -- GitHub has no per-job filter.
    `doctor` warns on it; `_env_names` collapses the env to `role == "apply"`, which
    would otherwise report a conforming branch policy as plain `ok`.

    Mutation: drop the `_report_shared_approval` call from `_reconcile_env`, which loses
    the `differs` line. Dropping its `name in ctx["shared"]` test does nothing here --
    both suffixed names are absent in this fixture -- and reddens
    `test_a_split_apply_environments_reviewers_are_not_reported` instead.
    """
    fake = make_gh(
        {
            "repos/o/r/environments/dev-eu": {
                "deployment_branch_policy": CUSTOM_POLICY,
                "protection_rules": [
                    {"type": "branch_policy"},
                    {"type": "required_reviewers"},
                    {"type": "wait_timer"},
                ],
            },
            "repos/o/r/environments/dev-eu-plan": SystemExit("gh: Not Found (HTTP 404)"),
            "repos/o/r/environments/dev-eu-apply": SystemExit("gh: Not Found (HTTP 404)"),
            "repos/o/r/environments/dev-eu/deployment-branch-policies": {
                "total_count": 1,
                "branch_policies": [{"name": "main"}],
            },
        }
    )
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_envs(ctx(shared={"dev-eu"}))
    assert fake.calls == [
        ["gh", "api", "repos/o/r/environments/dev-eu-plan"],
        ["gh", "api", "repos/o/r/environments/dev-eu-apply"],
        ["gh", "api", "repos/o/r/environments/dev-eu"],
        ["gh", "api", "repos/o/r/environments/dev-eu/deployment-branch-policies"],
    ]
    assert onboard.REPORT == [
        ("ok", "dev-eu", ""),
        (
            "differs",
            "dev-eu",
            "it carries protection rules (required_reviewers, wait_timer) and is shared, "
            "so the plan cells and the nightly drift run do not start immediately either. "
            "To gate applies alone, split it into `dev-eu-plan` and `dev-eu-apply` and "
            "drop `dev-eu` from --shared and SHIPMATE_SHARED_ENVS.",
        ),
        ("ok", "dev-eu branch policy", "main"),
    ]
    assert onboard._exit_code() == 2


def test_a_split_apply_environments_reviewers_are_not_reported(monkeypatch):
    """Required reviewers on `<env>-apply` are the reviewer gate docs/hardening.md row 6
    asks for, not drift: only a *shared* bare environment's rules stall the plan path.

    Mutation: drop the `name in ctx["shared"]` test in `_report_shared_approval`.
    """
    fake = make_gh(
        {
            "repos/o/r/environments/dev-eu": SystemExit("gh: Not Found (HTTP 404)"),
            "repos/o/r/environments/dev-eu-plan": {"deployment_branch_policy": None},
            "repos/o/r/environments/dev-eu-apply": {
                "deployment_branch_policy": CUSTOM_POLICY,
                "protection_rules": [{"type": "required_reviewers"}],
            },
            "repos/o/r/environments/dev-eu-apply/deployment-branch-policies": {
                "total_count": 1,
                "branch_policies": [{"name": "main"}],
            },
        }
    )
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_envs(ctx())
    assert onboard.REPORT == [
        ("ok", "dev-eu-plan", ""),
        ("ok", "dev-eu-apply", ""),
        ("ok", "dev-eu-apply branch policy", "main"),
    ]
    assert onboard._exit_code() == 0


def test_a_diverging_repository_app_id_is_refused(monkeypatch):
    """`SHIPMATE_APP_ID` is reported-not-overwritten, but `--app-id` also pins the gate
    ruleset's `integration_id` and selects whose PEM lands on `shipmate-engine`. Letting
    the two diverge writes a required status check the workflows -- which mint their
    token from the variable -- can never satisfy, and the default branch stays blocked
    until an admin deletes the ruleset. Refusing costs no extra call: `main` has the
    variables in hand before the first reconciler.

    The agreeing case is built with `"".join`, so the two equal values are distinct
    objects: `"111"` twice is one interned literal, and an identity comparison would pass
    over it.

    Mutations, each proven: downgrade the refusal to `report("differs", ...)`; compare
    with `is` rather than `==`.
    """
    with pytest.raises(SystemExit) as e:
        onboard._refuse_diverging_app_id("222", {"SHIPMATE_APP_ID": "111"})
    assert "111" in str(e.value) and "222" in str(e.value)
    assert onboard.REPORT == []

    onboard._refuse_diverging_app_id("".join("111"), {"SHIPMATE_APP_ID": "111"})
    onboard._refuse_diverging_app_id("222", {})
    assert onboard.REPORT == []

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
ec = load_script("env-config")


def make_gh(routes):
    """A fake `_run`: records every invocation, answers reads from `routes`,
    returns "" for every write.

    A read is `gh api <path>` with no `-X`, keyed by the path, or `gh secret list ...` /
    `gh variable list ...`, keyed by its whole command line; anything else is a write and
    is only recorded. A `git` call is keyed by its whole command line and answered raw, ""
    when unrouted: to `git ls-files` that is "nothing tracked", the `todo` direction. An
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
        elif args[0] == "git":
            return routes.get(" ".join(args), "")
        else:
            return ""
        if key not in routes:
            raise AssertionError(f"unrouted read: {key}")
        answer = routes[key]
        if isinstance(answer, SystemExit):
            raise answer
        return json.dumps(answer)

    _run.calls = calls  # ty: ignore[unresolved-attribute]
    _run.stdin = stdins  # ty: ignore[unresolved-attribute]
    _run.secrets = scrubbed  # ty: ignore[unresolved-attribute]
    return _run


ENGINE_PATH = "repos/o/r/environments/shipmate-engine"
ENGINE_POLICIES = f"{ENGINE_PATH}/deployment-branch-policies"
ENGINE_SECRETS = f"{ENGINE_PATH}/secrets?per_page=100"
# Named without the word ruff S105 flags: this is a command line, not a credential.
REPO_KEY_LIST = "gh secret list --json name"
VARIABLE_LIST = "gh variable list --json name,value"
RULES = "repos/o/r/rules/branches/main?per_page=100"
REMOTE_SHIM = "repos/o/r/contents/.github/workflows/shipmate.yml?ref=main"
CUSTOM_POLICY = {"protected_branches": False, "custom_branch_policies": True}


def ctx(**over):
    base = {
        "repo": "o/r",
        "default_branch": "main",
        "app_id": "1",
        "key": "-----BEGIN-----\npem\n",
        "envs": ["dev-eu"],
        "stacks": [],
        "shared": set(),
        "unresolved": set(),
        "root": None,
        "engine": None,
        "variables": {},
        "sha": "a" * 40,
        "version": "v0.26.0",
        "is_private": False,
        "shim_on_default": False,
        "engine_secrets": set(),
        "repo_secrets": set(),
        "table": None,
        "ruleset_deferred": False,
        "apply_envs": {},
        "review_count": 0,
        "tags_by_stack": {},
        "tagged": {"dev-eu": frozenset()},
        "tree_errors": [],
        "cells": [],
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


def test_no_environment_name_derives_no_environment():
    """A repository adopted before any stack carries an env tag, with no table entry yet,
    has nothing to bind, and that is not an error.

    Mutation: restore the refusal of an empty set.
    """
    assert onboard._derive_envs(set()) == []


def test_dry_run_issues_no_write(monkeypatch):
    """`--dry-run` reads and reports; it must not run a single write command.

    Mutation: make `write` fall through to `_run` when `_DRY` is set.
    """
    fake = make_gh({})
    monkeypatch.setattr(onboard, "_run", fake)
    monkeypatch.setattr(onboard, "_DRY", True)
    onboard.REPORT.clear()
    onboard.write("create", "thing", ["gh", "api", "-X", "PUT", "x"])
    assert fake.calls == []
    assert onboard.REPORT == [("would create", "thing", "")]


def write_table(root, entry):
    """A `.github/shipmate-config.yml` under `root` with one `dev-eu` entry ending in `entry`,
    which is indented as the file needs it."""
    (root / ".github").mkdir(exist_ok=True)
    (root / ".github" / "shipmate-config.yml").write_text(
        f"layout: tf_vars\n\nenvironments:\n  dev-eu:\n    region: eu-west-1\n{entry}",
        encoding="utf-8",
        newline="\n",
    )


def test_a_shared_entry_no_stack_tags_is_provisioned(monkeypatch, tmp_path, capsys):
    """A `shared: true` entry is provisioned like any other, tagged or not: its bare
    environment exists before the first pull request tagging a stack into it merges.

    Mutation: restore the loop refusing a shared name outside the tag-derived environments.
    """
    write_table(tmp_path, "    shared: true\n")
    policies = {"repos/o/r/environments/dev-eu/deployment-branch-policies": ABSENT}
    _, exc = run_main(monkeypatch, tmp_path, policies, ["--dry-run"], membership=({}, {}))
    assert exc.code == 0
    assert _would_create("dev-eu") == ["dev-eu", "dev-eu branch policy"]


def test_derived_environment_failing_the_regex_is_refused():
    """An env/* tag becomes an API path segment and a `gh --env` argument.

    Mutation: drop the `_ENV_RE` loop, so `../admin` is returned as an environment.
    """
    with pytest.raises(SystemExit) as e:
        onboard._derive_envs({"../admin": ["s"]})
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
    """`is_private` decides what the reviewer checklist item tells the operator, and nothing
    else reads `_repo_facts`'s third element -- every other test either refuses before the
    return or stubs the function. Both halves are needed: the tuple compared whole catches a
    wrong key, and the argv compared whole catches `isPrivate` dropping out of the `--json`
    list, which the payload alone cannot, because a recording fake answers the same JSON
    whatever it is asked for.

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
    """The slug is interpolated into API paths. Mutation: drop the `REPO_RE`
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


def _facts_with_branch(monkeypatch, branch):
    monkeypatch.setattr(
        onboard,
        "_run",
        lambda args, secrets=(), stdin=None: json.dumps(
            {"nameWithOwner": "o/r", "defaultBranchRef": {"name": branch}, "isPrivate": False}
        ),
    )


@pytest.mark.parametrize("branch", ["a,b", "1.0", "main+"])
def test_repo_facts_refuses_a_branch_the_trigger_cannot_carry_unquoted(monkeypatch, branch):
    """The branch lands unquoted in `branches: [<branch>]`: `a,b` is two YAML list items,
    `1.0` a float, and `main+` a GitHub filter pattern rather than the branch. One case per
    class; the message is compared whole.

    Mutation: drop the `_BRANCH_RE` check from `_repo_facts`.
    """
    _facts_with_branch(monkeypatch, branch)
    with pytest.raises(SystemExit) as e:
        onboard._repo_facts()
    assert str(e.value) == (
        f"the default branch {branch!r} cannot be written unquoted into the workflow file's "
        "`push: branches:` filter, so this script does not onboard it. Follow "
        "docs/getting-started.md by hand and edit `branches:` to match the branch."
    )


def test_a_branch_with_a_slash_is_accepted_and_quoted_in_every_api_path(monkeypatch):
    """`/` and `.` are legal in a branch and in the trigger, but `/` inside a path segment
    or a query value must be escaped; GitHub decodes both spellings.

    Mutations: drop `safe=""` from the `_gate_entry` quote; drop it from the
    `_shim_on_default` quote.
    """
    _facts_with_branch(monkeypatch, "release/1.x")
    assert onboard._repo_facts() == ("o/r", "release/1.x", False)
    rules = "repos/o/r/rules/branches/release%2F1.x?per_page=100"
    shim = "repos/o/r/contents/.github/workflows/shipmate.yml?ref=release%2F1.x"
    fake = make_gh({rules: [], shim: {"type": "file"}})
    monkeypatch.setattr(onboard, "_run", fake)
    assert onboard._gate_entry(ctx(default_branch="release/1.x")) == (None, None, 0)
    assert onboard._shim_on_default("o/r", "release/1.x") is True
    assert fake.calls == [["gh", "api", rules], ["gh", "api", shim]]


def test_run_passes_stdin_as_bytes_with_text_mode_off(monkeypatch):
    r"""`text=True` wraps the child's stdin in a `TextIOWrapper` that rewrites every
    \n to `os.linesep`, so the PEM stored on Windows is not the PEM that was read.

    The whole kwargs mapping is compared against a hand-written dict rather than the
    bytes a child observes: the translation only happens where `os.linesep` is
    `\r\n`, so an observed-bytes assertion is inert on the Linux runner CI uses.

    Mutation: add `text=True` back to `_shipmate.run`'s `subprocess.run` call.
    """
    seen = {}

    def fake(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, b"out", b"")

    monkeypatch.setattr(subprocess, "run", fake)
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

    Three mutations redden it: drop the `scrub` call around the stderr, so the token
    appears verbatim; drop the `raise` and return `p.stdout`, so a failed write is
    reported as done; or drop the `+ f"\\n{scrub(stderr, secrets)}"` clause, which also
    takes `_gh_json_or_none`'s only channel for spotting an `HTTP 404`.
    """
    with pytest.raises(SystemExit) as e:
        onboard._run(
            [sys.executable, "-c", "import sys; sys.stderr.write('tok-' + 'abc'); sys.exit(3)"],
            secrets=("tok-abc",),
        )
    assert "tok-abc" not in str(e.value)
    assert "***" in str(e.value)
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


def test_main_refuses_a_non_numeric_app_id():
    """Mutation: `_APP_ID_RE.fullmatch` to `args.app_id.isdigit()`, which accepts
    the superscript digit below."""
    with pytest.raises(SystemExit) as e:
        onboard.main(["--app-id", "²", "--key", "k"])
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
    onboard.write("set", "key", ["gh", "secret", "set", "X"], stdin="pem", secrets=("pem",))
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

    Mutations, each proven: delete `_reconcile_env(ctx, ENGINE_ENV, "apply")`; delete
    `_reconcile_key(ctx)`; swap those two, which writes the key to an environment that
    does not exist yet; delete `_reconcile_envs(ctx)`; delete `_reconcile_variables(ctx)`;
    delete `_reconcile_ruleset(ctx)`; delete `_reconcile_shim(ctx)`; delete `_checklist(ctx)`;
    `_repo_root()` back to `pathlib.Path.cwd()`; delete
    `_refuse_diverging_app_id(args.app_id, variables)`, which is the only guard against a
    ruleset pinned to an App the workflows do not use; delete `sys.exit(_exit_code())`;
    swap two reconcilers; move the `"shim_on_default"` read into
    `_reconcile_ruleset`, after the first write, where a 403 aborts a half-written run; move
    the `_engine_secrets` and `_repo_secrets` reads below `_reconcile_env(ctx, ENGINE_ENV,
    "apply")`, after the first write; delete
    `_refuse_missing_key(ctx)`, which lets a run with no key create environments before
    `_reconcile_key` finds nothing to set. `_read_key` sits in a conditional expression,
    whose call `_calls_in_order` still lists.
    """
    tree = ast.parse((ENGINE / "scripts" / "onboard").read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    stages = [c for c in _calls_in_order(main) if c.startswith("_") or c.startswith("sys.exit")]
    assert stages == [
        "_APP_ID_RE.fullmatch(args.app_id)",
        "_read_key(args.key)",
        "_engine_pin(engine)",
        "_repo_root()",
        "_repo_facts()",
        "_env_membership()",
        "_variables()",
        "_refuse_diverging_app_id(args.app_id, variables)",
        "_resolve_shared(root, repo, variables)",
        "_derive_envs(set(stacks_by_env) | set((table or {}).get('environments', {})))",
        "_shim_on_default(repo, default_branch)",
        "_engine_secrets(repo)",
        "_repo_secrets()",
        "_refuse_missing_key(ctx)",
        "_reconcile_env(ctx, ENGINE_ENV, 'apply')",
        "_reconcile_key(ctx)",
        "_reconcile_envs(ctx)",
        "_reconcile_variables(ctx)",
        "_reconcile_ruleset(ctx)",
        "_reconcile_shim(ctx)",
        "_checklist(ctx)",
        "sys.exit(_exit_code())",
        "_exit_code()",
    ]


_CONFORMING_ENGINE = {
    ENGINE_PATH: {"deployment_branch_policy": CUSTOM_POLICY},
    ENGINE_POLICIES: {"total_count": 1, "branch_policies": [{"name": "main"}]},
}
#: The secret-name set `main` reads when the App key is already on `shipmate-engine`.
PLACED = {"SHIPMATE_APP_PRIVATE_KEY"}


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
        }
    )
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_env(ctx(), onboard.ENGINE_ENV, "apply")
    onboard._reconcile_key(ctx())
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
        ["gh", "secret", "set", "SHIPMATE_APP_PRIVATE_KEY", "--env", "shipmate-engine"],
    ]


def test_the_key_reaches_gh_secret_set_on_stdin_and_as_a_secret(monkeypatch):
    """`gh secret set` has no `--body` sentinel for stdin -- `--body -` stores the
    literal one-character string `-` -- and the PEM must be in `secrets` so a failed
    write cannot echo it.

    Mutation: pass the PEM as `["--body", ctx["key"]]` instead of on stdin; or drop
    `secrets=(ctx["key"],)`.
    """
    fake = make_gh({})
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
        }
    )
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_env(ctx(), onboard.ENGINE_ENV, "apply")
    onboard._reconcile_key(ctx(engine_secrets=PLACED))
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
    ]
    assert body_of(
        fake, ["gh", "api", "-X", "PUT", "repos/o/r/environments/shipmate-engine", "--input", "-"]
    ) == {"deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}}


def test_conforming_engine_environment_writes_nothing(monkeypatch):
    """A second run over a configured repository must change nothing: two reads (the
    secret names are read in `main`), no write, and every report line `ok` so the exit
    code stays 0.

    Mutation: drop the `custom_branch_policies` test in `_reconcile_env`, so a
    conforming environment is PUT again.
    """
    fake = make_gh(dict(_CONFORMING_ENGINE))
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_env(ctx(), onboard.ENGINE_ENV, "apply")
    onboard._reconcile_key(ctx(engine_secrets=PLACED))
    assert fake.calls == [
        ["gh", "api", "repos/o/r/environments/shipmate-engine"],
        ["gh", "api", "repos/o/r/environments/shipmate-engine/deployment-branch-policies"],
    ]
    assert [verb for verb, _subject, _detail in onboard.REPORT] == ["ok", "ok", "ok", "ok"]
    assert onboard._exit_code() == 0


def test_repository_level_key_is_deleted(monkeypatch):
    """A repository-level copy of the App key defeats the environment scoping
    entirely -- a workflow on any branch can read it -- so it is removed, not
    reported.

    Mutation: downgrade the deletion to `report("differs", ...)`.
    """
    fake = make_gh(dict(_CONFORMING_ENGINE))
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_env(ctx(), onboard.ENGINE_ENV, "apply")
    onboard._reconcile_key(ctx(engine_secrets=PLACED, repo_secrets=PLACED))
    assert fake.calls == [
        ["gh", "api", "repos/o/r/environments/shipmate-engine"],
        ["gh", "api", "repos/o/r/environments/shipmate-engine/deployment-branch-policies"],
        ["gh", "secret", "delete", "SHIPMATE_APP_PRIVATE_KEY"],
    ]
    assert [(verb, subject) for verb, subject, _detail in onboard.REPORT] == [
        ("ok", "shipmate-engine"),
        ("ok", "shipmate-engine branch policy"),
        ("ok", "shipmate-engine SHIPMATE_APP_PRIVATE_KEY"),
        ("delete", "repository secret SHIPMATE_APP_PRIVATE_KEY"),
    ]


def test_shared_mode_binds_one_bare_environment(monkeypatch):
    """An environment whose table entry holds `shared: true` is one bare
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
    onboard._reconcile_env(ctx(), onboard.ENGINE_ENV, "apply")
    onboard._reconcile_key(ctx(engine_secrets=PLACED))
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
    ]
    assert onboard.REPORT == [
        ("ok", "shipmate-engine", ""),
        ("create", "shipmate-engine branch policy", "main"),
        (
            "differs",
            "shipmate-engine branch policy",
            "also permits release/*: a workflow on those branches can still claim what "
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
    "present. Nothing binds `dev-eu`, so nothing was created or changed for `dev-eu`. "
    "Delete `dev-eu`, or set `shared: true` in `environments.dev-eu` so the engine "
    "binds the bare `dev-eu` instead.",
)
SHARED_CONFLICT = (
    "differs",
    "dev-eu",
    "the engine binds `dev-eu` for `dev-eu`, and `dev-eu-plan` and `dev-eu-apply` are "
    "also present. Nothing binds `dev-eu-plan` and `dev-eu-apply`, so nothing was created "
    "or changed for `dev-eu`. Delete `dev-eu-plan` and `dev-eu-apply`, or drop "
    "`shared: true` from `environments.dev-eu`.",
)


def test_a_bare_env_alongside_an_apply_env_is_reported_as_unused(monkeypatch):
    """Which naming the engine binds depends on the table's `shared` key, so a repository
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
    it, exit 0, and then be refused by every later run -- with the pair it just wrote
    never reconciled again.

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
    """The mirror of the case above, reached by marking a split repository's environment
    `shared: true`: creating the bare `dev-eu` beside the pair leaves the same unused
    naming behind, so it is refused rather than written.

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
    """A required reviewer or a wait timer on a plan environment stalls that
    environment's plan cells and every drift sweep covering it. It is drift, not something
    to strip: removing a protection a consumer set is not this script's call.

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
    and the repository has no variable, no rule and no workflow file in the checkout. The
    remote default branch holds one (`shim_on_default`), or the ruleset POST would be
    deferred rather than reached.

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
    onboard._reconcile_env(ctx(), onboard.ENGINE_ENV, "apply")
    onboard._reconcile_key(ctx(repo_secrets=PLACED))
    onboard._reconcile_envs(ctx())
    onboard._reconcile_variables(ctx())
    onboard._reconcile_ruleset(ctx(shim_on_default=True))
    onboard._reconcile_shim(ctx(root=tmp_path, engine=ENGINE))
    onboard._checklist(ctx(root=tmp_path))
    assert list(tmp_path.iterdir()) == []
    assert fake.calls == [
        ["gh", "api", "repos/o/r/environments/shipmate-engine"],
        ["gh", "api", "repos/o/r/environments/shipmate-engine/deployment-branch-policies"],
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
        "would create",
        "would create",
    ]


def test_absent_variables_are_set_from_the_flags_and_no_tool_version_is_written(monkeypatch):
    """A repository with no variables gets exactly the one the workflows still read. The
    tool versions are not among them: `actions/setup` takes both from the release's own
    VERSIONS file, so a repository copy would only be a second source of truth.

    The whole recorded call list is compared against a hand-written constant: an assertion
    that TERRAMATE_VERSION is absent is satisfied by a run that wrote nothing at all.

    Mutation: add a second `write("set", ...)` for TERRAMATE_VERSION to
    `_reconcile_variables`.
    """
    fake = make_gh({VARIABLE_LIST: []})
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_variables(ctx(variables=onboard._variables()))
    assert fake.calls == [
        ["gh", "variable", "list", "--json", "name,value"],
        ["gh", "variable", "set", "SHIPMATE_APP_ID", "--body", "1"],
    ]


def test_variable_names_are_matched_uppercased(monkeypatch):
    """The API returns variable names uppercased whatever case they were created in, so
    a case-sensitive lookup would set a variable that is already there.

    Mutation: drop the `.upper()` in `_variables`.
    """
    fake = make_gh({VARIABLE_LIST: [{"name": "shipmate_app_id", "value": "1"}]})
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_variables(ctx(variables=onboard._variables()))
    assert fake.calls == [["gh", "variable", "list", "--json", "name,value"]]
    assert onboard.REPORT == [("ok", "SHIPMATE_APP_ID", "1")]


ORG_VARS = "repos/o/r/actions/organization-variables"
ORG_VARS_READ = ["gh", "api", ORG_VARS, "--paginate", "--slurp"]
ABSENT = SystemExit("gh: Not Found (HTTP 404)")
NO_ORG_VARS = [{"variables": [], "total_count": 0}]

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
    REMOTE_SHIM: ABSENT,
}


#: One stack, `stacks/app`, tagged for `dev-eu`: what `_env_membership` returns in `run_main`.
ONE_STACK = ({"dev-eu": ["stacks/app"]}, {"stacks/app": ["env/dev-eu"]})


def run_main(monkeypatch, tmp_path, extra_routes, argv, key=True, membership=ONE_STACK):
    """Drive `main()` over `FRESH_ROUTES` plus `extra_routes`, returning (fake, SystemExit).

    Only the reads `main` does before its first reconciler are stubbed -- the git, terramate
    and `gh repo view` reads, each with its own test. Everything below them runs for real
    against the fake, which is what makes the order of the reads and writes observable.
    `key=False` passes no `--key`; `membership=None` leaves `_env_membership` real. The run
    starts in `tmp_path`, the checkout root.
    """
    fake = make_gh({**FRESH_ROUTES, **extra_routes})
    monkeypatch.setattr(onboard, "_run", fake)
    monkeypatch.setattr(onboard, "_engine_pin", lambda engine: ("a" * 40, "v0.26.0"))
    monkeypatch.setattr(onboard, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(onboard, "_repo_facts", lambda: ("o/r", "main", False))
    if membership is not None:
        monkeypatch.setattr(onboard, "_env_membership", lambda: membership)
    monkeypatch.chdir(tmp_path)
    pem = tmp_path / "key.pem"
    pem.write_text("-----BEGIN-----\npem\n", encoding="utf-8", newline="\n")
    with pytest.raises(SystemExit) as excinfo:
        onboard.main(["--app-id", "1", *(["--key", str(pem)] if key else []), *argv])
    return fake, excinfo.value


def test_a_python_without_pyyaml_refuses_before_any_read(monkeypatch):
    """The install command per platform, whole, and nothing read or written first.

    Mutation: move the check after `_repo_facts()`, the first `gh` call -- its stub fails the
    test."""

    def unreachable(*args):
        pytest.fail("onboard read something before refusing a missing PyYAML")

    for name in ("_run", "_engine_pin", "_repo_root", "_repo_facts", "_read_key"):
        monkeypatch.setattr(onboard, name, unreachable)
    monkeypatch.setitem(sys.modules, "yaml", None)
    with pytest.raises(SystemExit) as excinfo:
        onboard.main(["--app-id", "1"])
    assert excinfo.value.code == (
        "onboard needs PyYAML. Install it for the python you run onboard with: sudo apt install "
        "python3-yaml (Debian or Ubuntu), brew install pyyaml (macOS), python -m pip install "
        "pyyaml (Windows or any other)."
    )


def test_a_whole_run_writes_one_file_and_no_configuration(monkeypatch, tmp_path):
    """`onboard` moves no pin -- `_reconcile_shim` reports `pin-only` and leaves it, and that
    status never reaches `_exit_code`. A `.github/shipmate-config.yml` written here could therefore
    hand a repository still pinned to an older engine a file that engine refuses, silently,
    because a top-level key it predates is rejected outright. So the file stays a checklist
    item for a human who merges it in the right order.

    The whole set of files under the checkout is compared against a hand-written constant
    rather than the absence of one name: a write added under any other name reddens here.
    `key.pem` is this harness's own input, not something the run created. The drift file is
    the consumer's to name, so `shipmate.yml` is the only workflow file a run writes.

    Mutations: write a `.github/shipmate-config.yml` from `_checklist`; render the `shipmate drift`
    fence to `.github/workflows/shipmate-drift.yml` from `main`.
    """
    run_main(monkeypatch, tmp_path, {}, [])
    assert sorted(
        p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()
    ) == [".github/workflows/shipmate.yml", "key.pem"]


KEY = "SHIPMATE_APP_PRIVATE_KEY"
#: Hand-written: `main`'s reads up to the missing-key refusal, and nothing after them.
READS_BEFORE_KEY_REFUSAL = [
    ["gh", "variable", "list", "--json", "name,value"],
    ["gh", "api", REMOTE_SHIM],
    ["gh", "api", ENGINE_SECRETS],
    ["gh", "secret", "list", "--json", "name"],
]
MISSING_KEY = (
    "shipmate-engine holds no SHIPMATE_APP_PRIVATE_KEY, so this run has no key to place "
    "there. Re-run with --key <path to the App's PEM private key>."
)


def test_a_run_without_key_reuses_the_placed_key(monkeypatch, tmp_path):
    """A second run needs no PEM: `shipmate-engine` already holds the key, and no REST path
    returns its value, so there is nothing to compare or rewrite.

    Mutation: make `--key` `required=True` again, so argparse refuses the run with exit 2.
    """
    fake, exit_ = run_main(
        monkeypatch,
        tmp_path,
        {**_CONFORMING_ENGINE, ENGINE_SECRETS: {"total_count": 1, "secrets": [{"name": KEY}]}},
        [],
        key=False,
    )
    assert exit_.code == 0
    assert [c for c in fake.calls if c[:3] == ["gh", "secret", "set"]] == []
    assert ("ok", f"shipmate-engine {KEY}", "") in onboard.REPORT


def test_a_run_without_key_refuses_before_its_first_write(monkeypatch, tmp_path):
    """No `--key` and no key on `shipmate-engine` (it does not exist, so its secret read
    answers 404) leaves nothing to place: the run refuses before it creates anything. The
    whole call list is compared, so any write ahead of the refusal reddens it.

    Mutation: move `_refuse_missing_key(ctx)` below `_reconcile_env(ctx, ENGINE_ENV,
    "apply")`, which creates `shipmate-engine` first.
    """
    fake, exc = run_main(monkeypatch, tmp_path, {}, [], key=False)
    assert str(exc) == MISSING_KEY
    assert fake.calls == READS_BEFORE_KEY_REFUSAL


def test_a_dry_run_without_key_refuses_too(monkeypatch, tmp_path):
    """A `would set` with no key to set is a false report. Mutation: return early from
    `_refuse_missing_key` under `_DRY`."""
    fake, exc = run_main(monkeypatch, tmp_path, {}, ["--dry-run"], key=False)
    assert str(exc) == MISSING_KEY
    assert fake.calls == READS_BEFORE_KEY_REFUSAL


def _would_create(prefix):
    """The subjects `--dry-run` reported it would create, of those starting with `prefix`."""
    return [s for verb, s, _ in onboard.REPORT if verb == "would create" and s.startswith(prefix)]


def _environment_puts(fake):
    return [c[4] for c in fake.calls if c[:4] == ["gh", "api", "-X", "PUT"]]


def _variable_sets(fake):
    return [c for c in fake.calls if c[:3] == ["gh", "variable", "set"]]


def test_the_table_decides_which_naming_is_created(monkeypatch, tmp_path):
    """The checkout's `.github/shipmate-config.yml` is the only source of the shared set: an
    entry holding `shared: true` gets the bare environment and no split pair, and no run
    writes a variable naming it.

    Mutations: `_resolve_shared` returning `set()` without reading the file reddens the
    shared case; writing a SHIPMATE_SHARED_ENVS variable from `_reconcile_variables`
    reddens its variable list.
    """
    write_table(tmp_path, "    shared: true\n")
    fake, _ = run_main(
        monkeypatch,
        tmp_path,
        {"repos/o/r/environments/dev-eu/deployment-branch-policies": ABSENT},
        [],
    )
    assert _environment_puts(fake) == [ENGINE_PATH, "repos/o/r/environments/dev-eu"]
    assert _variable_sets(fake) == [["gh", "variable", "set", "SHIPMATE_APP_ID", "--body", "1"]]


def test_without_a_table_every_environment_is_split(monkeypatch, tmp_path):
    """No file shares nothing. Mutation: `_resolve_shared` returning every derived
    environment when the file is absent."""
    fake, _ = run_main(monkeypatch, tmp_path, {}, [])
    assert _environment_puts(fake) == [
        ENGINE_PATH,
        "repos/o/r/environments/dev-eu-plan",
        "repos/o/r/environments/dev-eu-apply",
    ]


def test_an_invalid_table_refuses_before_any_write(monkeypatch, tmp_path):
    """`shared_envs` trusts the table's shape, and a `yes` reads as unshared, the split
    naming. The structural check refuses first, while no write has run.

    Mutation: drop the `validate_structure` call in `_resolve_shared`, which reconciles
    the split pair.
    """
    write_table(tmp_path, "    shared: yes\n")
    fake, exc = run_main(monkeypatch, tmp_path, {}, [])
    assert fake.calls == [["gh", "variable", "list", "--json", "name,value"]]
    assert str(exc) == ("::error::environments.dev-eu.shared must be true or false, got 'yes'.")


def write_referencing_table(root):
    """A `.github/shipmate-config.yml` whose shared `dev-eu` takes its region from DEV_EU_REGION."""
    (root / ".github").mkdir(exist_ok=True)
    (root / ".github" / "shipmate-config.yml").write_text(
        "layout: tf_vars\n\nenvironments:\n  dev-eu:\n    region: {vars: DEV_EU_REGION}\n"
        "    shared: true\n",
        encoding="utf-8",
        newline="\n",
    )


def org_region(value):
    return {ORG_VARS: [{"variables": [{"name": "DEV_EU_REGION", "value": value}]}]}


def test_a_repository_variable_resolves_a_reference(monkeypatch, tmp_path):
    """A reference-holding table resolves from the repository listing, and `shared` is read
    from the resolved table. Mutation: pass `variables=None` to `parse_table`, which reads
    the absent SHIPMATE_GITHUB_VARS and refuses."""
    monkeypatch.delenv("SHIPMATE_GITHUB_VARS", raising=False)
    monkeypatch.setattr(onboard, "_run", make_gh({ORG_VARS: NO_ORG_VARS}))
    write_referencing_table(tmp_path)
    _, shared = onboard._resolve_shared(tmp_path, "o/r", {"DEV_EU_REGION": "eu-west-1"})
    assert shared == {"dev-eu"}


def test_an_organization_variable_resolves_a_reference(monkeypatch, tmp_path):
    """Mutation: pass only the repository `variables` to `parse_table`, so the name reads
    as unset."""
    monkeypatch.delenv("SHIPMATE_GITHUB_VARS", raising=False)
    monkeypatch.setattr(onboard, "_run", make_gh(org_region("eu-west-1")))
    write_referencing_table(tmp_path)
    assert onboard._resolve_shared(tmp_path, "o/r", {})[1] == {"dev-eu"}


def test_the_repository_variable_wins_over_the_organization_one(monkeypatch, tmp_path):
    """GitHub gives a repository variable precedence over an organization variable of the
    same name. Mutation: swap the merge order, so the organization's empty value refuses."""
    monkeypatch.delenv("SHIPMATE_GITHUB_VARS", raising=False)
    monkeypatch.setattr(onboard, "_run", make_gh(org_region("")))
    write_referencing_table(tmp_path)
    _, shared = onboard._resolve_shared(tmp_path, "o/r", {"DEV_EU_REGION": "eu-west-1"})
    assert shared == {"dev-eu"}


def test_a_failed_organization_read_names_the_table_reference(monkeypatch, tmp_path):
    """The failed read names the table reference that caused it, so the operator knows why
    onboard read organization variables at all. Mutation: pass a different remedy from
    `_resolve_shared`."""
    monkeypatch.setattr(
        onboard, "_run", make_gh({ORG_VARS: SystemExit("gh: Forbidden (HTTP 403)")})
    )
    write_referencing_table(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        onboard._resolve_shared(tmp_path, "o/r", {"DEV_EU_REGION": "eu-west-1"})
    assert str(excinfo.value) == (
        "could not read the organization variables reaching o/r: gh: Forbidden (HTTP 403)\n"
        "A fine-grained token needs this repository's Variables read permission, and "
        "`--slurp` needs a recent `gh`. .github/shipmate-config.yml environments.dev-eu.region "
        "references GitHub variable DEV_EU_REGION, and onboard resolves a reference from "
        "these variables and the repository's."
    )


def test_resolve_shared_returns_the_table_it_validated(tmp_path):
    """`main` keeps the table for the checklist. Mutation: return `{}, set()` when there is
    no file, which reads as a table declaring nothing."""
    assert onboard._resolve_shared(tmp_path, "o/r", {}) == (None, set())
    write_table(tmp_path, "    shared: true\n")
    assert onboard._resolve_shared(tmp_path, "o/r", {}) == (
        {"layout": "tf_vars", "environments": {"dev-eu": {"region": "eu-west-1", "shared": True}}},
        {"dev-eu"},
    )


def test_a_table_without_a_reference_makes_no_api_call(monkeypatch, tmp_path):
    """Mutation: call `_reaching_variables` whether or not the file holds a reference."""

    def refuse(*args, **kwargs):
        raise AssertionError(f"unexpected call: {args}")

    monkeypatch.setattr(onboard, "_run", refuse)
    write_table(tmp_path, "    shared: true\n")
    assert onboard._resolve_shared(tmp_path, "o/r", {})[1] == {"dev-eu"}


def test_the_shared_flag_is_gone(monkeypatch, tmp_path, capsys):
    """The table replaced `--shared`; argparse must reject it as unknown rather than
    accept a value nothing reads. Mutation: restore the `--shared` argument."""
    fake, exc = run_main(monkeypatch, tmp_path, {}, ["--shared", "dev-eu"])
    assert exc.code == 2
    assert fake.calls == []
    assert "unrecognized arguments: --shared dev-eu" in capsys.readouterr().err


def test_an_organization_app_id_still_gets_a_repository_copy(monkeypatch, tmp_path):
    """An organization-level SHIPMATE_APP_ID equal to --app-id is not consulted: with no
    repository copy, the run writes one. The organization route is never read.

    Mutation: return early from `_reconcile_variables`, which writes no variable.
    """
    fake, exit_ = run_main(
        monkeypatch,
        tmp_path,
        {ORG_VARS: [{"variables": [{"name": "SHIPMATE_APP_ID", "value": "1"}]}]},
        [],
    )
    assert exit_.code == 0
    assert _variable_sets(fake) == [["gh", "variable", "set", "SHIPMATE_APP_ID", "--body", "1"]]
    assert [c for c in fake.calls if ORG_VARS in c] == []


def test_a_diverging_repository_app_id_refuses_before_any_write(monkeypatch, tmp_path):
    """A repository SHIPMATE_APP_ID differing from --app-id would pin the gate ruleset to an
    App the workflows do not mint from. The whole call list is compared, so any write ahead
    of the refusal reddens it.

    Mutation: delete the `_refuse_diverging_app_id(args.app_id, variables)` call in `main`.
    """
    fake, exc = run_main(
        monkeypatch, tmp_path, {VARIABLE_LIST: [{"name": "SHIPMATE_APP_ID", "value": "2"}]}, []
    )
    assert str(exc) == (
        "the SHIPMATE_APP_ID repository variable is 2 and --app-id is 1. The workflows mint "
        "their token from the variable, so a gate ruleset pinned to 1 could never be satisfied "
        "and the default branch would be blocked. Re-run with --app-id 2, or change the "
        "variable first."
    )
    assert fake.calls == [["gh", "variable", "list", "--json", "name,value"]]


def test_the_organization_read_is_paginated_and_slurped(monkeypatch):
    """`make_gh` keys a `gh api` read on the URL alone, so the fake hands back both pages
    whether or not the flags are passed: resolving a name off the second page proves the loop
    over pages, and only the argv assertion proves the request that produces them.

    Mutations, three: drop `--paginate`; drop `--slurp` -- both red the argv assertion and
    leave the two-page resolution green, which is why that assertion exists; and read
    `pages[0]` alone, which reds the second-page fixture.
    """
    two_pages = [
        {"variables": [{"name": "OTHER_VARIABLE", "value": "ops"}], "total_count": 1},
        {"variables": [{"name": "SHIPMATE_APP_ID", "value": "1"}], "total_count": 1},
    ]
    fake = make_gh({ORG_VARS: two_pages})
    monkeypatch.setattr(onboard, "_run", fake)
    assert onboard._reaching_variables("o/r", "") == {
        "OTHER_VARIABLE": "ops",
        "SHIPMATE_APP_ID": "1",
    }
    assert fake.calls == [ORG_VARS_READ]


def test_missing_gate_rule_creates_the_ruleset(monkeypatch):
    """A repository with no gate requirement gets exactly the gate rule, pinned to the
    App id, with `strict` on -- and nothing else, because a second ruleset carrying a
    `pull_request` rule would conflict with one the repository may already have.

    The POST body is compared whole against a hand-written dict.

    The workflow file is on the remote default branch here, so a pull request can
    satisfy the gate and the ruleset is created.

    Mutations: drop `strict_required_status_checks_policy` from the body; treat the
    remote workflow file as always absent.
    """
    fake = make_gh({RULES: []})
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_ruleset(ctx(app_id="4326562", shim_on_default=True))
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


def test_the_gate_ruleset_waits_for_the_workflow_file_on_the_default_branch(monkeypatch, tmp_path):
    """`pull_request_target` runs the default branch's workflow file, so until that file
    is merged no pull request can produce `shipmate / gate`, and a ruleset requiring it
    blocks the pull request that adds the file. The checkout already holds the file here,
    which is why the presence read must go to the remote default branch.

    Mutation: treat the remote workflow file as always present.
    """
    shim = tmp_path / ".github" / "workflows" / "shipmate.yml"
    shim.parent.mkdir(parents=True)
    shim.write_text(
        onboard._render(ENGINE, "a" * 40, "v0.26.0", "main"), encoding="utf-8", newline="\n"
    )
    fake, exit_ = run_main(monkeypatch, tmp_path, {}, [])
    assert exit_.code == 0
    assert [c for c in fake.calls if "repos/o/r/rulesets" in c] == []
    assert [r for r in onboard.REPORT if r[1] == "gate ruleset"] == [
        (
            "deferred",
            "gate ruleset",
            "`.github/workflows/shipmate.yml` is not on main yet, or this token cannot read "
            "it and GitHub answered 404. No pull request can produce `shipmate / gate` "
            "before the file is there. Merge the pull request that adds it, then run this "
            "again to create the ruleset.",
        )
    ]


def test_a_required_gate_without_the_workflow_file_is_reported(monkeypatch):
    """A gate already required -- by hand, at organization level, or by an earlier run --
    while the workflow file is not on the default branch blocks the pull request that adds
    the file, because nothing can produce the status for it.

    Mutation: drop the `chk is not None and not ctx["shim_on_default"]` arm, so a
    conforming gate reports `ok`.
    """
    fake = make_gh({RULES: _rules()})
    monkeypatch.setattr(onboard, "_run", fake)
    onboard._reconcile_ruleset(ctx())
    assert fake.calls == [["gh", "api", RULES]]
    assert onboard.REPORT == [
        (
            "differs",
            "gate ruleset",
            "`shipmate / gate` is already required, but `.github/workflows/shipmate.yml` is "
            "not on main yet, or this token cannot read it and GitHub answered 404. The pull "
            "request adding the file cannot get the gate: disable the gate rule until it "
            "merges, or merge it through a bypass actor.",
        )
    ]
    assert onboard._exit_code() == 2


def test_a_forbidden_workflow_file_read_stops_the_run_before_any_write(monkeypatch, tmp_path):
    """A token without Contents read answers the presence read 403, which is not absence.
    Read after the first write, that leaves environments and secrets written and the run
    dead; read in the facts phase, nothing is written.

    Mutation: move the `"shim_on_default"` read into `_reconcile_ruleset`.
    """
    fake, exit_ = run_main(
        monkeypatch,
        tmp_path,
        {REMOTE_SHIM: SystemExit("gh: Resource not accessible (HTTP 403)")},
        [],
    )
    assert str(exit_) == "gh: Resource not accessible (HTTP 403)"
    assert fake.calls == [
        ["gh", "variable", "list", "--json", "name,value"],
        ["gh", "api", REMOTE_SHIM],
    ]


def test_a_forbidden_engine_secret_read_stops_the_run_before_any_write(monkeypatch, tmp_path):
    """A 403 on the `shipmate-engine` secret list is not absence. Read as "no key placed",
    a run with `--key` would overwrite a working placement.

    Mutation: replace the `_gh_json_or_none` call in `_engine_secrets` with a broad
    `except SystemExit` returning no names.
    """
    fake, exit_ = run_main(
        monkeypatch,
        tmp_path,
        {ENGINE_SECRETS: SystemExit("gh: Forbidden (HTTP 403)")},
        [],
    )
    assert str(exit_) == "gh: Forbidden (HTTP 403)"
    assert fake.calls == [
        ["gh", "variable", "list", "--json", "name,value"],
        ["gh", "api", REMOTE_SHIM],
        ["gh", "api", ENGINE_SECRETS],
    ]


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
    onboard._reconcile_ruleset(ctx(app_id="4326562", shim_on_default=True))
    assert fake.calls == [["gh", "api", RULES]]
    assert onboard.REPORT == [
        (
            "differs",
            "gate ruleset",
            "`shipmate / gate` is required under integration_id 15368, not the shipmate "
            "App (4326562), so a status from another identity satisfies it. Change it by hand.",
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
    onboard._reconcile_ruleset(ctx(shim_on_default=True))
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
    onboard._reconcile_ruleset(ctx(shim_on_default=True))
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
    onboard._reconcile_ruleset(ctx(shim_on_default=True))
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
    onboard._reconcile_ruleset(ctx(shim_on_default=True))
    assert onboard.REPORT == [
        (
            "differs",
            "gate ruleset",
            "the rulesets POST was rejected (HTTP 422). Most likely a `shipmate-gate` "
            "ruleset already exists but requires nothing on this branch, because its "
            "enforcement is `evaluate` or `disabled` and the effective-rules read cannot "
            "see it. A 422 has other causes, so read `gh api repos/OWNER/REPO/rulesets` "
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
        onboard._reconcile_ruleset(ctx(shim_on_default=True))
    assert "HTTP 500" in str(e.value)


#: Owner-agnostic, like the docs guard's selector: the pages publish `<owner>/shipmate/...`
#: as well as this organization's own spelling.
_CALL_PATH = "/shipmate/.github/workflows/"

_EXPECTED_CALLEES = {
    "shipmate.yml": [
        "plan.yml",
        "comment-ops.yml",
        "deploy.yml",
        "apply.yml",
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
    workflow the file routes to, in document order -- five jobs, five pin sites, and a
    locator that found only the first would ship four unpinned calls.

    The expected callee list is hand-written here, never read out of the docs, and the
    whole mapping is compared with `==`.

    Two claims, two mutations, each proved separately:
    - edit the fence's top-level `name:` line -> the locator matches zero fences and refuses;
    - edit a `uses:` filename in the fence -> the callee list differs.
    """
    found = {"shipmate.yml": _callees(onboard._render(ENGINE, "c" * 40, "v9.9.9", "main"))}
    assert found == _EXPECTED_CALLEES


def test_the_published_drift_fence_calls_drift_yml_on_the_placeholder():
    """The consumer saves the `shipmate drift` fence by hand, so nothing renders or pins it,
    and only this test reads it. Exactly one fence carries the name (`_fence` refuses
    otherwise), and its `uses:` lines, compared whole against a hand-written list, are one
    call of engine `drift.yml` on the placeholder the re-pin in docs/releasing.md rewrites.

    Mutation: `@main` in place of `@<engine-sha>` in the fence.
    """
    page = (ENGINE / "docs" / "getting-started.md").read_text(encoding="utf-8")
    uses = [
        line.strip()
        for line in onboard._fence(page, "shipmate drift").splitlines()
        if line.strip().startswith("uses:")
    ]
    assert uses == [
        "uses: ship-iac/shipmate/.github/workflows/drift.yml@<engine-sha>  # see the latest release"
    ]


_EXPECTED_PINS = {"shipmate.yml": 5}


def test_every_shim_is_pinned_at_every_site():
    """One file, five pins, and a file shipped still carrying `@<engine-sha>` resolves to
    nothing.

    Nothing else can see a missed rewrite: `_callees` splits before the `@`, so a surviving
    placeholder reads as a callee like any other. Two live triggers make that silence
    expensive: `_DOC_PIN` requires the trailing `#` comment, so a docs edit dropping
    `# see the latest release` from one line stops that pin being rewritten; and it is
    anchored on `ship-iac`, so normalising an owner in the docs to `<owner>` would unpin all
    five. `_DOC_PIN` stays anchored deliberately -- the docs/releasing.md re-pin is anchored
    the same way and the two writers must agree -- and this vector is what makes either edit
    loud.

    Hand-written, never derived from the docs.

    Mutations: `_DOC_PIN.sub(..., count=1)`, which rewrites one pin of the five; and delete
    `  # see the latest release` from the `plan` job's `uses:` line in the docs, which
    leaves that one call on `@<engine-sha>`.
    """
    rendered = {"shipmate.yml": onboard._render(ENGINE, "c" * 40, "v9.9.9", "main")}
    assert {name: text.count(f"@{'c' * 40} # v9.9.9") for name, text in rendered.items()} == (
        _EXPECTED_PINS
    )
    assert [name for name, text in rendered.items() if "<engine-sha>" in text] == []


def _shim_ctx(tmp_path):
    return ctx(root=tmp_path, engine=ENGINE, sha="c" * 40, version="v9.9.9")


def _plan_shim(tmp_path):
    """(path to the consumer's shipmate.yml, the text this script would render for it)."""
    path = tmp_path / ".github" / "workflows" / "shipmate.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path, onboard._render(ENGINE, "c" * 40, "v9.9.9", "main")


def test_the_rendered_push_trigger_names_the_default_branch():
    """The fence says `branches: [main]`, and on a repository whose default branch is
    anything else `deploy` never fires. The whole text is compared, so the substitution
    can touch nothing but that one line.

    Mutation: drop the substitution from `_render`.
    """
    main = onboard._render(ENGINE, "c" * 40, "v9.9.9", "main")
    assert onboard._render(ENGINE, "c" * 40, "v9.9.9", "develop") == main.replace(
        "    branches: [main]\n", "    branches: [develop]\n"
    )


def test_a_file_rendered_for_the_default_branch_reports_ok(tmp_path):
    """A re-run on a `develop` repository reads back the file the first run wrote for it.
    Rendered for `main` instead, it reports `differs` there on every run.

    Mutation: drop the substitution from `_render`.
    """
    path, main = _plan_shim(tmp_path)
    path.write_text(
        main.replace("    branches: [main]\n", "    branches: [develop]\n"),
        encoding="utf-8",
        newline="\n",
    )
    onboard._reconcile_shim({**_shim_ctx(tmp_path), "default_branch": "develop"})
    assert onboard.REPORT == [("ok", "shipmate.yml", "")]


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
    the docs/releasing.md re-pin's job. Naming that remedy is the whole point of the verb,
    and it is not drift, so it must not set exit 2 and fail the operator's run.

    Mutation: make `_depin` leave the trailing comment in place, so the version comment alone
    reads as `differs`.
    """
    path, _text = _plan_shim(tmp_path)
    older = onboard._render(ENGINE, "d" * 40, "v9.9.8", "main")
    path.write_text(older, encoding="utf-8", newline="\n")
    onboard._reconcile_shim(_shim_ctx(tmp_path))
    assert onboard.REPORT == [
        (
            "pin-only",
            "shipmate.yml",
            "move every engine ref to this engine's SHA in one commit (docs/releasing.md)",
        )
    ]
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
    copy of it -- the docs, the docs/releasing.md re-pin, the other consumers -- is LF.

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


def test_a_file_still_carrying_the_docs_placeholder_is_not_reported_pin_only(tmp_path):
    """A consumer who pasted the published fence by hand holds `@<engine-sha>`, which is not a
    pin the docs/releasing.md re-pin can move: its `sed` requires 40 hex, so it would rewrite
    nothing. Naming a remedy that cannot work is worse than naming none, so
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


#: Hand-written, not captured from the implementation: a constant pasted from the output
#: passes whatever the output says. A fresh public repository under `--dry-run`: no table,
#: no secret, every environment absent, no rule, no `CODEOWNERS`, no workflow file on the
#: default branch, so the gate ruleset is deferred.
FRESH_CHECKLIST = """
Still yours, each item marked from what this run read:

todo          SHIPMATE_PLAN_PASSPHRASE repository secret (optional)
    gh secret set SHIPMATE_PLAN_PASSPHRASE
    An organization secret of that name is not visible to this read.

todo          `.github/shipmate-config.yml`
    Add one: copy the example in docs/getting-started.md §Environments for this tier.

cannot check  o/r in the App installation's repository selection
    Reading `repos/o/r/installation` needs an App JWT, which this run
    does not hold. Check it, or add the repository, at
    https://github.com/organizations/o/settings/installations
    under the App's `Configure` button, in `Repository access`.
    The add-repository endpoint accepts PAT-classic tokens only, so it stays a UI step.

todo          approving review before apply
    Required reviewers and `Prevent self-review` on dev-eu-apply
    (docs/getting-started.md §Environment setup).

todo          CODEOWNERS entry covering /.github/workflows/
    Add one in `.github/CODEOWNERS`.

todo          Provider lock files
    1 of 1 stack(s) have no git-tracked `.terraform.lock.hcl`,
    so the provider cache serves none of them. Remove any `.gitignore` entry for the
    file first, since git refuses to add an ignored path:
      stacks/app
    In each, run `tofu init -backend=false`, then
    `tofu providers lock -platform=linux_amd64`, and commit the file.

ok            unmanaged stacks
ok            stack tags
todo          drift sweeps
    Add one workflow file per drift sweep from the `shipmate drift` fence in
    docs/getting-started.md, under any name in `.github/workflows/`, with the same
    pin as `shipmate.yml`. Without one, no stack is checked for drift (docs/drift.md).

todo          adoption pull request
    Re-run without --dry-run, then commit the workflow file and the table together,
    in a pull request that changes no stack. The table is read from the default
    branch, so the first plan needs it merged.

todo          gate ruleset
    Merge the adoption pull request: no ruleset requires `shipmate / gate` yet, because
    the workflows that produce it are not on the default branch (CONTRACT.md
    §Post-plan topology). Then run this script again to create the gate ruleset.
"""

#: Hand-written: a configured public repository with the `repo-example-folders` drift file.
#: Every item this run can read is `ok`; the App installation and CODEOWNERS items stay
#: `cannot check`.
CONFIGURED_CHECKLIST = """
Still yours, each item marked from what this run read:

ok            SHIPMATE_PLAN_PASSPHRASE repository secret (optional)
ok            `.github/shipmate-config.yml`
cannot check  o/r in the App installation's repository selection
    Reading `repos/o/r/installation` needs an App JWT, which this run
    does not hold. Check it, or add the repository, at
    https://github.com/organizations/o/settings/installations
    under the App's `Configure` button, in `Repository access`.
    The add-repository endpoint accepts PAT-classic tokens only, so it stays a UI step.

ok            approving review before apply
cannot check  CODEOWNERS entry covering /.github/workflows/
    `.github/CODEOWNERS` exists. Which paths it covers is GitHub's matching, not this run's.

ok            Provider lock files
ok            unmanaged stacks
ok            stack tags
ok            drift sweeps
    Read drift.yml in this working tree; a drift run reads the default branch's copy.

ok            adoption pull request
"""

REVIEWERS_RULE = {
    "type": "required_reviewers",
    "prevent_self_review": True,
    "reviewers": [{"type": "Team", "reviewer": {"slug": "ops"}}],
}
REVIEWED_APPLY = {"deployment_branch_policy": CUSTOM_POLICY, "protection_rules": [REVIEWERS_RULE]}
MAIN_POLICY = {"total_count": 1, "branch_policies": [{"name": "main"}]}


def checklist_of(out):
    """The checklist part of `main`'s stdout: everything from its heading on."""
    return out[out.index("\nStill yours") :]


def checklist_items(out):
    """{item: (verdict, detail lines)} parsed from `_checklist`'s output."""
    items = {}
    for line in out.splitlines()[3:]:
        if line and not line.startswith(" "):
            current = line[14:]
            items[current] = (line[:12].rstrip(), [])
        elif line:
            items[current][1].append(line[4:])
    return items


def test_the_checklist_of_a_fresh_repository_in_a_dry_run(monkeypatch, tmp_path, capsys):
    """Every item a fresh repository still needs is `todo`, and the App installation, which
    this run cannot read, is `cannot check`. The block is compared whole: a membership check
    passes a block that silently lost one item, a command, or its verdict.

    The environments are all absent, so `apply_envs` holds `None` for each: a dry run must
    not read that as reviewed.

    The checkout holds no `.github/workflows/`, so the drift sweeps item asks for a file.

    Mutations: delete the passphrase item from `_checklist`; delete `_drift_sweeps_item`
    from it.
    """
    _fake, exit_ = run_main(monkeypatch, tmp_path, {}, ["--dry-run"])
    assert exit_.code == 0
    assert checklist_of(capsys.readouterr().out) == FRESH_CHECKLIST


def test_the_checklist_of_a_configured_public_repository(monkeypatch, tmp_path, capsys):
    """Driven through `main`, so the reviewer verdict rests on the `dev-eu-apply` read that
    `_reconcile_env` recorded, not on a hand-filled `apply_envs`.

    Mutations: drop the `ctx["apply_envs"][name] = env` record from `_reconcile_env`, so the
    reviewer item turns `todo`; delete `_drift_sweeps_item` from `_checklist`.
    """
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "drift.yml").write_bytes(FOLDERS_DRIFT)
    (tmp_path / ".github" / "CODEOWNERS").write_text("* @o/ops\n", encoding="utf-8")
    (tmp_path / ".github" / "shipmate-config.yml").write_text(
        "layout: tf_vars\n\nenvironments:\n  dev-eu:\n    region: eu-west-1\n",
        encoding="utf-8",
        newline="\n",
    )
    routes = {
        ENGINE_PATH: {"deployment_branch_policy": CUSTOM_POLICY},
        ENGINE_POLICIES: MAIN_POLICY,
        ENGINE_SECRETS: {"secrets": [{"name": KEY}]},
        REPO_KEY_LIST: [{"name": "SHIPMATE_PLAN_PASSPHRASE"}],
        "repos/o/r/environments/dev-eu-plan": {"deployment_branch_policy": None},
        "repos/o/r/environments/dev-eu-apply": REVIEWED_APPLY,
        "repos/o/r/environments/dev-eu-apply/deployment-branch-policies": MAIN_POLICY,
        VARIABLE_LIST: [{"name": "SHIPMATE_APP_ID", "value": "1"}],
        REMOTE_SHIM: {"name": "shipmate.yml"},
        "git ls-files -z -- :(top)*.terraform.lock.hcl": "stacks/app/.terraform.lock.hcl\0",
    }
    _fake, exit_ = run_main(monkeypatch, tmp_path, routes, [])
    assert exit_.code == 0
    assert checklist_of(capsys.readouterr().out) == CONFIGURED_CHECKLIST


def test_todo_items_leave_the_exit_code_at_0(monkeypatch, tmp_path, capsys):
    """Exit 2 means a `differs` line and nothing else: every checklist item is the
    consumer's to do, so a wrapper branching on 2 would fail every first run.

    Mutation, proven: record each checklist verdict in `REPORT` and count `todo` in
    `_exit_code`. Counting `todo` alone cannot redden: the checklist writes nothing to
    `REPORT`, which is what keeps it out of the exit code.
    """
    _fake, exit_ = run_main(monkeypatch, tmp_path, {}, ["--dry-run"])
    assert "\ntodo  " in capsys.readouterr().out
    assert [v for v, _s, _d in onboard.REPORT if v == "differs"] == []
    assert exit_.code == 0


def test_the_passphrase_is_read_from_the_repository_secrets():
    """Mutation: read `engine_secrets` for it."""
    item = "SHIPMATE_PLAN_PASSPHRASE repository secret (optional)"
    placed = ctx(repo_secrets={"SHIPMATE_PLAN_PASSPHRASE"})
    assert onboard._passphrase_item(placed) == ("ok", item, [])
    assert onboard._passphrase_item(ctx()) == (
        "todo",
        item,
        [
            "gh secret set SHIPMATE_PLAN_PASSPHRASE",
            "An organization secret of that name is not visible to this read.",
        ],
    )


def test_a_table_failing_tf_vars_coverage_is_todo_naming_the_refusal():
    """The checkout's table already passed `validate_structure` in `_resolve_shared`; only
    `validate` sees an environment with no region, which every `tf_vars` cell refuses.

    Mutation: call `ec.validate_structure` instead of `ec.validate`.
    """
    table = ec.parse_table("layout: tf_vars\n\nenvironments:\n  dev-eu:\n    shared: false\n")
    assert onboard._table_item(ctx(table=table)) == (
        "todo",
        "`.github/shipmate-config.yml`",
        [
            "layout: tf_vars derives TF_VAR_env and TF_VAR_region from the environment "
            "table, and dev-eu has an entry with no region."
        ],
    )


def test_a_private_repository_with_an_approving_review_rule_is_ok():
    """A private repository below Enterprise cannot carry environment reviewers, so a
    `pull_request` rule at one approval is its apply gate.

    Mutation: compare `review_count > 1`.
    """
    item = "approving review before apply"
    ruled = ctx(is_private=True, review_count=1)
    assert onboard._review_item(ruled, ["dev-eu-apply"]) == ("ok", item, [])
    assert onboard._review_item(ctx(is_private=True), ["dev-eu-apply"]) == (
        "todo",
        item,
        [
            "Required reviewers and `Prevent self-review` on dev-eu-apply",
            "(docs/getting-started.md §Environment setup). On a private repository below",
            "Enterprise, GitHub refuses required reviewers, so the apply gate is then an",
            "approving-review `pull_request` rule on the default branch, which this script",
            "does not create (docs/branch-protection.md §Reproducible ruleset).",
        ],
    )


def test_an_environment_a_dry_run_would_create_is_not_reviewed():
    """Mutation: read a `None` environment as reviewed."""
    c = ctx(apply_envs={"dev-eu-apply": None})
    assert onboard._review_item(c, ["dev-eu-apply"])[0] == "todo"


@pytest.mark.parametrize(
    "rule",
    [
        {**REVIEWERS_RULE, "prevent_self_review": False},
        {**REVIEWERS_RULE, "reviewers": []},
    ],
    ids=["self-review-allowed", "no-reviewer"],
)
def test_a_reviewer_rule_missing_a_condition_is_todo(rule):
    """Mutations, each proven: drop the `prevent_self_review` condition from `_reviewed`;
    drop the `reviewers` condition."""
    c = ctx(apply_envs={"dev-eu-apply": {"protection_rules": [rule]}})
    assert onboard._review_item(c, ["dev-eu-apply"])[0] == "todo"
    reviewed = ctx(apply_envs={"dev-eu-apply": {"protection_rules": [REVIEWERS_RULE]}})
    assert onboard._review_item(reviewed, ["dev-eu-apply"])[0] == "ok"


def test_the_reviewer_verdict_ignores_the_engine_environment():
    """`shipmate-engine` is recorded in `apply_envs` too, but no apply cell binds it.

    Mutation: judge over every name in `apply_envs` instead of `names`.
    """
    c = ctx(apply_envs={"shipmate-engine": {}, "dev-eu-apply": REVIEWED_APPLY})
    assert onboard._review_item(c, ["dev-eu-apply"])[0] == "ok"


def test_the_review_count_reads_past_the_gate_rule(monkeypatch):
    """`_gate_entry` returns at the gate's own `required_status_checks` rule, so a count
    taken in that loop never sees a `pull_request` rule listed after it.

    Mutation: take the count inside the loop, before its `return`.
    """
    rules = [
        {
            "type": "required_status_checks",
            "parameters": {"required_status_checks": [{"context": onboard.GATE}]},
        },
        {"type": "pull_request", "parameters": {"required_approving_review_count": 1}},
    ]
    monkeypatch.setattr(onboard, "_run", make_gh({RULES: rules}))
    assert onboard._gate_entry(ctx()) == (
        {"context": onboard.GATE},
        {"required_status_checks": [{"context": onboard.GATE}]},
        1,
    )


def test_the_ruleset_reconciler_stores_the_review_count(monkeypatch):
    """`_checklist` judges the reviewer item on `ctx["review_count"]`, which only
    `_reconcile_ruleset` sets.

    Mutation: unpack the count into `_` in `_reconcile_ruleset`.
    """
    rules = [{"type": "pull_request", "parameters": {"required_approving_review_count": 2}}]
    monkeypatch.setattr(onboard, "_run", make_gh({RULES: rules}))
    c = ctx()
    onboard._reconcile_ruleset(c)
    assert c["review_count"] == 2


def test_a_shared_only_repository_is_judged_on_the_review_rule_alone(capsys, tmp_path):
    """A shared env is bound by plan cells and every drift sweep covering it too, so a required
    reviewer on it stalls them rather than gating an apply: no environment is asked for
    reviewers, and the `pull_request` rule is the only apply gate left to judge.

    Mutations, each proven: select the reviewer names on `role == "apply"` alone, which a
    bare shared environment satisfies; drop the `if gated` arm, omitting the item; compare
    `review_count > 1` in `_review_item`.
    """
    item = "approving review before apply"
    onboard._checklist(ctx(root=tmp_path, shared={"dev-eu"}))
    assert checklist_items(capsys.readouterr().out)[item] == (
        "todo",
        [
            "Shared, so no `<env>-apply` carries required reviewers: dev-eu.",
            "Their apply gate is an approving-review `pull_request` rule on the default",
            "branch, which this script does not create (docs/branch-protection.md",
            "§Reproducible ruleset).",
        ],
    )
    onboard._checklist(ctx(root=tmp_path, shared={"dev-eu"}, review_count=1))
    assert checklist_items(capsys.readouterr().out)[item] == (
        "ok",
        ["`required_approving_review_count` is 1 on the default branch's `pull_request` rule."],
    )


def test_a_shared_environment_beside_a_reviewed_one_needs_the_review_rule(capsys, tmp_path):
    """A shared env forfeits the environment reviewer gate, so a reviewed `prod-apply`
    beside it does not gate its applies: only the `pull_request` rule does.

    Mutation: drop `and not shared` from `_review_item`'s second `ok`.
    """
    item = "approving review before apply"
    mixed = {
        "root": tmp_path,
        "envs": ["dev-eu", "prod"],
        "shared": {"dev-eu"},
        "apply_envs": {"prod-apply": REVIEWED_APPLY},
    }
    onboard._checklist(ctx(**mixed))
    assert checklist_items(capsys.readouterr().out)[item] == (
        "todo",
        [
            "Shared, so no `<env>-apply` carries required reviewers: dev-eu.",
            "Their apply gate is an approving-review `pull_request` rule on the default",
            "branch, which this script does not create (docs/branch-protection.md",
            "§Reproducible ruleset).",
        ],
    )
    onboard._checklist(ctx(**mixed, review_count=1))
    assert checklist_items(capsys.readouterr().out)[item] == (
        "ok",
        ["`required_approving_review_count` is 1 on the default branch's `pull_request` rule."],
    )


def test_an_ungated_environment_is_not_asked_for_reviewers(capsys, tmp_path):
    """`gated: false` exempts an environment from the review requirement, so asking for
    reviewers on it contradicts the table.

    Mutation: drop the `env not in ungated` filter from `_checklist`.
    """
    onboard._checklist(
        ctx(
            root=tmp_path,
            envs=["dev-eu", "dev-us"],
            table={"environments": {"dev-us": {"gated": False}}},
            apply_envs={"dev-eu-apply": REVIEWED_APPLY, "dev-us-apply": {}},
        )
    )
    items = checklist_items(capsys.readouterr().out)
    assert items["approving review before apply"] == ("ok", [])


def test_the_checklist_skips_an_environment_the_reconciler_left_alone(capsys, tmp_path):
    """`_reconcile_envs` touches neither half of an environment holding both a bare
    `<env>` and an `<env>-apply`, because which the engine binds is undecided. Naming its
    `<env>-apply` in the reviewer item points at one the run refused to reconcile.

    Mutation: drop the `env not in ctx["unresolved"]` filter from `_checklist`.
    """
    onboard._checklist(ctx(root=tmp_path, envs=["dev-eu", "dev-us"], unresolved={"dev-us"}))
    items = checklist_items(capsys.readouterr().out)
    assert items["approving review before apply"] == (
        "todo",
        [
            "Required reviewers and `Prevent self-review` on dev-eu-apply",
            "(docs/getting-started.md §Environment setup).",
        ],
    )


def test_codeowners_outside_github_is_found_but_not_matched(tmp_path):
    """GitHub reads `CODEOWNERS` from `.github/`, the root or `docs/`; which paths an entry
    covers is not reimplemented here.

    Mutation: drop `docs/CODEOWNERS` from `_CODEOWNERS_PATHS`.
    """
    item = "CODEOWNERS entry covering /.github/workflows/"
    assert onboard._codeowners_item(ctx(root=tmp_path)) == (
        "todo",
        item,
        ["Add one in `.github/CODEOWNERS`."],
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "CODEOWNERS").write_text("* @o/ops\n", encoding="utf-8")
    assert onboard._codeowners_item(ctx(root=tmp_path)) == (
        "cannot check",
        item,
        ["`docs/CODEOWNERS` exists. Which paths it covers is GitHub's matching, not this run's."],
    )


LOCK_ITEM = "Provider lock files"


def lock_run(tracked):
    """A fake `_run` answering `git ls-files -z -- ':(top)*.terraform.lock.hcl'` as git does:
    every path in `tracked`, relative to the working directory, each NUL-terminated."""

    def _run(args, secrets=(), stdin=None):
        assert args == ["git", "ls-files", "-z", "--", ":(top)*.terraform.lock.hcl"], args
        return "".join(f"{path}\0" for path in sorted(tracked))

    return _run


def test_the_lock_item_is_ok_when_every_stack_tracks_its_lock(monkeypatch, tmp_path):
    """No lock is on disk: a tracked lock absent from the working tree gets no content verdict.

    Mutation: split the `git ls-files -z` output on a newline, which matches no path. Or
    flag a tracked lock that is not on disk as cache-written.
    """
    monkeypatch.chdir(tmp_path)
    stacks = ["a", "b", "c"]
    tracked = {f"{s}/.terraform.lock.hcl" for s in stacks}
    monkeypatch.setattr(onboard, "_run", lock_run(tracked))
    assert onboard._lock_files_item(ctx(root=tmp_path, stacks=stacks)) == ("ok", LOCK_ITEM, [])


def test_the_lock_item_names_each_stack_without_a_tracked_lock(monkeypatch, tmp_path):
    """Mutation: replace the tracked-file check with `is_file()` on the lock path, which
    reads `c`'s lock on disk as committed although git does not track it. Or drop the item's
    `sorted`, which names `c` before `a`.
    """
    monkeypatch.chdir(tmp_path)
    for s in ("a", "b", "c"):
        (tmp_path / s).mkdir()
        (tmp_path / s / ".terraform.lock.hcl").write_text("", encoding="utf-8")
    monkeypatch.setattr(onboard, "_run", lock_run({"b/.terraform.lock.hcl"}))
    assert onboard._lock_files_item(ctx(root=tmp_path, stacks=["c", "b", "a"])) == (
        "todo",
        LOCK_ITEM,
        [
            "2 of 3 stack(s) have no git-tracked `.terraform.lock.hcl`,",
            "so the provider cache serves none of them. Remove any `.gitignore` entry for the",
            "file first, since git refuses to add an ignored path:",
            "  a",
            "  c",
            "In each, run `tofu init -backend=false`, then",
            "`tofu providers lock -platform=linux_amd64`, and commit the file.",
        ],
    )


def test_the_lock_item_names_ten_stacks_and_counts_the_rest(monkeypatch, tmp_path):
    """Mutation: drop the ten-path cap, which names all twelve."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(onboard, "_run", lock_run(set()))
    stacks = [f"s{i:02}" for i in range(12)]
    assert onboard._lock_files_item(ctx(root=tmp_path, stacks=stacks)) == (
        "todo",
        LOCK_ITEM,
        [
            "12 of 12 stack(s) have no git-tracked `.terraform.lock.hcl`,",
            "so the provider cache serves none of them. Remove any `.gitignore` entry for the",
            "file first, since git refuses to add an ignored path:",
            "  s00",
            "  s01",
            "  s02",
            "  s03",
            "  s04",
            "  s05",
            "  s06",
            "  s07",
            "  s08",
            "  s09",
            "  and 2 more",
            "In each, run `tofu init -backend=false`, then",
            "`tofu providers lock -platform=linux_amd64`, and commit the file.",
        ],
    )


def test_the_lock_item_resolves_stacks_against_the_working_directory(monkeypatch, tmp_path):
    """`terramate list` and `git ls-files` both print paths relative to the directory they
    ran in, so a stack above it matches its lock as `../infra` and is named as printed.

    Mutation: resolve each stack to a root-relative path with `os.path.relpath(cwd / s,
    root)`, which reads `infra`'s tracked lock as missing and names `stacks/app`.
    """
    (tmp_path / "stacks").mkdir()
    monkeypatch.chdir(tmp_path / "stacks")
    monkeypatch.setattr(onboard, "_run", lock_run({"../infra/.terraform.lock.hcl"}))
    assert onboard._lock_files_item(ctx(root=tmp_path, stacks=["app", "../infra"])) == (
        "todo",
        LOCK_ITEM,
        [
            "1 of 2 stack(s) have no git-tracked `.terraform.lock.hcl`,",
            "so the provider cache serves none of them. Remove any `.gitignore` entry for the",
            "file first, since git refuses to add an ignored path:",
            "  app",
            "In each, run `tofu init -backend=false`, then",
            "`tofu providers lock -platform=linux_amd64`, and commit the file.",
        ],
    )


#: Provider blocks shaped as OpenTofu 1.12 writes them: a registry read records both hash
#: kinds, a `tofu init` through `plugin_cache_may_break_dependency_lock_file` one `h1:`.
REGISTRY_NULL = """provider "registry.opentofu.org/hashicorp/null" {
  version = "3.2.4"
  hashes = [
    "h1:AAAA=",
    "h1:BBBB=",
    "zh:1111",
    "zh:2222",
  ]
}
"""
REGISTRY_RANDOM = """provider "registry.opentofu.org/hashicorp/random" {
  version = "3.7.2"
  hashes = [
    "h1:CCCC=",
    "zh:3333",
  ]
}
"""
CACHE_RANDOM = """provider "registry.opentofu.org/hashicorp/random" {
  version = "3.7.2"
  hashes = [
    "h1:CCCC=",
  ]
}
"""
CACHE_WRITTEN_LINES = [
    "1 of 2 stack(s) track a `.terraform.lock.hcl` without the",
    "registry's `zh:` hashes, as a `tofu init` through a plugin cache writes it, so",
    "the runner's provider cache cannot use it:",
    "  b",
    "In each, run `tofu init -backend=false`, then",
    "`tofu providers lock -platform=linux_amd64`, and commit the file.",
]


def write_locks(root, locks):
    for stack, text in locks.items():
        (root / stack).mkdir()
        (root / stack / ".terraform.lock.hcl").write_text(text, encoding="utf-8")
    return lock_run({f"{s}/.terraform.lock.hcl" for s in locks})


def test_a_lock_with_only_h1_hashes_is_flagged(monkeypatch, tmp_path):
    """Mutation: accept a block carrying any `"h1:` entry, which reads `b` as `ok`."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        onboard, "_run", write_locks(tmp_path, {"a": REGISTRY_NULL, "b": CACHE_RANDOM})
    )
    assert onboard._lock_files_item(ctx(root=tmp_path, stacks=["a", "b"])) == (
        "todo",
        LOCK_ITEM,
        CACHE_WRITTEN_LINES,
    )


def test_one_cache_written_provider_block_flags_a_mixed_lock(monkeypatch, tmp_path):
    """One cache miss gives a lock whose other blocks came from the registry.

    Mutation: look for `"zh:` in the whole file instead of per `provider` block, which
    reads `b` as `ok`.
    """
    monkeypatch.chdir(tmp_path)
    locks = {"a": REGISTRY_NULL + "\n" + REGISTRY_RANDOM, "b": REGISTRY_NULL + "\n" + CACHE_RANDOM}
    monkeypatch.setattr(onboard, "_run", write_locks(tmp_path, locks))
    assert onboard._lock_files_item(ctx(root=tmp_path, stacks=["a", "b"])) == (
        "todo",
        LOCK_ITEM,
        CACHE_WRITTEN_LINES,
    )


def test_a_registry_written_lock_is_ok(monkeypatch, tmp_path):
    """Mutation: flag a block that carries `"zh:` instead of one that lacks it, which
    flags `a`."""
    monkeypatch.chdir(tmp_path)
    locks = {"a": REGISTRY_NULL + "\n" + REGISTRY_RANDOM}
    monkeypatch.setattr(onboard, "_run", write_locks(tmp_path, locks))
    assert onboard._lock_files_item(ctx(root=tmp_path, stacks=["a"])) == ("ok", LOCK_ITEM, [])


def test_a_missing_lock_and_a_cache_written_one_get_one_sentence_each(monkeypatch, tmp_path):
    """Each sentence names only its own stacks; one remedy serves both.

    Mutation: list the cache-written stacks under the missing-lock sentence, which names
    `b` there and drops the second sentence.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(onboard, "_run", write_locks(tmp_path, {"b": CACHE_RANDOM}))
    assert onboard._lock_files_item(ctx(root=tmp_path, stacks=["c", "b", "a"])) == (
        "todo",
        LOCK_ITEM,
        [
            "2 of 3 stack(s) have no git-tracked `.terraform.lock.hcl`,",
            "so the provider cache serves none of them. Remove any `.gitignore` entry for the",
            "file first, since git refuses to add an ignored path:",
            "  a",
            "  c",
            "1 of 3 stack(s) track a `.terraform.lock.hcl` without the",
            "registry's `zh:` hashes, as a `tofu init` through a plugin cache writes it, so",
            "the runner's provider cache cannot use it:",
            "  b",
            "In each, run `tofu init -backend=false`, then",
            "`tofu providers lock -platform=linux_amd64`, and commit the file.",
        ],
    )


def test_a_stack_in_two_environments_is_counted_once(monkeypatch, tmp_path, capsys):
    """The item names each managed stack once, whatever the number of environments tagging it.

    Mutation: build `ctx["stacks"]` from the `stacks_by_env` values without deduplicating,
    which lists `a` twice.
    """
    membership = (
        {"dev-eu": ["a", "b"], "dev-us": ["a"]},
        {"a": ["env/dev-eu", "env/dev-us"], "b": ["env/dev-eu"]},
    )
    dev_us = {
        "repos/o/r/environments/dev-us": ABSENT,
        "repos/o/r/environments/dev-us-plan": ABSENT,
        "repos/o/r/environments/dev-us-apply": ABSENT,
        "repos/o/r/environments/dev-us-apply/deployment-branch-policies": ABSENT,
    }
    run_main(monkeypatch, tmp_path, dev_us, ["--dry-run"], membership=membership)
    items = checklist_items(checklist_of(capsys.readouterr().out))
    assert items[LOCK_ITEM] == (
        "todo",
        [
            "2 of 2 stack(s) have no git-tracked `.terraform.lock.hcl`,",
            "so the provider cache serves none of them. Remove any `.gitignore` entry for the",
            "file first, since git refuses to add an ignored path:",
            "  a",
            "  b",
            "In each, run `tofu init -backend=false`, then",
            "`tofu providers lock -platform=linux_amd64`, and commit the file.",
        ],
    )


def test_the_lock_item_names_managed_stacks_only(monkeypatch, tmp_path, capsys):
    """An untagged stack is never planned, so its lock file is not this run's to ask for.

    Mutation: build `ctx["stacks"]` from `tags_by_stack`, which names `b` too.
    """
    membership = ({"dev-eu": ["a"]}, {"a": ["env/dev-eu"], "b": []})
    run_main(monkeypatch, tmp_path, {}, ["--dry-run"], membership=membership)
    items = checklist_items(checklist_of(capsys.readouterr().out))
    assert items[LOCK_ITEM] == (
        "todo",
        [
            "1 of 1 stack(s) have no git-tracked `.terraform.lock.hcl`,",
            "so the provider cache serves none of them. Remove any `.gitignore` entry for the",
            "file first, since git refuses to add an ignored path:",
            "  a",
            "In each, run `tofu init -backend=false`, then",
            "`tofu providers lock -platform=linux_amd64`, and commit the file.",
        ],
    )


def test_an_untagged_stack_is_adopted_through_the_real_membership(monkeypatch, tmp_path, capsys):
    """`onboard` scans the whole tree through build-matrix's own `env_membership`: an
    untagged stack beside a tagged one is named in the notice, and the run goes on.

    Mutation: restore the refusal of an untagged stack in build-matrix's `env_membership`.
    """
    monkeypatch.setattr(onboard.bm, "_list_stacks", lambda all_stacks, base: ["a", "b"])
    monkeypatch.setattr(onboard.bm, "_tags", lambda s: {"a": ["env/dev-eu"], "b": []}[s])
    _, exc = run_main(monkeypatch, tmp_path, {}, ["--dry-run"], membership=None)
    assert exc.code == 0
    assert (
        "::notice::1 stack(s) carry no env/* tag and are not managed by shipmate: b"
        in capsys.readouterr().out.splitlines()
    )


PRDO_ROUTES = {
    "repos/o/r/environments/prdo": ABSENT,
    "repos/o/r/environments/prdo-plan": ABSENT,
    "repos/o/r/environments/prdo-apply": ABSENT,
    "repos/o/r/environments/prdo-apply/deployment-branch-policies": ABSENT,
}


def test_a_table_entry_no_stack_tags_is_named_on_the_table_item(monkeypatch, tmp_path, capsys):
    """The union provisions a mistyped entry too, so the table item names it. env-config's
    unused-entry warning stays silent: it says to remove an entry the docs ask the consumer to
    declare before tagging.

    Mutation: drop the table-only detail line -- the item has no details.
    Mutation: always render the several-name wording -- the one-name lines differ.
    Mutation: pass `tagged` to `_table_item`'s `ec.validate` again -- the warning prints.
    """
    write_table(tmp_path, "  prdo:\n    region: eu-west-1\n")
    run_main(monkeypatch, tmp_path, PRDO_ROUTES, ["--dry-run"])
    out = capsys.readouterr().out
    assert checklist_items(checklist_of(out))["`.github/shipmate-config.yml`"] == (
        "ok",
        [
            "Provisioned for prdo, which no stack tags yet: its first tagging pull request",
            "deploys under that environment's protection. If the name is a typo, fix the",
            "entry.",
        ],
    )
    assert not [ln for ln in out.splitlines() if ln.startswith("::warning::")]


def _table_only_detail(*names):
    """`_table_item`'s detail for a tagged `dev-eu` plus table-only `names`."""
    entries = "".join(f"  {n}:\n    region: eu-west-1\n" for n in ("dev-eu", *names))
    table = ec.parse_table(f"layout: tf_vars\nenvironments:\n{entries}")
    verdict, _, details = onboard._table_item(ctx(table=table, envs=sorted(["dev-eu", *names])))
    assert verdict == "ok"
    return details


@pytest.mark.parametrize(
    ("names", "sentence"),
    [
        (
            ("production-eu-central",),
            "Provisioned for production-eu-central, which no stack tags yet: its first tagging "
            "pull request deploys under that environment's protection. If the name is a typo, "
            "fix the entry.",
        ),
        (
            ("prdo", "production-eu-central", "qa"),
            "Provisioned for prdo, production-eu-central, qa, which no stack tags yet: the first "
            "pull request tagging a stack into each deploys under that environment's "
            "protection. If a name is a typo, fix the entry.",
        ),
    ],
    ids=["one", "several"],
)
def test_the_table_only_detail_wraps_the_whole_sentence_at_80(names, sentence):
    """Mutation: always render the one-name wording -- `several` reddens. Mutation: always the
    several-name wording -- `one` reddens. Mutation: `width=100` -- both redden."""
    details = _table_only_detail(*names)
    assert all(len(line) <= 80 for line in details)
    assert " ".join(details) == sentence


def test_a_table_whose_entries_are_all_tagged_has_no_detail():
    """Mutation: name every environment, tagged or not -- `dev-eu` is listed."""
    table = ec.parse_table("layout: tf_vars\n\nenvironments:\n  dev-eu:\n    region: eu-west-1\n")
    assert onboard._table_item(ctx(table=table)) == ("ok", "`.github/shipmate-config.yml`", [])


def test_a_table_entry_is_provisioned_before_any_stack_tags_it(monkeypatch, tmp_path, capsys):
    """Its `<env>-apply` must carry the default-branch policy before the first pull request
    tagging a stack into it merges, or that merge deploys into an environment GitHub
    auto-creates with none.

    Mutation: derive `envs` from `stacks_by_env` alone.
    """
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "shipmate-config.yml").write_text(
        "layout: tf_vars\n\nenvironments:\n  prod:\n    region: eu-west-1\n",
        encoding="utf-8",
        newline="\n",
    )
    routes = {k.replace("prdo", "prod"): v for k, v in PRDO_ROUTES.items()}
    _, exc = run_main(monkeypatch, tmp_path, routes, ["--dry-run"], membership=({}, {}))
    assert exc.code == 0
    assert "environments: prod" in capsys.readouterr().out.splitlines()
    assert _would_create("prod") == ["prod-plan", "prod-apply", "prod-apply branch policy"]


def test_no_tagged_stack_and_no_table_binds_nothing(monkeypatch, tmp_path, capsys):
    """Mutation: print the empty join, `environments: `."""
    _, exc = run_main(monkeypatch, tmp_path, {}, ["--dry-run"], membership=({}, {"a": []}))
    assert exc.code == 0
    assert "environments: (none)" in capsys.readouterr().out.splitlines()


UNMANAGED_ITEM = "unmanaged stacks"
STACK_TAGS_ITEM = "stack tags"


def test_an_untagged_stack_is_listed_and_the_run_exits_0(monkeypatch, tmp_path, capsys):
    """Mutation: `_unmanaged_item` also calls `report("differs", ...)` -- the run exits 2."""
    membership = ({"dev-eu": ["a"]}, {"a": ["env/dev-eu"], "b": []})
    _, exc = run_main(monkeypatch, tmp_path, {}, ["--dry-run"], membership=membership)
    assert exc.code == 0
    assert checklist_items(checklist_of(capsys.readouterr().out))[UNMANAGED_ITEM] == (
        "todo",
        [
            "1 stack(s) carry no `env/*` tag:",
            "  b",
            "shipmate runs no stack without an `env/*` tag. A stack kept out on purpose",
            "needs nothing.",
        ],
    )


def test_more_than_ten_unmanaged_stacks_name_ten_and_count_the_rest():
    """Mutation: `_stack_list`'s slice `[:10]` to `[:11]` -- `s10` is named."""
    tags_by_stack = {f"s{i:02}": [] for i in range(12)}
    assert onboard._unmanaged_item(ctx(tags_by_stack=tags_by_stack)) == (
        "todo",
        UNMANAGED_ITEM,
        [
            "12 stack(s) carry no `env/*` tag:",
            *[f"  s{i:02}" for i in range(10)],
            "  and 2 more",
            "shipmate runs no stack without an `env/*` tag. A stack kept out on purpose",
            "needs nothing.",
        ],
    )


def test_a_stack_with_two_workload_tags_is_a_stack_tags_line(monkeypatch, tmp_path, capsys):
    """`full_tree` collects the refusal into `ctx["tree_errors"]` and drops only that stack's
    cells, so `b`'s workload gap is still checked.

    Mutations: call `full_tree` without the errors list -- the tree is lost and `b`'s line is
    gone; that and remove the `ec._gather` wrapper -- the refusal raises into `main`.
    """
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "shipmate-config.yml").write_text(
        "layout: tf_vars\n\nidentities:\n  dev:\n    aws:\n"
        "      apply: arn:aws:iam::111111111111:role/apply\n\n"
        "environments:\n  dev-eu:\n    region: eu-west-1\n    identity: dev\n"
        "    workloads: [core]\n",
        encoding="utf-8",
        newline="\n",
    )
    membership = (
        {"dev-eu": ["a", "b"]},
        {"a": ["env/dev-eu", "workload/x", "workload/y"], "b": ["env/dev-eu", "workload/net"]},
    )
    _, exc = run_main(monkeypatch, tmp_path, {}, ["--dry-run"], membership=membership)
    assert exc.code == 0
    assert checklist_items(checklist_of(capsys.readouterr().out))[STACK_TAGS_ITEM] == (
        "todo",
        [
            "stack 'a' carries 2 workload tags (workload/x, workload/y), and a stack carries at "
            "most one `workload/<name>` tag. Keep one in the stack's `tags` and remove the rest.",
            "1 cell(s) carry a workload tag their environment's workloads list does not name: "
            "b in dev-eu (workload/net; dev-eu lists core). A listed workload is the only one "
            "the default branch grants a role to. Retag the stack, or add the workload to "
            "environments.<env>.workloads in .github/shipmate-config.yml on the default "
            "branch, which is where this table is read from: merge it there on its own pull "
            "request first.",
            "`environments.dev-eu.workloads` lists core, which no stack in dev-eu tags. Tag a",
            "stack with each, or remove it from the list.",
        ],
    )


def test_a_slug_collision_is_a_stack_tags_line(monkeypatch, tmp_path, capsys):
    """`guard_slug_collisions` raises after the per-stack collection, so the tree is lost and
    the workload-gap check is skipped.

    Mutation: remove the `ec._gather` wrapper around `full_tree` in `main` -- the refusal
    raises into `main`.
    """
    membership = ({"dev-eu": ["a/b", "a-b"]}, {"a/b": ["env/dev-eu"], "a-b": ["env/dev-eu"]})
    _, exc = run_main(monkeypatch, tmp_path, {}, ["--dry-run"], membership=membership)
    assert exc.code == 0
    assert checklist_items(checklist_of(capsys.readouterr().out))[STACK_TAGS_ITEM] == (
        "todo",
        [
            "a-b, a/b all map to the plan artifact 'plan.dev-eu.a-b': distinct stack paths "
            "sharing one artifact name would make an apply download another stack's plan. "
            "Rename one so the path->'-' slug is unique."
        ],
    )


def _listing(**workloads):
    """A table whose entries each name an identity and list `workloads[env]`."""
    return {
        "layout": "folder",
        "identities": {"dev": {"aws": {"apply": "arn:aws:iam::111111111111:role/apply"}}},
        "environments": {
            env: {"region": "eu-west-1", "identity": "dev", "workloads": names}
            for env, names in workloads.items()
        },
    }


def test_a_cell_whose_workload_its_list_does_not_name_is_listed():
    """Mutation: drop the `refuse_workload_gaps` call from `_stack_tags_item` -- `ok`."""
    context = ctx(
        table=_listing(**{"dev-eu": ["core"]}),
        cells=[{"stack": "a", "environment": "dev-eu", "workload": "net"}],
        tagged={"dev-eu": frozenset({"core", "net"})},
    )
    assert onboard._stack_tags_item(context) == (
        "todo",
        STACK_TAGS_ITEM,
        [
            "1 cell(s) carry a workload tag their environment's workloads list does not name: "
            "a in dev-eu (workload/net; dev-eu lists core). A listed workload is the only one "
            "the default branch grants a role to. Retag the stack, or add the workload to "
            "environments.<env>.workloads in .github/shipmate-config.yml on the default "
            "branch, which is where this table is read from: merge it there on its own pull "
            "request first."
        ],
    )


def test_a_listed_workload_no_stack_in_its_environment_tags_is_listed():
    """`api` is tagged, but only on a `dev-us` stack.

    Mutation: `untagged_workloads` diffs against the workloads tagged anywhere in the tree
    instead of `tagged[env]` -- `ok`.
    """
    context = ctx(
        table=_listing(**{"dev-eu": ["api"], "dev-us": ["api"]}),
        tagged={"dev-eu": frozenset(), "dev-us": frozenset({"api"})},
    )
    assert onboard._stack_tags_item(context) == (
        "todo",
        STACK_TAGS_ITEM,
        [
            "`environments.dev-eu.workloads` lists api, which no stack in dev-eu tags. Tag a",
            "stack with each, or remove it from the list.",
        ],
    )


def test_a_needs_predecessor_no_entry_declares_and_no_stack_tags_is_todo():
    """`staging` is a declared, untagged entry: the provisioned-ahead order, already the
    `ok` provisioned line. `stagng` is neither an entry nor tagged, so it orders nothing while
    reading as ordering, and the item is `todo`.

    Mutations: pass only the tagged set to `stale_needs` -- `staging` is named too; return
    `ok` for the stale-needs case.
    """
    table = {
        "layout": "folder",
        "environments": {"prod": {"needs": ["staging", "stagng"]}, "staging": {}},
    }
    context = ctx(table=table, envs=["prod", "staging"], tagged={"prod": frozenset()})
    assert onboard._table_item(context) == (
        "todo",
        "`.github/shipmate-config.yml`",
        [
            "Provisioned for staging, which no stack tags yet: its first tagging pull request",
            "deploys under that environment's protection. If the name is a typo, fix the",
            "entry.",
            "`needs` names stagng, which no entry declares and no stack tags, so it orders",
            "nothing. If a name is a typo, fix it.",
        ],
    )


def test_onboard_and_env_config_share_one_table_rule(monkeypatch):
    """Both items render what env-config's functions return.

    Mutation: a local copy of either rule in onboard -- the sentinel is not shown.
    """
    monkeypatch.setattr(onboard.ec, "untagged_workloads", lambda table, tagged: [("e", ["SEN"])])
    monkeypatch.setattr(onboard.ec, "stale_needs", lambda table, envs: ["SENTINEL"])
    context = ctx(table={"layout": "folder", "environments": {"dev-eu": {}}})
    assert onboard._stack_tags_item(context)[2] == [
        "`environments.e.workloads` lists SEN, which no stack in e tags. Tag a stack with",
        "each, or remove it from the list.",
    ]
    assert onboard._table_item(context)[2] == [
        "`needs` names SENTINEL, which no entry declares and no stack tags, so it orders",
        "nothing. If a name is a typo, fix it.",
    ]


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


def test_a_bare_env_alongside_only_a_plan_env_is_reported_as_unused(monkeypatch):
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


def test_shared_mode_reports_the_unused_naming_too(monkeypatch):
    """Running once without `shared: true` and once with it produces all three environments.
    The second run must not silently bind the bare one: nothing binds the pair in shared
    mode, so the probe runs in shared mode as well.

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
    gates the plan cells and every drift sweep covering it too -- GitHub has no per-job filter.
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
            "so the plan cells and every drift sweep covering it do not start immediately either. "
            "To gate applies alone, split it into `dev-eu-plan` and `dev-eu-apply` and "
            "drop `shared: true` from `environments.dev-eu`.",
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


DRIFT_ITEM = "drift sweeps"
_FIXTURES = pathlib.Path(__file__).parent / "fixtures"
# Byte-for-byte copies of committed blobs (`git cat-file blob`), LF: repo-example-stacks
# `.github/workflows/drift-dev-eu.yml` at a76bffa and repo-example-folders
# `.github/workflows/drift.yml` at e432598.
STACKS_DRIFT = (_FIXTURES / "drift-stacks-dev-eu.yml").read_bytes()
FOLDERS_DRIFT = (_FIXTURES / "drift-folders.yml").read_bytes()
NO_DRIFT_FILE = [
    "Add one workflow file per drift sweep from the `shipmate drift` fence in",
    "docs/getting-started.md, under any name in `.github/workflows/`, with the same",
    "pin as `shipmate.yml`. Without one, no stack is checked for drift (docs/drift.md).",
]
DRIFT_CALL = "    uses: ship-iac/shipmate/.github/workflows/drift.yml@main\n"


def _read_line(*names):
    return (
        f"Read {', '.join(names)} in this working tree; a drift run reads the default "
        "branch's copy."
    )


def _drift_job(with_lines=""):
    """A minimal workflow file whose one job calls engine `drift.yml`."""
    return f"on:\n  workflow_dispatch:\njobs:\n  drift:\n{DRIFT_CALL}{with_lines}"


#: A table every drift test's `ctx()` validates against; a drift run refuses without one.
DRIFT_TABLE = {"layout": "folder", "environments": {"dev-eu": {}, "dev-us": {}}}


def _drift_ctx(tmp_path, files, **over):
    """`ctx` rooted at `tmp_path`, with `files` ({name: bytes or str}) in its workflows and
    `DRIFT_TABLE` unless `over` names a table."""
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    for name, body in files.items():
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        (workflows / name).write_bytes(data)
    return ctx(root=tmp_path, **{"table": DRIFT_TABLE, **over})


def test_an_absent_table_leaves_coverage_uncomputed(tmp_path):
    """A drift run reads the table before any cell and refuses when it is absent.

    Mutation: drop the absent-table blocker from `_coverage_blockers` -- `ok`.
    """
    cells, tags = _tree(("a", "dev-eu"))
    context = _drift_ctx(
        tmp_path, {"drift.yml": FOLDERS_DRIFT}, table=None, cells=cells, tags_by_stack=tags
    )
    assert onboard._drift_sweeps_item(context) == (
        "cannot check",
        DRIFT_ITEM,
        [
            "Coverage was not computed:",
            "  `.github/shipmate-config.yml` is absent, and a drift run refuses without it; see "
            "its item.",
            _read_line("drift.yml"),
        ],
    )


def _tree(*cells):
    """(cells, tags_by_stack) for `(stack, env)` pairs, each stack tagged with its envs."""
    tags = {}
    for stack, env in cells:
        tags.setdefault(stack, []).append(f"env/{env}")
    return (
        [{"stack": s, "environment": e, "workload": ""} for s, e in cells],
        tags,
    )


def test_a_cell_outside_every_drift_query_is_listed(tmp_path):
    """The `repo-example-stacks` file sweeps `env/dev-eu`; the `dev-us` cell is in no sweep.

    Mutation: `covered &= ...` in place of `covered |= ...` -- the dev-eu cell is listed too.
    """
    cells, tags = _tree(("a", "dev-eu"), ("a", "dev-us"))
    context = _drift_ctx(
        tmp_path, {"drift-dev-eu.yml": STACKS_DRIFT}, cells=cells, tags_by_stack=tags
    )
    assert onboard._drift_sweeps_item(context) == (
        "todo",
        DRIFT_ITEM,
        [
            "1 cell(s) no drift file sweeps:",
            "  a (dev-us)",
            "Widen a `tags` query, or add a drift file for them.",
            _read_line("drift-dev-eu.yml"),
        ],
    )


def test_a_drift_file_without_tags_sweeps_every_cell(tmp_path):
    """The `repo-example-folders` file carries no `tags`, which `filter_cells` reads as every
    cell.

    Mutation: skip a file whose query is empty (`if not query: continue`) -- both cells are
    listed.
    """
    cells, tags = _tree(("a", "dev-eu"), ("b", "dev-us"))
    context = _drift_ctx(tmp_path, {"drift.yml": FOLDERS_DRIFT}, cells=cells, tags_by_stack=tags)
    assert onboard._drift_sweeps_item(context) == ("ok", DRIFT_ITEM, [_read_line("drift.yml")])


def test_the_drift_query_is_parsed_by_build_matrix(tmp_path, monkeypatch):
    """Pinned by identity: the item shows what `build-matrix`'s `_parse_tag_query` raises.

    Mutation: a local copy of `_parse_tag_query` (and the `filter_cells` calling it) in
    onboard -- the sentinel is not shown.
    """

    def sentinel(query):
        raise SystemExit("::error::SENTINEL")

    monkeypatch.setattr(onboard.bm, "_parse_tag_query", sentinel)
    cells, tags = _tree(("a", "dev-eu"))
    context = _drift_ctx(
        tmp_path, {"drift-dev-eu.yml": STACKS_DRIFT}, cells=cells, tags_by_stack=tags
    )
    assert onboard._drift_sweeps_item(context)[2][0] == "drift-dev-eu.yml: SENTINEL"


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (256, ("ok", DRIFT_ITEM, [_read_line("drift.yml")])),
        (
            257,
            (
                "todo",
                DRIFT_ITEM,
                [
                    "drift.yml: 257 plan cells exceeds the GitHub Actions matrix limit of 256. "
                    "A drift sweep is one matrix; split it across more drift files, each calling "
                    "drift.yml with a narrower `tags` query (docs/drift.md).",
                    "257 cell(s) no drift file sweeps:",
                    *[f"  s{i:03} (dev-eu)" for i in range(10)],
                    "  and 247 more",
                    "Widen a `tags` query, or add a drift file for them.",
                    _read_line("drift.yml"),
                ],
            ),
        ),
    ],
)
def test_a_sweep_above_the_matrix_limit_is_listed(tmp_path, count, expected):
    """Both boundaries, through `build-matrix`'s own `cap_cells`. A sweep above the limit
    refuses before any cell plans, so it covers none of its cells.

    Mutations: `> MATRIX_LIMIT + 1` in `cap_cells` -- 257 is `ok`; `>= MATRIX_LIMIT` -- 256
    is listed; count the cells before `cap_cells` -- 257 lists no uncovered cell.
    """
    cells, tags = _tree(*[(f"s{i:03}", "dev-eu") for i in range(count)])
    context = _drift_ctx(tmp_path, {"drift.yml": FOLDERS_DRIFT}, cells=cells, tags_by_stack=tags)
    assert onboard._drift_sweeps_item(context) == expected


def test_a_dead_clause_and_an_empty_sweep_are_each_listed(tmp_path):
    """`filter_cells`' own notices, one line each, its `::notice::` prefix removed. The block
    value `env/dev-eu,env/ghost` is one OR query, comma included.

    `dead.yaml` pins the second suffix a workflow file may carry.

    Mutations: drop the captured-notice lines -- `ok`; drop `".yaml"` from `_drift_files`'
    suffixes -- `dead.yaml` is not read.
    """
    cells, tags = _tree(("a", "dev-eu"))
    files = {
        "dead.yaml": _drift_job("    with:\n      tags: env/dev-eu,env/ghost\n"),
        "ghost.yml": _drift_job("    with:\n      tags: env/ghost\n"),
    }
    context = _drift_ctx(tmp_path, files, cells=cells, tags_by_stack=tags)
    assert onboard._drift_sweeps_item(context) == (
        "todo",
        DRIFT_ITEM,
        [
            "dead.yaml: the drift tags query 'env/dev-eu,env/ghost' has clause(s) matching no "
            "cell: env/ghost. No stack carries: env/ghost. Tags match in their on-disk form, "
            "such as 'env/dev-eu'.",
            "ghost.yml: the drift tags query 'env/ghost' matches no stack x environment cell, "
            "so this sweep is empty. No stack carries: env/ghost. Tags match in their on-disk "
            "form, such as 'env/dev-eu'.",
            _read_line("dead.yaml", "ghost.yml"),
        ],
    )


def test_a_tree_error_leaves_coverage_uncomputed(tmp_path):
    """A two-tag stack's cells are dropped from the tree and a slug collision loses it, so
    either makes coverage unknowable rather than a list of falsely uncovered cells.

    Mutation: drop the `tree_errors` condition (`elif False:`) -- the two-tag case reports
    `ok` over a tree missing a stack.
    """
    cells, tags = _tree(("a", "dev-eu"))
    two_tags = _drift_ctx(
        tmp_path,
        {"drift.yml": FOLDERS_DRIFT},
        cells=cells,
        tags_by_stack=tags,
        tree_errors=["::error::stack 'b' carries 2 workload tags"],
    )
    assert onboard._drift_sweeps_item(two_tags) == (
        "cannot check",
        DRIFT_ITEM,
        [
            "Coverage was not computed:",
            "  a stack with two `workload/*` tags has its cells dropped; see `stack tags`.",
            _read_line("drift.yml"),
        ],
    )
    collided = {**two_tags, "cells": None, "tree_errors": ["::error::a-b, a/b all map"]}
    assert onboard._drift_sweeps_item(collided) == (
        "cannot check",
        DRIFT_ITEM,
        [
            "Coverage was not computed:",
            "  a slug collision loses the tree; see `stack tags`.",
            _read_line("drift.yml"),
        ],
    )


def test_a_commented_out_drift_call_is_no_drift_file(tmp_path):
    """A commented-out call sweeps nothing. Documents the parser's behaviour, no mutation: a
    comment never reaches the parsed document."""
    text = "on:\n  workflow_dispatch:\njobs:\n  drift:\n  #" + DRIFT_CALL[3:]
    context = _drift_ctx(tmp_path, {"drift.yml": text})
    assert onboard._drift_sweeps_item(context) == ("todo", DRIFT_ITEM, NO_DRIFT_FILE)


def test_a_workflow_file_that_is_not_utf8_is_cannot_check(tmp_path):
    """It may call `drift.yml`, so coverage is unknown rather than a crash with exit 1.

    Mutation: drop the `UnicodeDecodeError` handler -- the item raises.
    """
    cells, tags = _tree(("a", "dev-eu"))
    files = {"drift.yml": FOLDERS_DRIFT, "latin.yml": b"name: caf\xe9\n"}
    context = _drift_ctx(tmp_path, files, cells=cells, tags_by_stack=tags)
    assert onboard._drift_sweeps_item(context) == (
        "cannot check",
        DRIFT_ITEM,
        [
            "Coverage was not computed:",
            "  latin.yml: it is not UTF-8 text, so it was not read.",
            _read_line("drift.yml", "latin.yml"),
        ],
    )


def test_another_repositorys_drift_yml_is_no_drift_file(tmp_path):
    """Mutation: drop the repository from `doctor._engine_calls`' pattern -- the file is read
    as a sweep of every cell."""
    text = _drift_job().replace("ship-iac/shipmate", "other/tools")
    context = _drift_ctx(tmp_path, {"drift.yml": text})
    assert onboard._drift_sweeps_item(context) == ("todo", DRIFT_ITEM, NO_DRIFT_FILE)


def test_tags_outside_the_drift_job_are_not_the_query(tmp_path):
    """An `on: push: tags:` filter and another job's `with: tags:` are not the drift job's
    `with.tags`.

    Mutation: read `with.tags` from the last job (`dr._jobs(dr._mapping(doc))[-1][1]`) in
    `_drift_file` -- `env/x` matches no cell, `todo`.
    """
    text = (
        "on:\n  push:\n    tags: [v1]\njobs:\n  drift:\n"
        + DRIFT_CALL
        + "    with:\n      tags: env/dev-eu\n"
        + "  build:\n    uses: ./.github/workflows/build.yml\n    with:\n      tags: env/x\n"
    )
    cells, tags = _tree(("a", "dev-eu"))
    context = _drift_ctx(tmp_path, {"drift.yml": text}, cells=cells, tags_by_stack=tags)
    assert onboard._drift_sweeps_item(context) == ("ok", DRIFT_ITEM, [_read_line("drift.yml")])


@pytest.mark.parametrize(
    ("with_lines", "expected"),
    [
        ("    with: { runs_on: ubuntu-slim, tags: env/dev-eu }\n", ("env/dev-eu", "")),
        (
            '    with: { tags: "env/dev-eu,env/dev-us", runs_on: x }\n',
            ("env/dev-eu,env/dev-us", ""),
        ),
        ("    with:\n      tags: 'env/dev-eu:workload/app'\n", ("env/dev-eu:workload/app", "")),
        ("    with:\n      tags: env/dev-eu,env/dev-us\n", ("env/dev-eu,env/dev-us", "")),
        ("    with:\n      tags: >-\n        env/dev-eu\n", ("env/dev-eu", "")),
        (
            "    with:\n      tags: ${{ inputs.tags }}\n",
            (None, "its `tags` is a `${{ }}` expression, which only a run resolves"),
        ),
        ("    with:\n      runs_on: ubuntu-slim\n", ("", "")),
    ],
    ids=["flow", "flow-quoted-comma", "block-quoted", "block", "folded", "expression", "no-key"],
)
def test_the_drift_query_is_read_from_the_calling_jobs_with(tmp_path, with_lines, expected):
    """`jobs.<drift job>.with.tags`, whatever its form; a `build` job ahead of the drift job
    hands its own workflow a `tags` input that is not the query.

    Mutations: read `with.tags` from the first job (`dr._jobs(dr._mapping(doc))[0][1]`) in
    `_drift_file` -- every row reads `env/build`; drop the `${{` test -- `expression` is a
    query.
    """
    text = (
        "on:\n  workflow_dispatch:\njobs:\n  build:\n    uses: ./.github/workflows/build.yml\n"
        "    with:\n      tags: env/build\n  drift:\n" + DRIFT_CALL + with_lines
    )
    path = tmp_path / "drift.yml"
    path.write_text(text, encoding="utf-8", newline="\n")
    assert onboard._drift_file(path) == ("drift.yml", *expected)


def test_a_drift_finding_is_listed_and_the_run_exits_0(monkeypatch, tmp_path, capsys):
    """Mutation: the item calls `report("differs", ...)` on a finding -- the run exits 2."""
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "shipmate-config.yml").write_text(
        "layout: folder\n\nenvironments:\n  dev-eu: {}\n", encoding="utf-8", newline="\n"
    )
    (tmp_path / ".github" / "workflows" / "ghost.yml").write_text(
        _drift_job("    with:\n      tags: env/ghost\n"), encoding="utf-8", newline="\n"
    )
    _, exc = run_main(monkeypatch, tmp_path, {}, ["--dry-run"])
    assert exc.code == 0
    assert checklist_items(checklist_of(capsys.readouterr().out))[DRIFT_ITEM] == (
        "todo",
        [
            "ghost.yml: the drift tags query 'env/ghost' matches no stack x environment cell, "
            "so this sweep is empty. No stack carries: env/ghost. Tags match in their on-disk "
            "form, such as 'env/dev-eu'.",
            "1 cell(s) no drift file sweeps:",
            "  stacks/app (dev-eu)",
            "Widen a `tags` query, or add a drift file for them.",
            _read_line("ghost.yml"),
        ],
    )


def test_a_workflow_file_that_does_not_parse_is_cannot_check(tmp_path):
    """It may be a drift file, so coverage is unknown: the parser's message names why.

    Mutation: return None (no drift file) for a file that does not parse -- `ok`.
    """
    cells, tags = _tree(("a", "dev-eu"))
    files = {"broken.yml": "jobs:\n  a: 1\n  a: 2\n", "drift.yml": FOLDERS_DRIFT}
    context = _drift_ctx(tmp_path, files, cells=cells, tags_by_stack=tags)
    assert onboard._drift_sweeps_item(context) == (
        "cannot check",
        DRIFT_ITEM,
        [
            "Coverage was not computed:",
            "  broken.yml: it does not parse as YAML: duplicate key 'a' (line 3, column 3).",
            _read_line("broken.yml", "drift.yml"),
        ],
    )


def test_a_file_calling_drift_yml_twice_is_cannot_check(tmp_path):
    """One job's query alone would be read, and the second job's would go unread.

    Mutation: `len(calls) > 2` for `len(calls) > 1` in `_drift_file` -- the first job's query
    alone is read and the item is `ok`.
    """
    text = _drift_job("    with:\n      tags: env/dev-eu\n") + "  second:\n" + DRIFT_CALL
    cells, tags = _tree(("a", "dev-eu"))
    context = _drift_ctx(tmp_path, {"drift.yml": text}, cells=cells, tags_by_stack=tags)
    assert onboard._drift_sweeps_item(context) == (
        "cannot check",
        DRIFT_ITEM,
        [
            "Coverage was not computed:",
            "  drift.yml: it calls `drift.yml` more than once.",
            _read_line("drift.yml"),
        ],
    )


def test_a_sweep_refusing_a_workload_gap_covers_no_cell(tmp_path):
    """A drift detect runs `stamp_rows` and `refuse_workload_gaps` on its kept cells, and the
    gap refuses the whole sweep, so the folders file sweeps nothing here.

    Mutation: drop the `refuse_workload_gaps` call from `_sweep` -- `ok`.
    """
    cells = [{"stack": "a", "environment": "dev-eu", "workload": "net"}]
    context = _drift_ctx(
        tmp_path,
        {"drift.yml": FOLDERS_DRIFT},
        table=_listing(**{"dev-eu": ["core"]}),
        cells=cells,
        tags_by_stack={"a": ["env/dev-eu", "workload/net"]},
        tagged={"dev-eu": frozenset({"net"})},
    )
    assert onboard._drift_sweeps_item(context) == (
        "todo",
        DRIFT_ITEM,
        [
            "drift.yml: 1 cell(s) carry a workload tag their environment's workloads list does "
            "not name: a in dev-eu (workload/net; dev-eu lists core). A listed workload is the "
            "only one the default branch grants a role to. Retag the stack, or add the workload "
            "to environments.<env>.workloads in .github/shipmate-config.yml on the default branch, "
            "which is where this table is read from: merge it there on its own pull request "
            "first.",
            "1 cell(s) no drift file sweeps:",
            "  a (dev-eu)",
            "Widen a `tags` query, or add a drift file for them.",
            _read_line("drift.yml"),
        ],
    )


def test_a_table_failing_validation_leaves_coverage_uncomputed(tmp_path):
    """A drift detect refuses on it before any cell runs.

    Mutation: drop the `ec.validate` check from `_coverage_blockers` -- `ok`.
    """
    table = ec.parse_table("layout: tf_vars\n\nenvironments:\n  dev-eu:\n    shared: false\n")
    cells, tags = _tree(("a", "dev-eu"))
    context = _drift_ctx(
        tmp_path, {"drift.yml": FOLDERS_DRIFT}, table=table, cells=cells, tags_by_stack=tags
    )
    assert onboard._drift_sweeps_item(context) == (
        "cannot check",
        DRIFT_ITEM,
        [
            "Coverage was not computed:",
            "  `.github/shipmate-config.yml` fails validation; see its item.",
            _read_line("drift.yml"),
        ],
    )


def test_an_unreadable_workflow_file_or_directory_is_cannot_check(tmp_path, monkeypatch):
    """An `OSError` comes after the run's writes, so it must not stop the run with exit 1.

    Python 3.11-3.13 re-raise a `stat` `PermissionError` from `is_dir` and `is_file`, so
    those calls sit inside the same handlers.

    Mutations: drop the `OSError` handler in `_drift_file`, or move its `is_file` call out of
    the `try` -- the item raises `PermissionError`; drop the one around `iterdir`, or move the
    `is_dir` call out of it -- the same.
    """
    context = _drift_ctx(tmp_path, {"drift.yml": FOLDERS_DRIFT, "locked.yml": b"x\n"})
    real = pathlib.Path.read_text

    def read_text(self, *args, **kwargs):
        if self.name == "locked.yml":
            raise PermissionError(13, "Permission denied")
        return real(self, *args, **kwargs)

    locked = [
        "Coverage was not computed:",
        "  locked.yml: it could not be read: [Errno 13] Permission denied.",
    ]
    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    assert onboard._drift_sweeps_item(context)[2][:2] == locked
    monkeypatch.undo()
    real_is_file = pathlib.Path.is_file

    def is_file(self):
        if self.name == "locked.yml":
            raise PermissionError(13, "Permission denied")
        return real_is_file(self)

    monkeypatch.setattr(pathlib.Path, "is_file", is_file)
    assert onboard._drift_sweeps_item(context)[2][:2] == locked
    monkeypatch.undo()

    def denied(self):
        raise PermissionError(13, "Permission denied")

    listed = (
        "cannot check",
        DRIFT_ITEM,
        [
            "Coverage was not computed:",
            "  .github/workflows/: it could not be listed: [Errno 13] Permission denied.",
            _read_line(".github/workflows/"),
        ],
    )
    for method in ("iterdir", "is_dir"):
        monkeypatch.setattr(pathlib.Path, method, denied)
        assert onboard._drift_sweeps_item(context) == listed, method
        monkeypatch.undo()

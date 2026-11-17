"""`scripts/state-save` and `actions/state-save`: one cell's sealed state saved by a leg that
runs no pull-request code.

Threat model: an accidental edit that (a) saves bytes the cell did not produce under its
prefix (an artifact taken without comparing its digest, a key built from the bundle rather
than the row), (b) writes outside the stack (the bundle's path unchecked), (c) passes the leg
without an exact-key hit, so the cell's check completes over a state no restore finds, or
(d) lets a re-run save an earlier attempt's state late. A cell sealing other bytes under its
own digest is out of scope: its code already controls its own state.

The gh calls are stubbed: `run` for the listing, `_download` for each archive.
"""

import hashlib
import io
import json
import os
import zipfile

import pytest
from _loader import (
    ENGINE,
    action_yaml,
    bash_only,
    github_outputs,
    load_script,
    local_action,
    run_step,
)

ss = load_script("state-save")

PATH = "stacks/app/.state/dev-eu/terraform.tfstate"
NAME = "state.dev-eu.stacks-app.2"
CIPHER = b"SHPS1" + b"\x00\x01\xff" * 30


def _bundle(path=PATH, cipher=CIPHER):
    return b"shipmate-state-bundle-v1\n" + path.encode() + b"\n" + cipher


def _zip(bundle, member="state.bundle"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member, bundle)
    return buf.getvalue()


def _digest(bundle):
    return hashlib.sha256(bundle).hexdigest()


class _Leg:
    """`main` with gh stubbed. `artifacts` is the listing, `archives` maps an id to its zip."""

    def __init__(self, monkeypatch, tmp_path, artifacts, archives, *, digest, **env):
        self.calls = []
        self.out = tmp_path / "out"
        self.out.write_text("", encoding="utf-8")
        self.ws = tmp_path / "ws"
        self.ws.mkdir()
        base = {
            "RUNNER_ENVIRONMENT": "github-hosted",
            "STACK": "stacks/app",
            "ENV_NAME": "dev-eu",
            "SLUG": "stacks-app",
            "DIGEST": digest,
            "ATTEMPT": "2",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_RUN_ID": "77",
            "GITHUB_REPOSITORY": "o/r",
            "GITHUB_WORKSPACE": str(self.ws),
            "GITHUB_OUTPUT": str(self.out),
        }
        for k, v in {**base, **env}.items():
            monkeypatch.setenv(k, v)

        def run(argv):
            self.calls.append(argv)
            return "".join(json.dumps(a) + "\n" for a in artifacts)

        def download(artifact_id):
            self.calls.append(artifact_id)
            return archives[artifact_id]

        monkeypatch.setattr(ss, "run", run)
        monkeypatch.setattr(ss, "_download", download)

    def __call__(self):
        try:
            ss.main()
            code = 0
        except SystemExit as exc:
            code = exc.code
        return code, github_outputs(self.out)


NO_MATCH = f"::error::no artifact of this run named {NAME} matches the cell's state digest."


@pytest.mark.parametrize(
    ("artifacts", "archives", "ok"),
    [
        ([{"id": 1, "name": NAME}], {1: _zip(_bundle())}, True),
        ([{"id": 1, "name": NAME}], {1: _zip(_bundle(cipher=b"SHPS1planted" * 9))}, False),
        (
            [{"id": 1, "name": NAME}, {"id": 2, "name": NAME}],
            {1: _zip(_bundle(cipher=b"SHPS1planted" * 9)), 2: _zip(_bundle())},
            True,
        ),
        (
            [{"id": 1, "name": NAME}, {"id": 2, "name": NAME}],
            {1: _zip(_bundle()), 2: _zip(_bundle())},
            True,
        ),
        ([{"id": 3, "name": "state.dev-eu.other.2"}], {3: _zip(_bundle())}, False),
    ],
    ids=["one", "squatted", "planted-beside", "identical-copies", "other-name"],
)
def test_only_an_artifact_of_the_cells_name_whose_bytes_match_is_used(
    monkeypatch, tmp_path, artifacts, archives, ok
):
    """Mutations: take the first artifact without comparing its digest; refuse two identical
    copies; drop the name filter on the listing."""
    leg = _Leg(monkeypatch, tmp_path, artifacts, archives, digest=_digest(_bundle()))
    code, out = leg()
    if ok:
        assert (code, out) == (0, {"path": PATH, "kind": "save"})
        assert (leg.ws / PATH).read_bytes() == CIPHER
    else:
        assert (code, out) == (NO_MATCH, {})
        assert not (leg.ws / PATH).exists()


def test_the_listing_asks_for_the_cells_artifact_name(monkeypatch, tmp_path):
    """The name comes from `state-handoff.artifact_name`, the one derivation. Mutation: build
    the name with `-` joins."""
    leg = _Leg(monkeypatch, tmp_path, [], {}, digest=_digest(_bundle()))
    leg()
    assert leg.calls == [
        [
            "gh",
            "api",
            "--paginate",
            "--jq",
            ".artifacts[]",
            f"repos/o/r/actions/runs/77/artifacts?name={NAME}&per_page=100",
        ]
    ]


@pytest.mark.parametrize(
    "path", ["stacks/other/terraform.tfstate", "stacks/app/../other/x", "/etc/x"]
)
def test_a_bundle_path_outside_the_stack_is_refused(monkeypatch, tmp_path, path):
    """Mutation: skip `validate`."""
    bundle = _bundle(path=path)
    leg = _Leg(
        monkeypatch, tmp_path, [{"id": 1, "name": NAME}], {1: _zip(bundle)}, digest=_digest(bundle)
    )
    code, out = leg()
    assert out == {}
    assert code.startswith("::error::stacks/app: the state path ")
    assert list(leg.ws.rglob("*")) == []


def test_the_root_stacks_path_is_passed_on_as_the_restore_names_it(monkeypatch, tmp_path):
    """`actions/cache` versions an entry on its path string, and the restore passes
    `./terraform.tfstate` for the root stack `.`. Mutation: output `os.path.normpath(path)`."""
    bundle = _bundle(path="./terraform.tfstate")
    leg = _Leg(
        monkeypatch,
        tmp_path,
        [{"id": 1, "name": "state.dev-eu...2"}],
        {1: _zip(bundle)},
        digest=_digest(bundle),
        STACK=".",
        SLUG=".",
    )
    assert leg() == (0, {"path": "./terraform.tfstate", "kind": "save"})
    assert (leg.ws / "terraform.tfstate").read_bytes() == CIPHER


def test_a_bundle_without_the_header_is_refused(monkeypatch, tmp_path):
    """Mutation: drop the header test."""
    bundle = PATH.encode() + b"\n" + CIPHER
    leg = _Leg(
        monkeypatch, tmp_path, [{"id": 1, "name": NAME}], {1: _zip(bundle)}, digest=_digest(bundle)
    )
    code, out = leg()
    assert (code, out) == (
        f"::error::the bundle in {NAME} does not start with the bundle header.",
        {},
    )


@pytest.mark.parametrize(("attempt", "kind"), [("2", "save"), ("1", "lookup")])
def test_an_earlier_attempts_cell_is_only_looked_up(monkeypatch, tmp_path, attempt, kind):
    """Mutation: `kind = "save"` always."""
    name = f"state.dev-eu.stacks-app.{attempt}"
    leg = _Leg(
        monkeypatch,
        tmp_path,
        [{"id": 1, "name": name}],
        {1: _zip(_bundle())},
        digest=_digest(_bundle()),
        ATTEMPT=attempt,
    )
    assert leg() == (0, {"path": PATH, "kind": kind})


@pytest.mark.parametrize("runner", ["self-hosted", "", "GitHub-hosted"])
def test_a_runner_not_exactly_github_hosted_refuses_before_any_call(monkeypatch, tmp_path, runner):
    """Mutation: refuse only `self-hosted`."""
    leg = _Leg(monkeypatch, tmp_path, [], {}, digest="0" * 64, RUNNER_ENVIRONMENT=runner)
    code, out = leg()
    assert (code, out, leg.calls) == (ss.HOSTED_ONLY, {}, [])


def test_the_path_is_masked_before_any_line_names_it(monkeypatch, tmp_path, capsys):
    """Mutation: print the path before masking it."""
    leg = _Leg(
        monkeypatch,
        tmp_path,
        [{"id": 1, "name": NAME}],
        {1: _zip(_bundle())},
        digest=_digest(_bundle()),
    )
    leg()
    lines = capsys.readouterr().out.splitlines()
    naming = [line for line in lines if PATH in line]
    assert naming == [f"::add-mask::{PATH}"]
    assert lines.index(naming[0]) == 0


def test_the_saver_takes_no_encryption_key():
    """The key may live only on environments the saver does not bind; the digest binds the
    bytes. Mutation: add an `encryption-key` input."""
    assert set(action_yaml("state-save")["inputs"]) == {
        "stack",
        "environment",
        "slug",
        "digest",
        "attempt",
    }
    script = (ENGINE / "scripts" / "state-save").read_text(encoding="utf-8")
    assert "ENCRYPTION_KEY" not in script + json.dumps(action_yaml("state-save"))


_STATE_WITH = {
    "stack-slug": "${{ inputs.slug }}",
    "env": "${{ inputs.environment }}",
    "mode": "${{ steps.fetch.outputs.kind }}",
    "path": "${{ steps.fetch.outputs.path }}",
    "attempt": "${{ inputs.attempt }}",
}

_STEPS = [
    {
        "name": "Fetch the cell's state bundle",
        "id": "fetch",
        "shell": "bash",
        "env": {
            "GH_TOKEN": "${{ github.token }}",
            "STACK": "${{ inputs.stack }}",
            "ENV_NAME": "${{ inputs.environment }}",
            "SLUG": "${{ inputs.slug }}",
            "DIGEST": "${{ inputs.digest }}",
            "ATTEMPT": "${{ inputs.attempt }}",
        },
        "run": 'python3 "$GITHUB_ACTION_PATH/../../scripts/state-save"',
    },
    {
        "name": "Save the state",
        "id": "primary",
        "continue-on-error": True,
        "uses": local_action("state"),
        "with": {**_STATE_WITH, "key-suffix": ""},
    },
    {
        "name": "Save the state under the retry key",
        "id": "retry",
        "if": (
            "${{ !cancelled() && steps.fetch.outcome == 'success' "
            "&& steps.primary.outputs.saved != 'true' }}"
        ),
        "uses": local_action("state"),
        "with": {**_STATE_WITH, "key-suffix": "-r1"},
    },
]


def test_the_actions_steps_are_pinned_whole():
    """The key comes from the row's slug, environment and attempt, never from the bundle.
    Mutations: `mode: save` on the `-r1` call; drop the primary's `continue-on-error` (a refused
    save then skips the retry); drop `!cancelled()` (the implied `success()` skips the retry
    after the primary failed); `stack-slug` from a fetch output; `if: success()` on the last
    step; `PRIMARY_SAVED` from the primary's `outcome == 'success'`."""
    steps = action_yaml("state-save")["runs"]["steps"]
    assert steps[:3] == _STEPS
    last = steps[3]
    assert {k: v for k, v in last.items() if k != "run"} == {
        "name": "Fail unless the state entry was found",
        "if": "always()",
        "shell": "bash",
        "env": {
            "PRIMARY_SAVED": "${{ steps.primary.outputs.saved }}",
            "RETRY_SAVED": "${{ steps.retry.outputs.saved }}",
            "STACK": "${{ inputs.stack }}",
            "ENV_NAME": "${{ inputs.environment }}",
            "SLUG": "${{ inputs.slug }}",
            "ATTEMPT": "${{ inputs.attempt }}",
        },
    }
    assert len(steps) == 4


NOT_FOUND = (
    "::error::the state of dev-eu / stacks/app was not found under state/stacks-app/dev-eu/77-2 "
    "or state/stacks-app/dev-eu/77-2-r1. Never re-run this job or its workflow run. A re-run, a "
    "shipmate apply comment, or a merge can apply this cell's reviewed plan again against the "
    "state from before the apply. Before applying or merging any plan of this stack in this "
    "environment, on any pull request, follow docs/troubleshooting.md section The apply step "
    "ran and its state was not saved.\n"
)


@bash_only
@pytest.mark.parametrize(
    ("primary", "retry", "ok"),
    [("", "true", True), ("true", "", True), ("false", "false", False), ("", "", False)],
)
def test_the_last_step_passes_only_on_a_found_entry(tmp_path, primary, retry, ok):
    """`saved` is `true` only on an exact-key hit; a failed primary save (`continue-on-error`)
    leaves its output empty. Mutations: accept any non-empty `PRIMARY_SAVED`; drop the
    never-re-run instruction."""
    body = action_yaml("state-save")["runs"]["steps"][3]["run"]
    env = {
        **os.environ,
        "PRIMARY_SAVED": primary,
        "RETRY_SAVED": retry,
        "STACK": "stacks/app",
        "ENV_NAME": "dev-eu",
        "SLUG": "stacks-app",
        "ATTEMPT": "2",
        "GITHUB_RUN_ID": "77",
    }
    r = run_step(tmp_path, body, env)
    assert (r.returncode, r.stdout) == ((0, "") if ok else (1, NOT_FOUND))


def test_a_download_keeps_the_archives_bytes(monkeypatch):
    """An archive is not text. Mutation: read it through `run`, which decodes stdout."""
    raw = _zip(_bundle())
    seen = []

    def fake(argv, **kwargs):
        seen.append(argv)
        return ss.subprocess.CompletedProcess(argv, 0, stdout=raw, stderr=b"")

    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr(ss.subprocess, "run", fake)
    monkeypatch.setattr(ss, "run", lambda argv: raw.decode("utf-8", "replace"))
    assert ss._download(5) == raw
    assert seen == [["gh", "api", "repos/o/r/actions/artifacts/5/zip"]]

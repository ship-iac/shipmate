"""The provider plugin cache is restored per stack from a committed lock file and saved by drift
and apply cells.

Every cell that runs `tofu init` on a plan or apply path restores the entry keyed on the provider
addresses and versions in its own stack's `.terraform.lock.hcl` (`scripts/provider-cache-key`),
exact match: a `restore-keys` prefix would hand one stack another stack's partial provider set.
`drift-cell` and `apply-cell` save; plan cells never do. An apply cell already holds the
environment's apply role, so its save adds no reach a plan cell lacks. The save runs only after a
restore that missed and an `init` that left at least one file in the cache, and it reuses the
restore step's key, computed before `init -reconfigure` can rewrite the committed lock: a key
computed after it could name a lock no restore reads. In `apply-cell` both steps follow
`Save state` with no status function in their `if:`, so a failed apply saves nothing, and carry
`continue-on-error: true`, so a failed save cannot fail an applied cell. When `init` fails after
a restore that hit, each cell names the restored entry in one `::error::`, so a corrupt entry can
be deleted by key.

Threat model: accidental regression of an engine file that is SHA-pinned and reviewed, such as a
save added to another cell, a dropped guard or a widened key. Each value is compared whole
against a hand-written constant.

Mutations that red this module: `!= 'true'` to `== 'true'` in the check step's `if:`; the
`cache-hit` clause dropped from `apply-cell`'s check; `always() &&` prefixed to `apply-cell`'s
check `if:`; the save's key rebuilt from the key step's digest; `continue-on-error` dropped from
`apply-cell`'s save; `-type f` dropped from the check's `find`; `restore-keys` added to one
cell's restore; `STACK` dropped from one cell's key step `env:`; `actions/cache/save` of the
cache directory added to another action; the combined `actions/cache` action used anywhere;
`steps.init.outcome == 'failure'` or the `cache-hit` clause dropped from one cell's annotation
`if:`.
"""

import os

import pytest
from _loader import ACTIONS, WORKFLOWS, action_yaml, bash_only, run_step, step_by, workflow_yaml

_KEY_STEP = {
    "name": "Provider cache key",
    "id": "provider-cache-key",
    "shell": "bash",
    "env": {"STACK": "${{ inputs.stack }}"},
    "run": 'python3 "$GITHUB_ACTION_PATH/../../scripts/provider-cache-key"',
}

_RESTORE = {
    "name": "Restore provider cache",
    "id": "provider-cache",
    "if": "${{ steps.provider-cache-key.outputs.digest != '' }}",
    "uses": "actions/cache/restore",
    "with": {
        "path": "${{ env.TF_PLUGIN_CACHE_DIR }}",
        "key": "tofu-providers-${{ runner.os }}-${{ runner.arch }}-"
        "${{ steps.provider-cache-key.outputs.digest }}",
    },
}

_CELLS = ["apply-cell", "drift-cell", "plan-cell"]

_CHECK = {
    "name": "Check the provider cache",
    "id": "provider-cache-files",
    "if": "${{ steps.provider-cache.outcome == 'success' && "
    "steps.provider-cache.outputs.cache-hit != 'true' }}",
    "shell": "bash",
}

_SAVE = {
    "name": "Save provider cache",
    "if": "${{ steps.provider-cache-files.outputs.populated == 'true' }}",
    "uses": "actions/cache/save",
    "with": {
        "path": "${{ env.TF_PLUGIN_CACHE_DIR }}",
        "key": "${{ steps.provider-cache.outputs.cache-primary-key }}",
    },
}


def _without_ref(step):
    """``step`` with its ``uses:`` cut to the action path: the ref is a pin bumped on its own
    schedule."""
    return {**step, "uses": step["uses"].split("@")[0]}


@pytest.mark.parametrize("cell", _CELLS)
def test_each_cell_computes_the_key_from_its_own_lock_file(cell):
    assert step_by(cell, name="Provider cache key") == _KEY_STEP


@pytest.mark.parametrize("cell", _CELLS)
def test_each_cell_restores_the_cache_keyed_on_its_own_lock_file(cell):
    step = step_by(cell, name="Restore provider cache")
    assert _without_ref(step) == _RESTORE


_SAVERS = ["apply-cell", "drift-cell"]


def _expected(cell, step):
    return {**step, "continue-on-error": True} if cell == "apply-cell" else step


@pytest.mark.parametrize("cell", _SAVERS)
def test_savers_check_the_cache_only_after_a_restore_that_missed(cell):
    step = step_by(cell, name="Check the provider cache")
    assert {k: v for k, v in step.items() if k != "run"} == _expected(cell, _CHECK)
    assert step["run"] == step_by("drift-cell", name="Check the provider cache")["run"]


@pytest.mark.parametrize("cell", _SAVERS)
def test_savers_save_only_a_populated_cache_under_the_restore_key(cell):
    assert _without_ref(step_by(cell, name="Save provider cache")) == _expected(cell, _SAVE)


def _uses_steps(node):
    """Every mapping carrying a `uses:` anywhere in a parsed YAML document."""
    if isinstance(node, dict):
        if isinstance(node.get("uses"), str):
            yield node
        for value in node.values():
            yield from _uses_steps(value)
    elif isinstance(node, list):
        for item in node:
            yield from _uses_steps(item)


def _engine_tree():
    docs = {p.parent.name: action_yaml(p) for p in sorted(ACTIONS.glob("*/action.yml"))}
    docs |= {p.name: workflow_yaml(p) for p in sorted(WORKFLOWS.glob("*.yml"))}
    assert len(docs) > 20, f"expected the whole engine tree, found {len(docs)} files"
    return docs


def test_only_drift_and_apply_save_the_provider_cache():
    """Every `actions/cache/save` in the engine, by file and path, is apply's and drift's provider
    saves and the state save. Mutation: a save step with
    `path: ${{ runner.temp }}/.tofu-plugin-cache` added to `plan-cell`."""
    saves = sorted(
        (name, (step.get("with") or {}).get("path"))
        for name, doc in _engine_tree().items()
        for step in _uses_steps(doc)
        if step["uses"].split("@")[0] == "actions/cache/save"
    )
    assert saves == [
        ("apply-cell", "${{ env.TF_PLUGIN_CACHE_DIR }}"),
        ("drift-cell", "${{ env.TF_PLUGIN_CACHE_DIR }}"),
        ("state", "${{ inputs.path }}"),
    ]


def test_nothing_uses_the_combined_restore_and_save_action():
    # `actions/cache` saves in its post step on every job it restores in, detect jobs included.
    users = {
        name
        for name, doc in _engine_tree().items()
        for step in _uses_steps(doc)
        if step["uses"].split("@")[0] == "actions/cache"
    }
    assert users == set()


def _run_check(tmp_path, populate):
    cache = tmp_path / "cache"
    cache.mkdir()
    populate(cache)
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "TF_PLUGIN_CACHE_DIR": cache.as_posix(),
        "GITHUB_OUTPUT": out.as_posix(),
    }
    body = step_by("drift-cell", name="Check the provider cache")["run"]
    r = run_step(tmp_path, body, env)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    return out.read_text(encoding="utf-8")


def _nested_file(cache):
    leaf = cache / "registry.opentofu.org" / "hashicorp" / "null" / "3.2.4" / "linux_amd64"
    leaf.mkdir(parents=True)
    (leaf / "terraform-provider-null").write_bytes(b"\x7fELF")


@bash_only
@pytest.mark.parametrize(
    "populate",
    [lambda cache: None, lambda cache: (cache / "registry.opentofu.org").mkdir()],
    ids=["empty", "empty-subdirectory"],
)
def test_an_empty_cache_is_not_reported_populated(tmp_path, populate):
    assert _run_check(tmp_path, populate) == ""


@bash_only
def test_a_cache_holding_a_nested_file_is_reported_populated(tmp_path):
    assert _run_check(tmp_path, _nested_file) == "populated=true\n"


_ANNOTATE_IF = (
    "${{ failure() && steps.init.outcome == 'failure' && "
    "steps.provider-cache.outputs.cache-hit == 'true' }}"
)


@pytest.mark.parametrize("cell", _CELLS)
def test_each_cell_names_the_entry_only_when_init_fails_after_a_hit(cell):
    step = step_by(cell, name="Name the restored provider cache entry")
    assert {k: v for k, v in step.items() if k != "run"} == {
        "name": "Name the restored provider cache entry",
        "if": _ANNOTATE_IF,
        "shell": "bash",
        "env": {"KEY": "${{ steps.provider-cache.outputs.cache-primary-key }}"},
    }


@pytest.mark.parametrize("cell", _CELLS)
def test_each_cells_init_step_carries_the_id_the_annotation_reads(cell):
    assert step_by(cell, name="Initialize the stack")["id"] == "init"


@bash_only
@pytest.mark.parametrize("cell", _CELLS)
def test_the_annotation_names_the_key_and_the_delete_command(tmp_path, cell):
    body = step_by(cell, name="Name the restored provider cache entry")["run"]
    r = run_step(tmp_path, body, {**os.environ, "KEY": "tofu-providers-Linux-X64-abc123"})
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        "::error::OpenTofu's init failed after restoring provider cache entry "
        "tofu-providers-Linux-X64-abc123. If the log above says a cached package does not "
        "match the content of the downloaded package, delete the entry with gh cache delete "
        "tofu-providers-Linux-X64-abc123 and re-run.\n"
    )

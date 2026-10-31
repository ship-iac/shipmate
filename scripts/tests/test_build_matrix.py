import json
import pathlib
import subprocess
import sys

import pytest
from _detect_fixtures import answering_run_graph, run_graph_capture, spy_env_config
from _loader import action_yaml, github_outputs, load_script, record_calls
from _shipmate import run_annotated

bm = load_script("build-matrix")

#: What a detect reads when a test names no table. `layout` is required, and `folder`
#: derives no identity variables, so a row carries only the stamp and the tier.
_MINIMAL_TABLE = {"layout": "folder"}


def _no_read():
    """A supplied table must reach validation without a second read of the file."""
    raise AssertionError("env_config read the table although one was supplied")


def test_multi_env_stack_yields_one_cell_per_env():
    cells = bm.full_tree(
        envs=["dev-eu", "dev-us"],
        stacks_by_env={"dev-eu": ["stacks/app"], "dev-us": ["stacks/app", "stacks/dns"]},
        tags_by_stack={
            "stacks/app": ["env/dev-eu", "env/dev-us"],
            "stacks/dns": ["env/dev-us", "workload/net-edge"],
        },
    )
    assert cells == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": ""},
        {"stack": "stacks/app", "environment": "dev-us", "workload": ""},
        {
            "stack": "stacks/dns",
            "environment": "dev-us",
            "workload": "net-edge",
        },
    ]


def test_empty_when_no_changed_stacks():
    assert bm.full_tree(["dev-eu"], {"dev-eu": []}, {}) == []


def test_raises_above_256_cells():
    # The remediation must match CONTRACT.md §Fan-out: splitting the change is the
    # general remedy, and `shipmate apply <env>` is not an escape hatch -- the
    # ceiling trips in plan detect, so no reviewed plan exists to apply.
    stacks = [f"stacks/s{i}" for i in range(257)]
    with pytest.raises(SystemExit) as exc_info:
        bm.cap_cells(
            bm.full_tree(["dev-eu"], {"dev-eu": stacks}, {s: ["env/dev-eu"] for s in stacks}),
            False,
        )
    assert str(exc_info.value) == (
        "::error::257 plan cells exceeds the GitHub Actions matrix limit of 256. "
        "Split the change across several pull requests -- the matrix is built over "
        "`terramate list --changed`. A one-line edit to a shared local module correctly "
        "marks every dependent stack changed and is one atomic change by nature; there the "
        "only lever is to reduce the number of environments in play."
    )


def test_whole_tree_sweep_above_256_cells_names_drift_files(monkeypatch):
    """A drift sweep is split across drift files, not pull requests.

    Reddens when the `all_stacks` branch is dropped, or `compute_cells` stops forwarding
    `all_stacks`, and the changed-set message returns.
    """
    stacks = [f"stacks/s{i}" for i in range(257)]
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: stacks)
    monkeypatch.setattr(bm, "all_tags", lambda: {s: ["env/dev-eu"] for s in stacks})
    with pytest.raises(SystemExit) as exc_info:
        bm.compute_cells(all_stacks=True)
    assert str(exc_info.value) == (
        "::error::257 plan cells exceeds the GitHub Actions matrix limit of 256. "
        "A drift sweep is one matrix; split it across more drift files, each calling "
        "drift.yml with a narrower `tags` query (docs/drift.md)."
    )


def test_the_matrix_limit_counts_the_cells_a_query_keeps(monkeypatch):
    """257 tree cells and a query keeping 10 plan those 10.

    Mutations: cap the tree before the filter in `compute_cells` -- the 257-cell tree refuses;
    return the filtered cells as the tree -- the tree comparison reddens."""
    stacks = [f"stacks/s{i:03}" for i in range(257)]
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: stacks)
    monkeypatch.setattr(
        bm,
        "all_tags",
        lambda: {
            s: ["env/dev-eu", "workload/keep" if s < "stacks/s010" else "workload/drop"]
            for s in stacks
        },
    )
    _, cells, tree, _, _ = bm.compute_cells(all_stacks=True, tags="workload/keep")
    assert [c["stack"] for c in cells] == [f"stacks/s{i:03}" for i in range(10)]
    assert tree == [{"environment": "dev-eu", "stack": f"stacks/s{i:03}"} for i in range(257)]


def test_stack_at_engine_reserved_word_paths_plans():
    """Stacks at paths exactly `apply` and `shipmate` each build a cell row.

    Reddens on re-adding a refusal of either path (SystemExit).
    """
    cells = bm.full_tree(
        ["dev"],
        {"dev": ["apply", "shipmate"]},
        {"apply": ["env/dev"], "shipmate": ["env/dev"]},
    )
    assert cells == [
        {"stack": "apply", "environment": "dev", "workload": ""},
        {"stack": "shipmate", "environment": "dev", "workload": ""},
    ]


_TWO_WORKLOADS_ERROR = (
    "::error::stack 'stacks/dns' carries 2 workload tags (workload/net, workload/network), "
    "and a stack carries at most one `workload/<name>` tag. Keep one in the stack's `tags` "
    "and remove the rest."
)


def test_two_workload_tags_refuse_naming_the_stack_and_every_tag():
    """Reddens on restoring the first-match `return` loop in `workload_of` (returns "net",
    no SystemExit), and on any edit to the message: it is compared whole."""
    with pytest.raises(SystemExit) as exc_info:
        bm.workload_of(["env/dev-eu", "workload/net", "workload/network"], "stacks/dns")
    assert exc_info.value.code == _TWO_WORKLOADS_ERROR


def test_one_or_zero_workload_tags_keep_their_values():
    """Reddens on `workload_of` returning "" unconditionally."""
    assert bm.workload_of(["env/dev-eu", "workload/net"], "stacks/dns") == "net"
    assert bm.workload_of(["env/dev-eu"], "stacks/dns") == ""


def test_full_tree_refuses_a_stack_with_two_workload_tags():
    """Reddens on replacing the `workload_of` call in `full_tree` with an inline first-match
    over the tags, and on dropping the sort of the named tags (they arrive unsorted here)."""
    with pytest.raises(SystemExit) as exc_info:
        bm.full_tree(
            ["dev-eu"],
            {"dev-eu": ["stacks/dns"]},
            {"stacks/dns": ["workload/network", "env/dev-eu", "workload/net"]},
        )
    assert exc_info.value.code == _TWO_WORKLOADS_ERROR


def test_apply_detect_cells_refuse_a_stack_with_two_workload_tags():
    """Reddens on replacing the `workload_of` call in `apply-detect.cells_for_env` with an
    inline first-match over the tags."""
    ad = load_script("apply-detect")
    with pytest.raises(SystemExit) as exc_info:
        ad.cells_for_env(
            "dev-eu",
            ["stacks/dns"],
            {"stacks/dns": ["env/dev-eu", "workload/net", "workload/network"]},
        )
    assert exc_info.value.code == _TWO_WORKLOADS_ERROR


def test_list_stacks_changed_uses_changed_flag(monkeypatch):
    captured = {}
    monkeypatch.setattr(bm, "_run", lambda args: captured.update(args=args) or "stacks/a\n")
    assert bm._list_stacks(all_stacks=False, base="deadbeef") == ["stacks/a"]
    assert captured["args"] == ["terramate", "list", "--changed", "-B", "deadbeef"]


def test_list_stacks_all_omits_changed_flag(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        bm, "_run", lambda args: captured.update(args=args) or "stacks/a\nstacks/b\n"
    )
    assert bm._list_stacks(all_stacks=True, base="") == ["stacks/a", "stacks/b"]
    assert captured["args"] == ["terramate", "list"]


#: Raw `terramate debug show metadata > metadata-<name>.txt` captures (stdout to a file, not a
#: TTY) from terramate 0.17.1 windows_x86_64, run at the root of: `repo-example-stacks`
#: aabb04b, `repo-example-folders` bdea16c, `repo-example-workspaces` df8c25b (`stacks`,
#: `folders`, `workspaces`); a scratch repository of stacks `outer`, `outer/inner`,
#: `with space/app`, `quo"te` (Windows stores the `"` as U+F022, which the header escapes as
#: `\uf022` and `terramate list` prints raw), `empty` (`tags = []`), `notags` (no `tags`),
#: `desc` (`description = "terramate.stack.tags=[\"env/evil\"]"`) and `descnl` (the same
#: description after `x\n\t`) (`edge`); a scratch repository with a stack at its root and one
#: at `child` (`root`). A literal `"` cannot reach a Windows directory name, so the `\"`
#: header escape is decoded by the same `json.loads` but was never captured.
_METADATA = {
    "stacks": {
        "stacks/app": ["env/dev-eu", "env/dev-us"],
        "stacks/auth": ["env/dev-eu"],
        "stacks/dns": ["env/dev-us", "workload/net"],
        "stacks/platform": ["env/dev-eu"],
        "stacks/sandbox/box": ["env/sbx"],
        "stacks/tenant-a": ["env/dev-eu", "env/dev-us"],
        "stacks/tenant-b": ["env/dev-eu", "env/dev-us"],
        "stacks/workers": ["env/dev-eu"],
    },
    "folders": {
        "envs/dev-eu/eu-west-1/app": ["env/dev-eu"],
        "envs/dev-eu/eu-west-1/platform": ["env/dev-eu"],
        "envs/dev-us/us-east-1/dns": ["env/dev-us"],
    },
    "workspaces": {"components/app": ["env/dev-eu", "env/dev-us"]},
    "edge": {
        "desc": ["env/dev-eu"],
        "descnl": [],
        "empty": [],
        "notags": [],
        "outer": ["env/dev-eu"],
        "outer/inner": ["env/dev-us", "workload/app"],
        "quo\uf022te": ["env/dev-eu"],
        "with space/app": ["env/dev-eu"],
    },
    "root": {".": ["env/dev-eu"], "child": ["env/dev-us"]},
}


def _metadata(name):
    path = pathlib.Path(__file__).parent / "fixtures" / f"metadata-{name}.txt"
    return path.read_bytes().decode("utf-8")


@pytest.mark.parametrize("name", list(_METADATA))
def test_parse_metadata_reads_every_stacks_tags_keyed_as_list_prints_it(name):
    """Mutations: match `tags=` anywhere in a line -- `edge`'s `desc` description raises;
    strip the header's quotes instead of decoding them -- `quo\\uf022te` keeps its escape;
    drop `or "."` -- `root` keys its root stack `""`; keep the leading `/` -- every case."""
    assert bm.parse_metadata(_metadata(name)) == _METADATA[name]


def test_a_detect_reads_every_stacks_tags_with_one_terramate_call(monkeypatch):
    """Mutation: read each stack's tags with its own `all_tags` call in `env_membership` --
    eight metadata calls."""
    calls = []

    def run(args):
        calls.append(args)
        if args == ["terramate", "list"]:
            return "".join(f"{s}\n" for s in _METADATA["stacks"])
        return _metadata("stacks")

    monkeypatch.setattr(bm, "_run", run)
    _, tags_by_stack = bm.env_membership(all_stacks=True)
    assert tags_by_stack == _METADATA["stacks"]
    assert calls == [["terramate", "list"], ["terramate", "debug", "show", "metadata"]]


def test_a_listed_stack_the_metadata_lacks_refuses_naming_it(monkeypatch):
    """Mutation: default a missing stack to `[]` in `tags_of` -- `stacks/new` reads as
    unmanaged and detect goes on."""
    monkeypatch.setattr(
        bm, "_list_stacks", lambda all_stacks, base: ["stacks/app", "stacks/new", "stacks/old"]
    )
    monkeypatch.setattr(bm, "all_tags", lambda: {"stacks/app": ["env/dev-eu"]})
    with pytest.raises(SystemExit) as e:
        bm.env_membership(all_stacks=True)
    assert str(e.value) == (
        "::error::terramate debug show metadata printed no tags for 2 stack(s): "
        "stacks/new, stacks/old. Two causes produce this: a working directory other than the "
        "repository root, where `terramate list` prints stack paths relative to it and the "
        "metadata does not; or metadata output this parser does not read, as after a "
        "Terramate version change."
    )


def test_a_metadata_line_that_does_not_decode_refuses_naming_it():
    """A header holding a raw control character is not JSON. Mutation: drop the
    `json.JSONDecodeError` handler in `parse_metadata` -- the decode error escapes."""
    with pytest.raises(SystemExit) as e:
        bm.parse_metadata('stack "/a\x1fb":\n\tterramate.stack.tags=[]\n')
    assert str(e.value) == (
        "::error::terramate debug show metadata printed a line that does not parse as JSON: "
        "'stack \"/a\\x1fb\":'."
    )


def test_tags_of_an_empty_workset_reads_no_metadata(monkeypatch):
    """Mutation: drop `if not stacks: return {}` in `tags_of` -- `_run` is called."""
    monkeypatch.setattr(bm, "_run", lambda args: pytest.fail(f"ran {args}"))
    assert bm.tags_of([]) == {}


def test_compute_cells_fans_out_multi_env(monkeypatch):
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: ["stacks/app"])
    monkeypatch.setattr(
        bm, "all_tags", lambda: {"stacks/app": ["env/dev-eu", "env/dev-us", "workload/app"]}
    )
    _, cells, _, _, _ = bm.compute_cells(all_stacks=True)
    assert cells == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": "app"},
        {"stack": "stacks/app", "environment": "dev-us", "workload": "app"},
    ]


def test_env_membership_groups_stacks_by_env_tag(monkeypatch):
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: ["stacks/app", "stacks/dns"])
    tags = {
        "stacks/app": ["env/dev-eu", "env/dev-us"],
        "stacks/dns": ["env/dev-eu", "workload/dns"],
    }
    monkeypatch.setattr(bm, "all_tags", lambda: tags)
    stacks_by_env, tags_by_stack = bm.env_membership(all_stacks=True)
    assert stacks_by_env == {"dev-eu": ["stacks/app", "stacks/dns"], "dev-us": ["stacks/app"]}
    assert tags_by_stack == tags


_CHARSET = (
    " is not an environment name: "
    "lowercase letters, digits, '-' and '_', with no quotes, spaces or path separators."
)


@pytest.mark.parametrize(
    ("tag", "message"),
    [
        ("env/a.b", "::error::stack 'stacks/c' tag 'env/a.b'" + _CHARSET),
        ("env/../admin", "::error::stack 'stacks/c' tag 'env/../admin'" + _CHARSET),
        (
            "env/prod-apply",
            (
                "::error::stack 'stacks/c' tag 'env/prod-apply' carries the environment suffix "
                "'-apply'; a tag is matched against the bare logical env name; write 'prod' "
                "instead."
            ),
        ),
        (
            "env/shipmate-engine",
            (
                "::error::stack 'stacks/c' tag 'env/shipmate-engine' names 'shipmate-engine', the "
                "engine's own environment, which holds the shipmate App's private key; use another "
                "name."
            ),
        ),
    ],
    ids=["dot", "path", "suffix", "engine"],
)
def test_env_membership_refuses_a_tag_no_environment_can_take(monkeypatch, tag, message):
    """Mutations: drop the `check_env_name` call -- `env/a.b` passes; refuse only on
    `is_env_name` -- `env/prod-apply` passes; delete the `ENGINE_ENV` check from
    `check_env_name`, or change `ENGINE_ENV`'s value -- `env/shipmate-engine` passes."""
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: ["stacks/app", "stacks/c"])
    monkeypatch.setattr(bm, "all_tags", lambda: {"stacks/app": ["env/dev-eu"], "stacks/c": [tag]})
    with pytest.raises(SystemExit) as e:
        bm.env_membership(all_stacks=True)
    assert str(e.value) == message


_UNMANAGED_TREE = {
    "stacks/app": ["env/dev-eu"],
    "stacks/zeta": ["workload/net"],
    "stacks/alpha": [],
}
_TWENTY_UNMANAGED = {"stacks/app": ["env/dev-eu"], **{f"stacks/u{i:02}": [] for i in range(20)}}


def test_env_membership_skips_untagged_stacks_with_a_whole_tree_notice(monkeypatch, capsys):
    """Two stacks with no `env/*` tag are in the tag map, in no environment, and named in one
    sorted notice. Mutation: restore the `SystemExit` refusal."""
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(_UNMANAGED_TREE))
    monkeypatch.setattr(bm, "all_tags", lambda: _UNMANAGED_TREE)
    stacks_by_env, tags_by_stack = bm.env_membership(all_stacks=True)
    assert stacks_by_env == {"dev-eu": ["stacks/app"]}
    assert tags_by_stack == _UNMANAGED_TREE
    assert capsys.readouterr().out == (
        "::notice::2 stack(s) carry neither an env/* tag nor shipmate/local "
        "and are not managed by shipmate: "
        "stacks/alpha, stacks/zeta\n"
    )


def test_the_unmanaged_notice_keeps_a_percent_in_a_stack_path(monkeypatch, capsys):
    """A stack path can hold `%`: escaped as workflow-command data, the notice shows it verbatim.
    Mutation: drop `command_data` from the unmanaged notice -- red."""
    tree = {"stacks/app": ["env/dev-eu"], "stacks/c100%": []}
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(tree))
    monkeypatch.setattr(bm, "all_tags", lambda: tree)
    bm.env_membership(all_stacks=True)
    assert capsys.readouterr().out == (
        "::notice::1 stack(s) carry neither an env/* tag nor shipmate/local "
        "and are not managed by shipmate: stacks/c100%25\n"
    )


def test_the_changed_set_scan_names_its_scope_in_the_notice(monkeypatch, capsys):
    """Mutation: print the whole-tree wording on both scans."""
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(_UNMANAGED_TREE))
    monkeypatch.setattr(bm, "all_tags", lambda: _UNMANAGED_TREE)
    bm.env_membership(all_stacks=False, base="deadbeef")
    assert capsys.readouterr().out == (
        "::notice::2 changed stack(s) carry neither an env/* tag nor shipmate/local "
        "and are not managed by shipmate: "
        "stacks/alpha, stacks/zeta\n"
    )


def test_the_notice_names_ten_paths_and_counts_the_rest(monkeypatch, capsys):
    """Mutation: name every path -- the notice lists all twenty."""
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(_TWENTY_UNMANAGED))
    monkeypatch.setattr(bm, "all_tags", lambda: _TWENTY_UNMANAGED)
    bm.env_membership(all_stacks=True)
    assert capsys.readouterr().out == (
        "::notice::20 stack(s) carry neither an env/* tag nor shipmate/local "
        "and are not managed by shipmate: "
        + ", ".join(f"stacks/u{i:02}" for i in range(10))
        + ", and 10 more\n"
    )


def test_no_unmanaged_stack_prints_nothing(monkeypatch, capsys):
    """Mutation: print the notice with N = 0."""
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: ["stacks/app"])
    monkeypatch.setattr(bm, "all_tags", lambda: {"stacks/app": ["env/dev-eu"]})
    bm.env_membership(all_stacks=True)
    assert capsys.readouterr().out == ""


def test_compute_cells_refuses_a_changed_stacks_env_tag_no_environment_can_take(monkeypatch):
    """Mutation: `compute_cells` passes `check_names=False` to `env_membership`."""
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: ["stacks/c"])
    monkeypatch.setattr(bm, "all_tags", lambda: {"stacks/c": ["env/a.b"]})
    with pytest.raises(SystemExit) as e:
        bm.compute_cells(all_stacks=False, base="deadbeef")
    assert str(e.value) == "::error::stack 'stacks/c' tag 'env/a.b'" + _CHARSET


def test_compute_cells_leaves_an_unmanaged_stack_out_of_cells_and_tree(monkeypatch):
    """The fourth element lists it, and no cell or tree entry names it, so drift closes its
    Issues. Mutation: add unmanaged stacks to `tree`."""
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(_UNMANAGED_TREE))
    monkeypatch.setattr(bm, "all_tags", lambda: _UNMANAGED_TREE)
    _, cells, tree, unmanaged, _ = bm.compute_cells(all_stacks=True)
    assert cells == [{"stack": "stacks/app", "environment": "dev-eu", "workload": ""}]
    assert tree == [{"environment": "dev-eu", "stack": "stacks/app"}]
    assert unmanaged == ["stacks/alpha", "stacks/zeta"]


def test_two_workload_tags_on_an_unmanaged_stack_draw_no_refusal(monkeypatch):
    """Mutation: call `workload_of` over every stack in `tags_by_stack` in `full_tree`."""
    tree = {"stacks/app": ["env/dev-eu"], "stacks/odd": ["workload/a", "workload/b"]}
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(tree))
    monkeypatch.setattr(bm, "all_tags", lambda: tree)
    _, cells, _, unmanaged, _ = bm.compute_cells(all_stacks=True)
    assert cells == [{"stack": "stacks/app", "environment": "dev-eu", "workload": ""}]
    assert unmanaged == ["stacks/odd"]


_LOCAL_TREE = {
    "a": ["shipmate/local"],
    "b": [],
    "c": ["shipmate/local", "env/dev"],
    "d": ["Shipmate/Local"],
}


def test_local_stacks_are_the_exact_tag_without_an_env_tag():
    """Mutations: compare the tag case-insensitively -- `d` appears; drop the `env/*` exclusion
    -- `c` appears; change `LOCAL_TAG`'s value -- red."""
    assert bm.LOCAL_TAG == "shipmate/local"
    assert bm.local_stacks({**_LOCAL_TREE, "e": ["shipmate/local "]}) == ["a"]


def test_the_unmanaged_notice_never_names_a_local_stack(monkeypatch, capsys):
    """Mutation: count and name `unmanaged_stacks` unfiltered in the notice -- `a` appears."""
    tree = {k: v for k, v in _LOCAL_TREE.items() if k != "c"}
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(tree))
    monkeypatch.setattr(bm, "all_tags", lambda: tree)
    bm.env_membership(all_stacks=True)
    assert capsys.readouterr().out == (
        "::notice::2 stack(s) carry neither an env/* tag nor shipmate/local "
        "and are not managed by shipmate: b, d\n"
    )


def test_a_changed_set_of_only_local_stacks_prints_no_notice(monkeypatch, capsys):
    """Mutation: count and name `unmanaged_stacks` unfiltered in the notice -- `a` is
    named."""
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: ["a"])
    monkeypatch.setattr(bm, "all_tags", lambda: {"a": ["shipmate/local"]})
    bm.env_membership(all_stacks=False, base="deadbeef")
    assert capsys.readouterr().out == ""


_LOCAL_AND_ENV = (
    "::error::stack {!r} carries 'shipmate/local' and {}: a local stack carries no env/* tag. "
    "Remove 'shipmate/local' for shipmate to run the stack, or its env/* tags to keep it out "
    "of CI."
)


def test_a_local_stack_with_an_env_tag_refuses(monkeypatch):
    """Mutation: let `env/*` win over `shipmate/local` silently (drop the raise) -- no
    refusal."""
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(_LOCAL_TREE))
    monkeypatch.setattr(bm, "all_tags", lambda: _LOCAL_TREE)
    with pytest.raises(SystemExit) as e:
        bm.env_membership(all_stacks=False, base="deadbeef")
    assert str(e.value) == _LOCAL_AND_ENV.format("c", "'env/dev'")


def test_every_local_stack_with_an_env_tag_is_named_in_one_refusal(monkeypatch):
    """Mutation: raise on the first offender -- `stacks/y` is missing."""
    tree = {
        "stacks/x": ["shipmate/local", "env/dev"],
        "stacks/ok": ["env/dev"],
        "stacks/y": ["env/dev", "workload/w", "env/prod", "shipmate/local"],
    }
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(tree))
    monkeypatch.setattr(bm, "all_tags", lambda: tree)
    with pytest.raises(SystemExit) as e:
        bm.env_membership(all_stacks=True)
    assert str(e.value) == "\n".join(
        [
            _LOCAL_AND_ENV.format("stacks/x", "'env/dev'"),
            _LOCAL_AND_ENV.format("stacks/y", "'env/dev', 'env/prod'"),
        ]
    )


def test_the_local_and_env_refusal_follows_check_names(monkeypatch):
    """Mutation: refuse a local stack's `env/*` tag regardless of `check_names` -- red."""
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(_LOCAL_TREE))
    monkeypatch.setattr(bm, "all_tags", lambda: _LOCAL_TREE)
    stacks_by_env, _ = bm.env_membership(all_stacks=True, check_names=False)
    assert stacks_by_env == {"dev": ["c"]}


def test_compute_cells_keeps_local_stacks_in_the_scan_set(monkeypatch):
    """The fourth element is the scan set `deploy-detect` refuses stranded checks over; the
    fifth is `reported_unmanaged`. Mutations: drop local stacks from `unmanaged_stacks` itself
    -- `a` leaves the fourth element; return `local_stacks` as the fifth -- red."""
    tree = {k: v for k, v in _LOCAL_TREE.items() if k != "c"}
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(tree))
    monkeypatch.setattr(bm, "all_tags", lambda: tree)
    _, _, _, scanned, reported = bm.compute_cells(all_stacks=True)
    assert scanned == ["a", "b", "d"]
    assert reported == ["b", "d"]


def test_two_workload_tags_on_a_local_stack_draw_no_refusal(monkeypatch):
    """Mutation: call `workload_of` over every stack in `tags_by_stack` in `full_tree`."""
    tree = {
        "stacks/app": ["env/dev-eu"],
        "stacks/lab": ["shipmate/local", "workload/a", "workload/b"],
    }
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(tree))
    monkeypatch.setattr(bm, "all_tags", lambda: tree)
    _, cells, _, scanned, _ = bm.compute_cells(all_stacks=True)
    assert cells == [{"stack": "stacks/app", "environment": "dev-eu", "workload": ""}]
    assert scanned == ["stacks/lab"]


def test_a_stated_head_repository_equal_to_this_repository_is_planned():
    assert bm.fork_pr_error("acme/iac", "acme/iac", False) == ""


def test_a_stated_foreign_head_repository_is_refused_naming_both():
    err = bm.fork_pr_error("acme/iac", "outsider/iac", False)
    assert err.startswith("::error::")
    assert "outsider/iac" in err
    assert "acme/iac" in err


def test_an_unstated_head_repository_is_refused_naming_the_input():
    # Pinning the message, not the refusal: letting an empty value fall through to the
    # equality check refuses too, with the fork wording, which tells a consumer who forgot
    # the input to push their branch to where it already is.
    assert bm.fork_pr_error("acme/iac", "   ", False) == _UNSTATED_HEAD_REPO


_UNSTATED_HEAD_REPO = (
    "::error::this run did not state its head repository. The `head-repo` input of "
    "shipmate's `build-matrix` action must be the pull request's head repository full "
    "name (`owner/repo`), so this run can be checked against a fork head. See "
    "`docs/getting-started.md` for the wiring. A workflow with no pull request at all "
    "(a drift sweep) passes `sweep: true` instead."
)


#: Values that are not the bool True, so every refusal helper must still refuse on them.
_NOT_OPTED_OUT = [
    pytest.param(False, id="False"),
    pytest.param("false", id="str-false"),
    pytest.param("true", id="str-true"),
    pytest.param(1, id="one"),
    pytest.param("", id="empty"),
    pytest.param(None, id="None"),
]


@pytest.mark.parametrize("value", _NOT_OPTED_OUT)
def test_only_the_bool_true_skips_the_three_refusals(monkeypatch, value):
    """main() parses the input to a bool, so a raw string here is a caller that forgot to parse.
    A truthy check would skip the fork refusal for "false".

    Mutation: `if sweep is True:` -> `if sweep:` in any of the three
    helpers -- its str-false, str-true and one rows redden."""
    monkeypatch.setattr(bm, "_run", lambda args: "basebase\n")
    assert bm.fork_pr_error("acme/iac", "outsider/iac", value).startswith("::error::")
    assert bm.head_checkout_error("cafe1234", value).startswith("::error::")
    assert bm.tag_filter_error("env/dev-eu", value).startswith("::error::")


def test_the_opt_out_plans_whatever_the_head_repository():
    for head in ("", "   ", "acme/iac", "outsider/iac"):
        assert bm.fork_pr_error("acme/iac", head, True) == ""


def test_an_unknown_this_repository_refuses_a_stated_head_repository():
    # GITHUB_REPOSITORY unset is not reachable on a runner; if it ever is, the
    # empty string must not compare equal to whatever was stated.
    assert "fork pull requests are not supported" in bm.fork_pr_error("", "acme/iac", False)


def _fake_run(head_sha, diffs, diff_calls, dot=None):
    """`_run` answering `git rev-parse HEAD` with `head_sha` and each `git diff` with, per
    `diffs` stack, a `diff --git` header for its `stack.tm.hcl` and its `diffs` text, recording
    the diff argv. `dot`, when given, answers the run-graph argv."""

    def fake_run(args):
        if args[:2] != ["git", "diff"]:
            return f"{head_sha}\n"
        diff_calls.append(args)
        return "".join(
            f"diff --git a/{s}/stack.tm.hcl b/{s}/stack.tm.hcl\n{text}" for s, text in diffs.items()
        )

    return fake_run if dot is None else answering_run_graph(fake_run, dot)


def _answer_stack_list(monkeypatch, stacks, list_calls):
    """The changed set answered with `stacks`' paths: by a `_list_stacks` double, or, given
    `list_calls`, by `_run` answering `terramate list` and appending its argv there while every
    other argv goes to the `_run` already in place."""
    if list_calls is None:
        monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(stacks))
        return
    inner = bm._run

    def list_run(args):
        if args[:2] != ["terramate", "list"]:
            return inner(args)
        list_calls.append(args)
        return "\n".join(stacks)

    monkeypatch.setattr(bm, "_run", list_run)


def _run_main(
    monkeypatch,
    tmp_path,
    env,
    cells=(("stacks/app", "dev-eu"),),
    called=None,
    plan_workflow=True,
    head_sha=None,
    table=None,
    stacks=None,
    tree=None,
    read_table=None,
    unmanaged=(),
    diffs=None,
    diff_calls=None,
    tag_map=None,
    graph=None,
    tag_reads=None,
    dot=None,
    merge_base=None,
    list_calls=None,
):
    """main() with GITHUB_OUTPUT redirected, returning (parsed outputs, calls) where calls
    records compute_cells' arguments, so a rejection is observable as the stack enumeration
    never having run. Pass `called` to keep that record readable when main() raises.

    The double returns `tree` as the scanned tree's cell names, or the names of `cells` when
    it is None. `stacks`, a `{stack: [tags]}` map, runs the real `compute_cells` over that
    tree instead of the double, and leaves `called`, `cells` and `tree` unused. `read_table`
    replaces the default-branch read, which otherwise answers `table`.

    The double returns `unmanaged` as both the scan's unmanaged stacks and its
    `reported_unmanaged`. `diffs`, `{stack: diff
    text}`, answers the `git diff` naming that stack's `.tm.hcl` pathspec and appends its argv
    to `diff_calls`; it needs `head_sha`.

    `head_sha` states that commit AND makes `git rev-parse HEAD` answer it, which is what a
    run past the head-checkout refusal looks like; without it the run states no head and is
    refused, so every test that wants to reach the matrix passes one.

    The inversion check's two reads are stubbed: `all_tags` answers `tag_map` (or `stacks`),
    and `run_graph_deps` is `graph`, by default an empty run-graph, which replaces both the
    run-graph read and its cycle sort. `dot`, run-graph text, keeps `run_graph_deps` real
    instead -- the read and the sort -- and answers its Terramate call; it needs `head_sha`.
    Each `all_tags` call appends the map it answers to `tag_reads` when one is given.

    `merge_base` replaces the merge-base resolution, by default the identity, so a stated
    base reaches `compute_cells` and the `git diff` unchanged. `list_calls`, with `stacks`,
    keeps `_list_stacks` real: its `terramate list` argv is appended there and answered with
    `stacks`' paths."""
    out = tmp_path / "out.txt"
    out.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    # main() reads the checkout it runs in: the consumer's workflow file has to be
    # where plan_workflow_error requires it, and the engine repo (pytest's cwd) has
    # no shipmate.yml of its own.
    (tmp_path / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    if plan_workflow:
        (tmp_path / ".github" / "workflows" / "shipmate.yml").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for k in (
        "GITHUB_EVENT_NAME",
        "GITHUB_REPOSITORY",
        "GITHUB_EVENT_PATH",
        "SHIPMATE_HEAD_REPO",
        "SHIPMATE_HEAD_SHA",
        "SHIPMATE_SWEEP",
        "SHIPMATE_TAGS",
        "SHIPMATE_BASE_SHA",
    ):
        monkeypatch.delenv(k, raising=False)
    if head_sha is not None:
        monkeypatch.setenv("SHIPMATE_HEAD_SHA", head_sha)
        monkeypatch.setattr(
            bm,
            "_run",
            _fake_run(head_sha, diffs or {}, [] if diff_calls is None else diff_calls, dot),
        )
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    called = [] if called is None else called

    def fake_compute(all_stacks=False, base="", tags="", errors=None, tag_map=None):
        called.append((all_stacks, base, tags))
        # The whole row `full_tree` emits, `workload` included: a double that omits a
        # key the real builder always adds cannot fail on a guard that pins the row shape.
        rows = [{"stack": s, "environment": e, "workload": ""} for s, e in cells]
        names = [{"environment": e, "stack": s} for s, e in cells] if tree is None else tree
        # The real `compute_cells` returns the env->workloads map beside the rows, and `main`
        # forwards it as `tagged` only under `all_stacks`. The rows tag no workload.
        return {e: frozenset() for _, e in cells}, rows, names, list(unmanaged), list(unmanaged)

    answer = dict(tag_map or {}) if stacks is None else stacks
    reads = [] if tag_reads is None else tag_reads

    def all_tags():
        reads.append(answer)
        return answer

    monkeypatch.setattr(bm, "all_tags", all_tags)
    if stacks is None:
        monkeypatch.setattr(bm, "compute_cells", fake_compute)
    else:
        _answer_stack_list(monkeypatch, stacks, list_calls)
    fallback = bm.run_graph_deps if dot is not None else lambda: {}
    monkeypatch.setattr(bm, "run_graph_deps", graph or fallback)
    monkeypatch.setattr(bm.ec, "read_table", read_table or (lambda: dict(table or _MINIMAL_TABLE)))
    monkeypatch.setattr(bm, "merge_base", merge_base or (lambda base: base))
    bm.main()
    parsed = github_outputs(out)
    return parsed, called


def test_main_fails_the_step_for_a_fork_and_does_not_enumerate(monkeypatch, tmp_path):
    # SystemExit, not an empty matrix: no gate is ever written for a fork head,
    # so a green "nothing to plan" would leave the contributor waiting on a
    # required check that structurally cannot arrive.
    called = []
    with pytest.raises(SystemExit) as excinfo:
        _run_main(
            monkeypatch,
            tmp_path,
            {
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_REPOSITORY": "acme/iac",
                "SHIPMATE_HEAD_REPO": "outsider/iac",
            },
            called=called,
        )
    assert "fork pull requests are not supported" in str(excinfo.value)
    assert called == []


def test_main_passes_the_three_environment_values_to_the_guard(monkeypatch, tmp_path):
    # Three distinct values, so a reordered call site is red rather than merely
    # differently spelled.
    seen = []
    monkeypatch.setattr(bm, "fork_pr_error", lambda *args: seen.append(args) or "")
    _run_main(
        monkeypatch,
        tmp_path,
        {
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REPOSITORY": "acme/iac",
            "SHIPMATE_HEAD_REPO": "outsider/iac",
            "SHIPMATE_SWEEP": "true",
        },
    )
    assert seen == [("acme/iac", "outsider/iac", True)]


def test_main_refuses_the_fork_before_the_consumer_wiring_checks(monkeypatch, tmp_path):
    # Both guards would fire on this run -- a fork head, and a checkout that is not the
    # stated one -- so the assertion pins the order: a run this script cannot vouch for is
    # turned away before it shells out to git on the fork's tree, so `_run` fails.
    called = []
    monkeypatch.setattr(bm, "_run", lambda args: pytest.fail("git ran on a fork's tree"))
    with pytest.raises(SystemExit) as excinfo:
        _run_main(
            monkeypatch,
            tmp_path,
            {
                "GITHUB_EVENT_NAME": "pull_request_target",
                "GITHUB_REPOSITORY": "acme/iac",
                "SHIPMATE_HEAD_REPO": "outsider/iac",
                "SHIPMATE_HEAD_SHA": "cafe1234",
            },
            called=called,
        )
    assert "fork pull requests are not supported" in str(excinfo.value)
    assert called == []


def test_main_refuses_a_pull_request_when_the_repository_is_unknown(monkeypatch, tmp_path):
    # `if repository and head_repo == repository` fails closed on purpose: with
    # GITHUB_REPOSITORY empty, dropping that clause makes every stated head
    # repository compare equal to the empty string and the run gets planned.
    called = []
    with pytest.raises(SystemExit) as excinfo:
        _run_main(
            monkeypatch,
            tmp_path,
            {
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_REPOSITORY": "",
                "SHIPMATE_HEAD_REPO": "acme/iac",
            },
            called=called,
        )
    assert "fork pull requests are not supported" in str(excinfo.value)
    assert called == []


def test_main_does_not_enumerate_stacks_for_a_fork(monkeypatch, tmp_path):
    # The rejection must precede the terramate calls, not merely discard them.
    def boom(*a, **k):
        pytest.fail("compute_cells ran for a fork pull request")

    monkeypatch.setattr(bm, "compute_cells", boom)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/iac")
    monkeypatch.setenv("SHIPMATE_HEAD_REPO", "outsider/iac")
    monkeypatch.delenv("SHIPMATE_SWEEP", raising=False)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out.txt"))
    # The assertion names the rejection so this guard cannot pass on somebody
    # else's SystemExit.
    with pytest.raises(SystemExit) as excinfo:
        bm.main()
    assert "fork pull requests are not supported" in str(excinfo.value)


def test_main_plans_a_same_repository_pull_request(monkeypatch, tmp_path):
    outputs, called = _run_main(
        monkeypatch,
        tmp_path,
        {
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REPOSITORY": "acme/iac",
            "SHIPMATE_HEAD_REPO": "acme/iac",
        },
        head_sha="cafe1234",
    )
    assert outputs["empty"] == "false"
    assert called == [(False, "", "")]


def test_main_drift_run_is_unaffected(monkeypatch, tmp_path):
    # A sweep has no pull request context: every stack must still be enumerated
    # off `sweep` alone, with no head repository stated.
    outputs, called = _run_main(
        monkeypatch,
        tmp_path,
        {
            "SHIPMATE_SWEEP": "true",
            "GITHUB_EVENT_NAME": "schedule",
            "GITHUB_REPOSITORY": "acme/iac",
        },
    )
    assert outputs["empty"] == "false"
    assert called == [(True, "", "")]


#: `dev-us` needs `dev-eu`, while `stacks/platform` (dev-eu only) runs after `stacks/dns`
#: (dev-us only): no order applies both.
_INVERTED_TABLE = {
    "layout": "folder",
    "environments": {"dev-eu": {}, "dev-us": {"needs": ["dev-eu"]}},
}
_INVERTED_TAGS = {"stacks/dns": ["env/dev-us"], "stacks/platform": ["env/dev-eu"]}
_INVERTED_DEPS = {"stacks/dns": set(), "stacks/platform": {"stacks/dns"}}
_PR_ENV = {
    "GITHUB_EVENT_NAME": "pull_request_target",
    "GITHUB_REPOSITORY": "acme/iac",
    "SHIPMATE_HEAD_REPO": "acme/iac",
}


def _run_inverted(monkeypatch, tmp_path, env, cells, graph=None, head_sha="cafe1234"):
    return _run_main(
        monkeypatch,
        tmp_path,
        env,
        cells=cells,
        head_sha=head_sha,
        table=_INVERTED_TABLE,
        tag_map=_INVERTED_TAGS,
        graph=graph or (lambda: _INVERTED_DEPS),
    )


def _annotated(monkeypatch):
    """`bm.main` as the script's entry runs it: through `run_annotated` with the `refusal`
    output, so a refusal exits 1 and prints its annotation to stderr."""
    main = bm.main
    monkeypatch.setattr(bm, "main", lambda: run_annotated(main, refusal_output=True))


def test_a_plan_refuses_changed_cells_whose_after_edge_needs_inverts(monkeypatch, tmp_path, capsys):
    """Both ends of the inverted edge changed: the plan refuses, and the gate description's
    `refusal` output names the pair and the short remedy. Mutation: drop the inversion check
    from `main`. Mutation: put the pair before the remedy on the first line -- red."""
    _annotated(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _run_inverted(
            monkeypatch,
            tmp_path,
            _PR_ENV,
            (("stacks/platform", "dev-eu"), ("stacks/dns", "dev-us")),
        )
    assert exc.value.code == 1
    assert capsys.readouterr().err == (
        "::error::needs order inverted, fix default-branch needs or tags: stacks/platform / "
        "dev-eu before stacks/dns / dev-us, which it runs after%0A"
        "- stacks/platform / dev-eu (env-level 0) after stacks/dns / dev-us (env-level 1); "
        "dev-us's level comes from dev-eu -> dev-us%0A"
        "Env-levels apply in order, so `after` cannot be met. Drop or reverse a `needs` entry "
        "in that chain on the default branch, or retag the stacks so the edge stays inside "
        "environments that order it.\n"
    )
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == (
        "refusal=needs order inverted, fix default-branch needs or tags: stacks/platform / "
        "dev-eu before stacks/dns / dev-us, which it runs after "
        "- stacks/platform / dev-eu (env-level 0) after stacks/dns / dev-us (env-level 1); "
        "dev-us's level comes from dev-eu -> dev-us "
        "Env-levels apply in order, so `after` cannot be met. Drop or reverse a `needs` entry "
        "in that chain on the default branch, or retag the stacks so the edge stays inside "
        "environments that order it.\n"
    )


def test_a_plan_passes_when_only_one_end_of_the_inverted_edge_changed(monkeypatch, tmp_path):
    """The unchanged `stacks/dns / dev-us` is in no run, so nothing applies out of order.
    Mutation: pass every `(stack, env)` of the tag map as `present`."""
    outputs, _ = _run_inverted(monkeypatch, tmp_path, _PR_ENV, (("stacks/platform", "dev-eu"),))
    assert outputs["matrix"] == json.dumps(
        {
            "include": [
                {
                    "stack": "stacks/platform",
                    "environment": "dev-eu",
                    "workload": "",
                    "role_arn": "",
                    "cred_region": "",
                    "tf_vars": {},
                    "config_path": "plan",
                    "env_binding": "dev-eu-plan",
                }
            ]
        }
    )


def test_a_drift_sweep_does_not_refuse_an_inverted_edge(monkeypatch, tmp_path):
    """A sweep plans every cell and applies none. Mutation: drop `not sweep` from the check."""
    outputs, _ = _run_inverted(
        monkeypatch,
        tmp_path,
        {
            "SHIPMATE_SWEEP": "true",
            "GITHUB_EVENT_NAME": "schedule",
            "GITHUB_REPOSITORY": "acme/iac",
        },
        (("stacks/platform", "dev-eu"), ("stacks/dns", "dev-us")),
        head_sha=None,
    )
    assert outputs["count"] == "2"


def test_a_plan_reads_the_tag_metadata_once(monkeypatch, tmp_path):
    """The real `compute_cells` scans the tree, and the plan reads no second tag map.

    Mutation: drop the `tag_map` pass-through from `compute_cells` to `env_membership` --
    two reads."""
    tags = {"stacks/app": ["env/dev-eu"]}
    reads = []
    _run_main(monkeypatch, tmp_path, _PR_ENV, head_sha="cafe1234", stacks=tags, tag_reads=reads)
    assert reads == [{"stacks/app": ["env/dev-eu"]}]


_CYCLE = (
    "::error::stack ordering cycle through {}: a stack ordered after itself, directly or through "
    "other stacks, has no first wave. Remove one `after` or `before` entry on that loop in the "
    "stack configuration."
)


def _assert_cycle_refusal(exc, capsys, tmp_path, stacks):
    """`stacks` holds no `%`, CR or LF, so the annotation is the message unescaped."""
    message = _CYCLE.format(stacks)
    assert exc.value.code == 1
    assert capsys.readouterr().err == f"{message}\n"
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == (
        f"refusal={message.removeprefix('::error::')}\n"
    )


@pytest.mark.parametrize(
    ("shape", "stacks"),
    [
        ("cycle-self", "a"),
        ("cycle-two", "a, b"),
        ("cycle-three", "a, b, c"),
        ("cycle-offcycle", "a, b"),
    ],
)
def test_run_graph_deps_refuses_a_cycle(monkeypatch, shape, stacks):
    """The fixtures are the raw Terramate 0.17.1 captures `test_waves.py` documents
    (`terramate experimental run-graph --label stack.dir` to a file, not a TTY). `offcycle`
    refuses although `c` is on no loop: the read covers the whole tree. The apply and deploy
    detect tests pin that those detects reach this function.

    Mutation: drop the `stack_levels` call from `run_graph_deps` (every shape returns)."""
    monkeypatch.setattr(bm, "_run", answering_run_graph(lambda args: "", run_graph_capture(shape)))
    with pytest.raises(SystemExit) as exc:
        bm.run_graph_deps()
    assert exc.value.code == _CYCLE.format(stacks)


def test_run_graph_deps_returns_the_acyclic_graph(monkeypatch):
    """`run-graph-stacks.dot` is the stacks flavor's graph. Mutation: `return
    wv.stack_levels(deps)` from `run_graph_deps` (the levels list comes back)."""
    monkeypatch.setattr(
        bm, "_run", answering_run_graph(lambda args: "", run_graph_capture("stacks"))
    )
    assert bm.run_graph_deps() == {
        "stacks/app": {"stacks/auth", "stacks/workers"},
        "stacks/auth": {"stacks/platform"},
        "stacks/platform": {"stacks/dns"},
        "stacks/workers": {"stacks/platform"},
        "stacks/tenant-a": {"stacks/app"},
        "stacks/tenant-b": {"stacks/app"},
        "stacks/dns": set(),
        "stacks/sandbox/box": set(),
    }


_ONE_ENV = {"layout": "folder", "environments": {"dev-eu": {}}}


@pytest.mark.parametrize(
    ("shape", "stacks"), [("cycle-self", ("a",)), ("cycle-three", ("a", "b", "c"))]
)
def test_a_plan_without_needs_refuses_a_cycle_through_its_cells(
    monkeypatch, tmp_path, capsys, shape, stacks
):
    """The table orders no environment, and the plan still reads and sorts the run-graph.
    Fixtures as `test_run_graph_deps_refuses_a_cycle`; `_run` answers the run-graph argv, so
    `run_graph_deps` is real.

    Mutation: re-gate the read behind `any(order.values())` in `main` (both red)."""
    _annotated(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch,
            tmp_path,
            _PR_ENV,
            cells=tuple((s, "dev-eu") for s in stacks),
            head_sha="cafe1234",
            table=_ONE_ENV,
            tag_map={s: ["env/dev-eu"] for s in stacks},
            dot=run_graph_capture(shape),
        )
    _assert_cycle_refusal(exc, capsys, tmp_path, ", ".join(stacks))


def test_a_plan_with_no_cell_refuses_a_cycle(monkeypatch, tmp_path, capsys):
    """A pull request can close a loop and plan nothing, for example with an `after` on a stack
    that has no `env/*` tag. Fixture as `test_run_graph_deps_refuses_a_cycle`.

    Mutation: add `cells and` to the read's guard in `main`."""
    _annotated(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch,
            tmp_path,
            _PR_ENV,
            cells=(),
            head_sha="cafe1234",
            table=_ONE_ENV,
            tag_map={},
            dot=run_graph_capture("cycle-two"),
        )
    _assert_cycle_refusal(exc, capsys, tmp_path, "a, b")


_DNS_PLATFORM_LOOP = (
    "digraph  {\n\t\n"
    '\tn1[label="/stacks/dns"];\n'
    '\tn2[label="/stacks/platform"];\n'
    '\tn1->n2[color="red"];\n'
    '\tn2->n1[color="red"];\n'
    "\t\n}\n"
)


def test_a_cycle_refuses_before_an_inversion(monkeypatch, tmp_path, capsys):
    """`stacks/platform` and `stacks/dns` are each `after` the other, and dev-us needs dev-eu,
    so the pair also inverts. The DOT is hand-built for these two stack names in the shape of
    the raw 0.17.1 captures (tab indent, red cycle edges).

    Mutation: drop the `stack_levels` call from `run_graph_deps` (the inversion message
    appears)."""
    _annotated(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch,
            tmp_path,
            _PR_ENV,
            cells=(("stacks/platform", "dev-eu"), ("stacks/dns", "dev-us")),
            head_sha="cafe1234",
            table=_INVERTED_TABLE,
            tag_map=_INVERTED_TAGS,
            dot=_DNS_PLATFORM_LOOP,
        )
    _assert_cycle_refusal(exc, capsys, tmp_path, "stacks/dns, stacks/platform")


def test_the_refusal_output_keeps_a_percent_sequence_in_a_stack_path(monkeypatch, tmp_path, capsys):
    """The gate description is JSON, never workflow-command data, so `refusal` carries a literal
    `%0A` or `%25` in a stack path verbatim, while the annotation escapes it. The DOT is
    hand-built in the shape of the raw 0.17.1 captures: none of them holds such a name.

    Mutation: write `refusal=` through `command_data` in `run_annotated` -- red."""
    dot = (
        "digraph  {\n\t\n"
        '\tn1[label="/stacks/a%0Ab"];\n'
        '\tn2[label="/stacks/c100%25"];\n'
        '\tn1->n2[color="red"];\n'
        '\tn2->n1[color="red"];\n'
        "\t\n}\n"
    )
    _annotated(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch,
            tmp_path,
            _PR_ENV,
            cells=(),
            head_sha="cafe1234",
            table=_ONE_ENV,
            tag_map={},
            dot=dot,
        )
    assert exc.value.code == 1
    assert capsys.readouterr().err == (
        "::error::stack ordering cycle through stacks/a%250Ab, stacks/c100%2525: a stack "
        "ordered after itself, directly or through other stacks, has no first wave. Remove one "
        "`after` or `before` entry on that loop in the stack configuration.\n"
    )
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == (
        "refusal=stack ordering cycle through stacks/a%0Ab, stacks/c100%25: a stack ordered "
        "after itself, directly or through other stacks, has no first wave. Remove one `after` "
        "or `before` entry on that loop in the stack configuration.\n"
    )


def test_a_drift_sweep_reads_no_run_graph(monkeypatch, tmp_path):
    """A sweep applies nothing, and refusing a cycle there would blank drift for every stack off
    the loop. Mutation: drop `not sweep` from the read's guard in `main`; it also reddens
    `test_a_drift_sweep_does_not_refuse_an_inverted_edge`, which proves no inversion refuses a
    sweep, while this one proves a sweep never reads the run-graph at all."""

    def boom():
        raise AssertionError("a drift sweep read the run-graph")

    outputs, _ = _run_main(
        monkeypatch,
        tmp_path,
        {
            "SHIPMATE_SWEEP": "true",
            "GITHUB_EVENT_NAME": "schedule",
            "GITHUB_REPOSITORY": "acme/iac",
        },
        cells=(("stacks/platform", "dev-eu"), ("stacks/dns", "dev-us")),
        table=_INVERTED_TABLE,
        tag_map=_INVERTED_TAGS,
        graph=boom,
    )
    assert outputs["count"] == "2"


def test_a_stated_head_equal_to_the_checkout_is_planned(monkeypatch):
    monkeypatch.setattr(bm, "_run", lambda args: "cafe1234\n")
    assert bm.head_checkout_error("cafe1234", False) == ""


def test_a_checkout_that_is_not_the_stated_head_is_refused_naming_both(monkeypatch):
    """What both plan triggers leave checked out by default: the base branch under
    `pull_request_target`, the dispatch ref under `workflow_dispatch`. Either way terramate
    diffs the wrong tree against the base and reports nothing changed. Both SHAs are asserted
    because the message is the only place naming the tree planned and the one that should be."""
    monkeypatch.setattr(bm, "_run", lambda args: "basebase\n")
    err = bm.head_checkout_error("cafe1234", False)
    assert err.startswith("::error::")
    assert "cafe1234" in err and "basebase" in err
    assert "nothing queued to apply" in err


def test_an_unstated_head_is_refused_naming_the_input(monkeypatch):
    """Refused, not skipped, and without probing git: the stated head is the only thing this
    check compares against, so an omitted `head-sha` leaves it unmade -- the direction that
    plans the base, greens the gate and queues no applies. The message is asserted, not the
    refusal alone: it names the input and the drift opt-out, never the mismatch wording."""
    monkeypatch.setattr(bm, "_run", lambda args: pytest.fail("probed with no stated head"))
    for head in ("", "   "):
        assert bm.head_checkout_error(head, False) == (
            "::error::this run did not state the commit it is planning. The `head-sha` input "
            "of shipmate's `build-matrix` action must be the pull request's head commit, so "
            "this checkout can be checked against it. Wire it to the `head-sha` output of "
            "shipmate's `pr-facts` action; see `docs/getting-started.md`. A workflow with no "
            "pull request at all (a drift sweep) passes `sweep: true` instead."
        )


def test_the_opt_out_skips_the_head_checkout_check(monkeypatch):
    """The drift path has no pull-request context and no reason to be at any particular commit, so
    `git rev-parse` must not even run."""
    monkeypatch.setattr(bm, "_run", lambda args: pytest.fail("head checkout was probed"))
    for head in ("", "cafe1234"):
        assert bm.head_checkout_error(head, True) == ""


@pytest.mark.parametrize(
    ("value", "opted_out"),
    [
        pytest.param("true", True, id="true"),
        pytest.param("True", False, id="capitalised"),
        pytest.param(" true", False, id="padded"),
        pytest.param(" TRUE ", False, id="padded-upper"),
        pytest.param("false", False, id="manifest-default"),
        pytest.param("False", False, id="capitalised-false"),
        pytest.param(" false ", False, id="padded-false"),
        pytest.param("yes", False, id="yes"),
        pytest.param("1", False, id="one"),
        pytest.param("", False, id="empty"),
        pytest.param("sweep", False, id="input-name"),
    ],
)
def test_main_parses_the_opt_out_once_for_all_three_guards(monkeypatch, tmp_path, value, opted_out):
    """Only the exact string "true" is a sweep: the manifest default is the non-empty string
    "false", so anything that treats a non-empty value as a sweep would plan every unstated
    run unchecked, and a capitalised or padded value fails closed into the refusals.

    Mutations: `.lower()` on main's read -- the capitalised row reddens; `.strip()` -- the
    padded row reddens; parse with `bool(...)` -- every non-empty `False` row reddens; pass
    the raw variable to `tag_filter_error` -- every row reddens."""
    seen = []
    for name in ("fork_pr_error", "head_checkout_error", "tag_filter_error"):
        monkeypatch.setattr(bm, name, lambda *args, _n=name: seen.append((_n, args[-1])) or "")
    _run_main(monkeypatch, tmp_path, {"SHIPMATE_SWEEP": value})
    assert seen == [
        ("fork_pr_error", opted_out),
        ("head_checkout_error", opted_out),
        ("tag_filter_error", opted_out),
    ]


def test_a_capitalised_sweep_refuses_a_run_with_no_head_repository(monkeypatch, tmp_path):
    """`sweep: True` is not a sweep, so a run stating no head repository meets the fork
    refusal instead of being planned unchecked.

    Mutation: `.lower()` on main's read of `SHIPMATE_SWEEP` -- the run plans."""
    called = []
    with pytest.raises(SystemExit) as exc_info:
        _run_main(
            monkeypatch,
            tmp_path,
            {
                "GITHUB_EVENT_NAME": "schedule",
                "GITHUB_REPOSITORY": "acme/iac",
                "SHIPMATE_SWEEP": "True",
            },
            called=called,
        )
    assert str(exc_info.value) == _UNSTATED_HEAD_REPO
    assert called == []


def test_main_refuses_a_run_that_states_no_head(monkeypatch, tmp_path):
    # The wiring this backstop exists for: a correct `head-repo` gets past the
    # fork refusal, and the wrapper never wired `head-sha`.
    called = []
    monkeypatch.setattr(bm, "_run", lambda args: pytest.fail("probed with no stated head"))
    with pytest.raises(SystemExit) as excinfo:
        _run_main(
            monkeypatch,
            tmp_path,
            {
                "GITHUB_EVENT_NAME": "pull_request_target",
                "GITHUB_REPOSITORY": "acme/iac",
                "SHIPMATE_HEAD_REPO": "acme/iac",
            },
            called=called,
        )
    assert "did not state the commit it is planning" in str(excinfo.value)
    assert called == []


def test_main_refuses_a_dispatched_run_whose_checkout_is_not_the_stated_head(monkeypatch, tmp_path):
    """The leg the event-keyed version made no check on: `actions/checkout` takes the dispatch
    ref here, so a wrapper that adds the trigger and forgets `ref:` plans the default branch
    against the base, comes out empty, skips the plan job and greens the gate with nothing
    queued. Held at main(): re-keying the guard cannot help if the call site keys on events."""
    called = []
    monkeypatch.setattr(bm, "_run", lambda args: "defaultbranch\n")
    with pytest.raises(SystemExit) as excinfo:
        _run_main(
            monkeypatch,
            tmp_path,
            {
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_REPOSITORY": "acme/iac",
                "SHIPMATE_HEAD_REPO": "acme/iac",
                "SHIPMATE_HEAD_SHA": "cafe1234",
            },
            called=called,
        )
    assert "which is not the commit it is planning (cafe1234)" in str(excinfo.value)
    assert called == []


def test_main_refuses_a_dispatched_run_that_states_no_head(monkeypatch, tmp_path):
    # The same leg, unstated rather than mismatching: a wrapper that adds the
    # trigger and never wires `head-sha` is refused too, without probing git.
    called = []
    monkeypatch.setattr(bm, "_run", lambda args: pytest.fail("probed with no stated head"))
    with pytest.raises(SystemExit) as excinfo:
        _run_main(
            monkeypatch,
            tmp_path,
            {
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_REPOSITORY": "acme/iac",
                "SHIPMATE_HEAD_REPO": "acme/iac",
            },
            called=called,
        )
    assert "did not state the commit it is planning" in str(excinfo.value)
    assert called == []


def test_plan_workflow_at_the_contract_path_is_planned(monkeypatch, tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "shipmate.yml").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert bm.plan_workflow_error("pull_request_target") == ""


def test_a_renamed_plan_workflow_is_refused(monkeypatch, tmp_path):
    """This refusal makes the path load-bearing: no plan-run lookup matches it literally any
    more, so a rename would merge green with doctor's `shipmate.yml` probe reduced to a
    notice. The whole message is hand-written, and names only consequences still true now
    that the plan run id rides on each apply check and `actions/dispatch` aims every verb at
    this one file; a clause about plan-run discovery coming back here would be a falsehood."""
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "shipmate-plan.yml").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert bm.plan_workflow_error("pull_request") == (
        "::error::this repository has no `.github/workflows/shipmate.yml`, the one path "
        "`CONTRACT.md` lets the consumer's workflow file live at, and this refusal is what "
        "enforces it. That exact filename is matched literally by `shipmate doctor`, whose "
        "`shipmate.yml` probe checks its job name, dispatch wiring, event routing and drift "
        "call, and by "
        "`actions/dispatch`, which sends every commented verb to it. A consumer workflow "
        "under any other name draws that probe's could-not-read notice and doctor's own "
        "`pull_request_target` warning, and is reached by no `shipmate` command at "
        "all. Move the consumer's workflow back to `.github/workflows/shipmate.yml`."
    )


def test_plan_workflow_check_is_skipped_off_a_pull_request(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for event in ("schedule", "workflow_dispatch", "push", ""):
        assert bm.plan_workflow_error(event) == ""


def test_main_refuses_a_base_checkout(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(bm, "_run", lambda args: "basebase\n")
    with pytest.raises(SystemExit) as excinfo:
        _run_main(
            monkeypatch,
            tmp_path,
            {
                "GITHUB_EVENT_NAME": "pull_request_target",
                "GITHUB_REPOSITORY": "acme/iac",
                "SHIPMATE_HEAD_REPO": "acme/iac",
                "SHIPMATE_HEAD_SHA": "cafe1234",
            },
            called=called,
        )
    assert "which is not the commit it is planning (cafe1234)" in str(excinfo.value)
    assert called == []


def test_main_refuses_a_missing_plan_workflow(monkeypatch, tmp_path):
    called = []
    with pytest.raises(SystemExit) as excinfo:
        _run_main(
            monkeypatch,
            tmp_path,
            {
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_REPOSITORY": "acme/iac",
                "SHIPMATE_HEAD_REPO": "acme/iac",
            },
            called=called,
            plan_workflow=False,
            head_sha="cafe1234",
        )
    assert ".github/workflows/shipmate.yml" in str(excinfo.value)
    assert called == []


def test_build_matrix_action_declares_its_inputs():
    """`sweep: "true"` is the one input that skips a refusal: it skips the head-repository and
    head-commit refusals, and only engine `drift.yml` sets it, in engine-owned YAML a
    pull-request author cannot edit. No other input turns a refusal off: `head-repo` and
    `head-sha` are what those two refusals key on and an empty value refuses either, and `tags`
    is refused outside a sweep. The direction is chosen so a forgotten input refuses (plan
    wrapper) or reddens the sweep (drift), never plans a fork.

    Hand-written, name -> default; descriptions are prose and not pinned. Mutation: restore
    `no-pull-request` as a second input."""
    from _loader import action_yaml

    doc = action_yaml("build-matrix")
    assert {name: spec.get("default") for name, spec in doc["inputs"].items()} == {
        "base-sha": "",
        "sweep": "false",
        "head-repo": "",
        "head-sha": "",
        "tags": "",
        "github-vars": "",
    }


def test_build_matrix_action_hands_the_script_the_names_it_reads():
    """The whole `env:` block against a hand-written constant: the script reads
    SHIPMATE_HEAD_REPO, SHIPMATE_HEAD_SHA and SHIPMATE_SWEEP by name, so a renamed
    key here leaves every plan run unstated -- refused, but only in production."""
    from _loader import action_steps

    (step,) = [s for s in action_steps("build-matrix") if s.get("id") == "build"]
    assert step["env"] == {
        "SHIPMATE_BASE_SHA": "${{ inputs.base-sha }}",
        "SHIPMATE_HEAD_REPO": "${{ inputs.head-repo }}",
        "SHIPMATE_HEAD_SHA": "${{ inputs.head-sha }}",
        "SHIPMATE_SWEEP": "${{ inputs.sweep }}",
        "SHIPMATE_TAGS": "${{ inputs.tags }}",
        "SHIPMATE_GITHUB_VARS": "${{ inputs.github-vars }}",
        "GH_TOKEN": "${{ github.token }}",
    }


def test_build_matrix_action_declares_the_outputs_the_gate_reads():
    """Mutation: delete the `refusal` output -- the gate never names a detect error.
    Mutation: delete the `env_order` output -- the plan comment orders environments by name.
    Mutation: delete the `command_hints` output -- the plan comment never shows its commands.
    Mutation: delete or rename the `auto_envs` output -- no auto apply is ever dispatched."""
    # `count` is what the trusted summary job measures its evidence against, so
    # a rename or a rewire here is a silent hole in the gate. Hand-written,
    # name -> wiring; descriptions are prose and deliberately not pinned.
    doc = action_yaml("build-matrix")
    assert {name: spec["value"] for name, spec in doc["outputs"].items()} == {
        "matrix": "${{ steps.build.outputs.matrix }}",
        "empty": "${{ steps.build.outputs.empty }}",
        "count": "${{ steps.build.outputs.count }}",
        "max_parallel": "${{ steps.build.outputs.max_parallel }}",
        "command_hints": "${{ steps.build.outputs.command_hints }}",
        "auto_envs": "${{ steps.build.outputs.auto_envs }}",
        "cells": "${{ steps.build.outputs.cells }}",
        "unmanaged": "${{ steps.build.outputs.unmanaged }}",
        "lost": "${{ steps.build.outputs.lost }}",
        "refusal": "${{ steps.build.outputs.refusal }}",
        "env_order": "${{ steps.build.outputs.env_order }}",
    }


def test_the_cells_output_is_the_scanned_tree_not_the_matrix(monkeypatch, tmp_path):
    """`cells` is the tree `compute_cells` returns, names alone, in its order: drift-issues closes
    the Issue of every cell missing from it, so the filtered matrix there would close the Issues
    of every cell outside the query. Mutations: write `cells` from the matrix rows' names;
    emit the tree reversed."""
    outputs, _ = _run_main(
        monkeypatch,
        tmp_path,
        {
            "GITHUB_EVENT_NAME": "schedule",
            "GITHUB_REPOSITORY": "acme/iac",
            "SHIPMATE_SWEEP": "true",
        },
        cells=(("stacks/app", "dev-eu"),),
        tree=[
            {"environment": "dev-eu", "stack": "stacks/app"},
            {"environment": "prod-eu", "stack": "stacks/db"},
        ],
    )
    assert json.loads(outputs["cells"]) == [
        {"environment": "dev-eu", "stack": "stacks/app"},
        {"environment": "prod-eu", "stack": "stacks/db"},
    ]


def test_rejects_stacks_that_slug_to_one_artifact_name():
    # `a/b` and `a-b` both slug to `a-b`, so both cells' plan artifact is
    # `plan.dev-eu.a-b` and an apply downloads whichever landed last.
    stacks = ["a/b", "a-b"]
    with pytest.raises(SystemExit) as exc_info:
        bm.full_tree(["dev-eu"], {"dev-eu": stacks}, {s: ["env/dev-eu"] for s in stacks})
    assert str(exc_info.value) == (
        "::error::a-b, a/b all map to the plan artifact 'plan.dev-eu.a-b': distinct "
        "stack paths sharing one artifact name would make an apply download another "
        "stack's plan. Rename one so the path->'-' slug is unique."
    )


def test_same_slug_in_different_envs_is_allowed():
    # The env is part of the artifact name: `plan.dev-eu.a-b` and
    # `plan.prod-eu.a-b` are distinct, so there is nothing to collide.
    cells = bm.full_tree(
        ["dev-eu", "prod-eu"],
        {"dev-eu": ["a/b"], "prod-eu": ["a-b"]},
        {"a/b": ["env/dev-eu"], "a-b": ["env/prod-eu"]},
    )
    assert [c["stack"] for c in cells] == ["a/b", "a-b"]


@pytest.mark.parametrize("cells", [(("stacks/app", "dev-eu"), ("stacks/db", "dev-eu")), ()])
def test_the_three_outputs_agree_on_one_cell_list(monkeypatch, tmp_path, cells):
    """`count`, `matrix` and `empty` all describe the same cells.

    Fails when `count` is computed from anything but the list written to `matrix`: the
    trusted summary job checks its evidence against `count`, so a count above the cells
    planned would hold the gate on a cell no plan run ever produced."""
    outputs, _ = _run_main(
        monkeypatch,
        tmp_path,
        {
            "GITHUB_EVENT_NAME": "schedule",
            "GITHUB_REPOSITORY": "acme/iac",
            "SHIPMATE_SWEEP": "true",
        },
        cells=cells,
    )
    assert outputs["count"] == str(len(cells))
    assert len(json.loads(outputs["matrix"])["include"]) == len(cells)
    assert outputs["empty"] == ("true" if not cells else "false")


def test_the_plan_workflow_path_is_the_one_consumer_file():
    """The whole path, hand-written. Three surfaces match this literally — `actions/dispatch`
    aims every verb at it, `shipmate doctor` keys its fork-trigger exemption and its five
    filename-keyed probes on it, and this refusal is what makes
    a repository carry it at all. A rename here silently retires all of them.

    Mutation: set it back to `.github/workflows/plan.yml`.
    """
    assert bm.PLAN_WORKFLOW == ".github/workflows/shipmate.yml"


#: The plan and drift legs of one call site. Hand-written; `sweep` is the only difference
#: between them, and it is what decides whether an environment absent from the scan is evidence.
_TWO_ENV_TABLE = {
    "layout": "folder",
    "environments": {
        "dev-eu": {"region": "eu-west-1"},
        "dev-us": {"region": "us-east-1"},
    },
}
_UNUSED_DEV_US = (
    "::warning::the environment table declares dev-us, which no stack tags. Remove the "
    "entry, or tag the stacks that belong to it. This is a warning rather than a refusal "
    "because the table is read from the default branch and the tags from this branch, so "
    "an environment or a workload arrives and leaves over two pull requests."
)


def test_a_plan_run_says_nothing_about_an_environment_outside_the_changed_set(
    monkeypatch, tmp_path, capsys
):
    """A run that is not a sweep scans the CHANGED stacks only, so `dev-us` having no stack in that
    set says nothing about whether any stack tags it.

    Mutation: pass `tagged` unconditionally at the call site. Every plan run then
    warns about every environment with no changed stack. The drift sibling below is what keeps
    this test from passing with the diagnostic deleted."""
    _run_main(
        monkeypatch,
        tmp_path,
        {
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REPOSITORY": "acme/iac",
            "SHIPMATE_HEAD_REPO": "acme/iac",
        },
        head_sha="a" * 40,
        table=_TWO_ENV_TABLE,
    )
    assert "dev-us" not in capsys.readouterr().out


def test_a_whole_tree_run_does_report_the_unused_entry(monkeypatch, tmp_path, capsys):
    """The same call site with `sweep: true` -- drift -- has scanned the whole tree, so the
    absence is evidence. Mutation: pass `None` unconditionally there; drift then never reports
    an unused entry and the diagnostic reaches no production path at all."""
    _run_main(
        monkeypatch,
        tmp_path,
        {
            "GITHUB_EVENT_NAME": "schedule",
            "GITHUB_REPOSITORY": "acme/iac",
            "SHIPMATE_SWEEP": "true",
        },
        table=_TWO_ENV_TABLE,
    )
    assert _UNUSED_DEV_US in capsys.readouterr().out.splitlines()


_DRIFT_ENV = {
    "GITHUB_EVENT_NAME": "schedule",
    "GITHUB_REPOSITORY": "acme/iac",
    "SHIPMATE_SWEEP": "true",
}
#: `stacks/net` alone tags `workload/net`, and `stacks/web` alone sits in dev-us.
_WORKLOAD_TREE = {
    "stacks/app": ["env/dev-eu", "workload/app"],
    "stacks/net": ["env/dev-eu", "workload/net"],
    "stacks/web": ["env/dev-us"],
}
_LISTING_TABLE = {
    "layout": "folder",
    "identities": {"dev": {"aws": {"apply": "arn:aws:iam::111111111111:role/apply"}}},
    "environments": {
        "dev-eu": {"region": "eu-west-1", "identity": "dev", "workloads": ["app", "net"]},
        "dev-us": {"region": "us-east-1"},
    },
}


def test_drift_passes_the_whole_tree_workload_map(monkeypatch, tmp_path):
    """Mutations: pass `set(tagged)` at `main`'s drift call -- the real `env_config` raises
    `AttributeError: 'set' object has no attribute 'get'`; or build `tagged` from the cells in
    `compute_cells` -- dev-us records `frozenset({''})`."""
    seen = spy_env_config(monkeypatch, bm)
    _run_main(monkeypatch, tmp_path, _DRIFT_ENV, table=_LISTING_TABLE, stacks=_WORKLOAD_TREE)
    assert seen == [{"dev-eu": frozenset({"app", "net"}), "dev-us": frozenset()}]


def test_a_plan_passes_no_workload_map(monkeypatch, tmp_path):
    """Mutation: pass `tagged` unconditionally at `main`'s `env_config` call -- the spy records
    the workload map."""
    seen = spy_env_config(monkeypatch, bm)
    _run_main(
        monkeypatch,
        tmp_path,
        {
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REPOSITORY": "acme/iac",
            "SHIPMATE_HEAD_REPO": "acme/iac",
        },
        head_sha="a" * 40,
        table=_LISTING_TABLE,
        stacks=_WORKLOAD_TREE,
    )
    assert seen == [None]


def test_a_supplied_table_is_validated_exactly_as_a_read_one_is(monkeypatch):
    """`table=` exists to spare a second read, not to skip the checks. `validate_structure`
    alone does not supply `_check_dry_coverage`, so a supplied `layout: tf_vars`
    table with no entry for a cell's environment would derive neither identity variable and
    the cell would plan undistinguished from every other environment's.

    Mutation: on the supplied-table branch return `ec.validate_structure(table)` instead of
    routing through `ec.validate`.
    """
    monkeypatch.setattr(bm.ec, "read_table", _no_read)
    cells = [{"stack": "stacks/app", "environment": "dev-eu", "workload": ""}]
    with pytest.raises(SystemExit) as exc:
        bm.env_config(cells, table={"layout": "tf_vars", "environments": {}})
    assert "dev-eu" in str(exc.value) and "no entry in it" in str(exc.value)


_MISSING = object()


def _resolving(binding):
    """A `resolve` double, matching its signature, whose row carries `binding` as its
    `env_binding`, or no such key for `_MISSING`."""

    def resolve(table, env, path, workload):
        row = {"role_arn": "", "cred_region": "", "tf_vars": {}, "config_path": path}
        if binding is not _MISSING:
            row["env_binding"] = binding
        return row

    return resolve


@pytest.mark.parametrize(
    ("binding", "shown"),
    [(_MISSING, "None"), (42, "42"), ("", "''")],
    ids=["missing", "int", "empty"],
)
def test_a_row_without_a_usable_binding_refuses(monkeypatch, binding, shown):
    """Every cell job binds `environment:` from the row, and GitHub binds no environment for an
    empty one: the cell would run outside every environment control.

    Mutation: delete the `env_binding` check from `stamp_rows` -- all three cases red.
    """
    monkeypatch.setattr(bm.ec, "resolve", _resolving(binding))
    cells = [{"stack": "stacks/app", "environment": "dev-eu", "workload": ""}]
    with pytest.raises(SystemExit) as exc:
        bm.stamp_rows(cells, _MINIMAL_TABLE, "plan")
    assert str(exc.value) == (
        f"::error::stacks/app in dev-eu resolved env_binding {shown}, which names no GitHub "
        "Environment. A job bound to it would run outside every environment control, so no "
        "matrix is written."
    )


def test_main_writes_no_matrix_when_a_binding_refuses(monkeypatch, tmp_path, capsys):
    """The refusal lands before the matrix is written, so no half-written matrix reaches a job
    that would bind it; only the refusal line is.

    Mutation: delete the `env_binding` check from `stamp_rows` -- main writes the matrix.
    """
    monkeypatch.setattr(bm.ec, "resolve", _resolving(""))
    env = {
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_REPOSITORY": "acme/iac",
        "SHIPMATE_HEAD_REPO": "acme/iac",
    }
    _annotated(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, tmp_path, env, head_sha="cafe1234")
    assert exc.value.code == 1
    assert capsys.readouterr().err == (
        "::error::stacks/app in dev-eu resolved env_binding '', which names no GitHub "
        "Environment. A job bound to it would run outside every environment control, so no "
        "matrix is written.\n"
    )
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == (
        "refusal=stacks/app in dev-eu resolved env_binding '', which names no GitHub "
        "Environment. A job bound to it would run outside every environment control, so no "
        "matrix is written.\n"
    )


def test_a_refusal_reaches_the_refusal_output_and_still_fails_the_step(
    monkeypatch, tmp_path, capsys
):
    """Mutation: drop `raise SystemExit(1) from None` from `run_annotated` -- no `SystemExit`
    reaches the test."""
    tree = {"stacks/a": ["env/dev", "workload/net", "workload/web"]}
    _annotated(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch,
            tmp_path,
            _PLAN_ENV,
            head_sha="a" * 40,
            table=_CORE_ONLY,
            stacks=tree,
        )
    assert exc.value.code == 1
    assert capsys.readouterr().err == f"{_TWO_TAG_A}\n"
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == (
        "refusal=stack 'stacks/a' carries 2 workload tags (workload/net, workload/web), and a "
        "stack carries at most one `workload/<name>` tag. Keep one in the stack's `tags` and "
        "remove the rest.\n"
    )


def test_a_multi_error_refusal_writes_its_first_error_only(monkeypatch, tmp_path, capsys):
    """The output carries the first error, its own lines joined; a second error is dropped,
    and the annotation still carries both.

    Mutation: write the whole code as the `refusal` line in `run_annotated` -- the gap line
    follows it."""
    tree = {
        "stacks/a": ["env/dev", "workload/net", "workload/web"],
        "stacks/c": ["env/dev", "workload/app"],
    }
    _annotated(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch,
            tmp_path,
            _PLAN_ENV,
            head_sha="a" * 40,
            table=_CORE_ONLY,
            stacks=tree,
        )
    assert exc.value.code == 1
    assert capsys.readouterr().err == (
        f"{_TWO_TAG_A}\n"
        "::error::1 cell(s) carry a workload tag their environment's workloads list does not "
        "name: stacks/c in dev (workload/app; dev lists core). A listed workload is the only one "
        "the default branch grants a role to. Retag the stack, or add the workload to "
        "environments.<env>.workloads in .github/shipmate-config.yml on the default branch, "
        "which is where this table is read from: merge it there on its own pull request first.\n"
    )
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == (
        "refusal=stack 'stacks/a' carries 2 workload tags (workload/net, workload/web), and a "
        "stack carries at most one `workload/<name>` tag. Keep one in the stack's `tags` and "
        "remove the rest.\n"
    )


def test_a_runner_without_pyyaml_reaches_the_refusal_output(monkeypatch, tmp_path, capsys):
    """The default branch's file is read through the real parser on a runner without PyYAML,
    and the gate must name the prerequisite rather than a generic detect failure.

    Mutation: delete the `runner_refusal()` call from `load_config` -- the parse then runs
    without its prerequisite and no `refusal` line names it."""
    monkeypatch.setitem(sys.modules, "yaml", None)
    refusal = (
        "the engine needs PyYAML for python3 and this runner has none. Install python3-yaml "
        "(Debian or Ubuntu) or PyYAML >= 6 for this runner's python3; CONTRACT.md section "
        "Runner prerequisites lists it."
    )
    _annotated(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch,
            tmp_path,
            _PLAN_ENV,
            head_sha="a" * 40,
            read_table=lambda: bm.ec.parse_table("layout: folder\n"),
        )
    assert exc.value.code == 1
    assert capsys.readouterr().err == f"::error::{refusal}\n"
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == f"refusal={refusal}\n"


def test_an_exit_that_is_not_a_refusal_writes_no_refusal(monkeypatch, tmp_path, capsys):
    """Mutation: drop the `isinstance` check from `is_refusal` -- `int.startswith` raises
    `AttributeError`."""

    def exit_one(repository, head_repo, sweep):
        raise SystemExit(1)

    monkeypatch.setattr(bm, "fork_pr_error", exit_one)
    _annotated(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, tmp_path, _PLAN_ENV, head_sha="a" * 40)
    assert exc.value.code == 1
    assert capsys.readouterr().err == ""
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == ""


#: `dev-eu` lists `core` and `net`; `prod` names no identity, so a tag there is inert.
_LISTED = {
    "layout": "folder",
    "identities": {"dev": {"aws": {"account": "111111111111", "apply": "deploy-{workload}"}}},
    "environments": {
        "dev-eu": {"region": "eu-west-1", "identity": "dev", "workloads": ["net", "core"]},
        "prod": {"region": "eu-west-1"},
    },
}


def test_every_cell_tagged_outside_its_list_is_named_in_one_refusal():
    """Mutation: raise inside the loop -- only stacks/app is named."""
    cells = [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": "app"},
        {"stack": "stacks/net", "environment": "dev-eu", "workload": "net"},
        {"stack": "stacks/free", "environment": "prod", "workload": "app"},
        {"stack": "stacks/db", "environment": "dev-eu", "workload": "db"},
        {"stack": "stacks/bare", "environment": "dev-eu", "workload": ""},
    ]
    with pytest.raises(SystemExit) as exc:
        bm.refuse_workload_gaps(cells, _LISTED)
    assert str(exc.value) == (
        "::error::2 cell(s) carry a workload tag their environment's workloads list does not "
        "name: stacks/app in dev-eu (workload/app; dev-eu lists core, net); stacks/db in dev-eu "
        "(workload/db; dev-eu lists core, net). A listed workload is the only one the default "
        "branch grants a role to. Retag the stack, or add the workload to "
        "environments.<env>.workloads in .github/shipmate-config.yml on the default branch, "
        "which is where this table is read from: merge it there on its own pull request first."
    )


_PLAN_ENV = {
    "GITHUB_EVENT_NAME": "pull_request",
    "GITHUB_REPOSITORY": "acme/iac",
    "SHIPMATE_HEAD_REPO": "acme/iac",
}
#: Both environments name an identity and list only `core`, so every other tag is outside.
_CORE_ONLY = {
    "layout": "folder",
    "identities": {"dev": {"aws": {"account": "111111111111", "apply": "deploy-{workload}"}}},
    "environments": {
        "dev": {"region": "eu-west-1", "identity": "dev", "workloads": ["core"]},
        "prod": {"region": "eu-west-1", "identity": "dev", "workloads": ["core"]},
    },
}
_TWO_TAG_A = (
    "::error::stack 'stacks/a' carries 2 workload tags (workload/net, workload/web), and a "
    "stack carries at most one `workload/<name>` tag. Keep one in the stack's `tags` and remove "
    "the rest."
)
_TWO_TAG_B = (
    "::error::stack 'stacks/b' carries 2 workload tags (workload/db, workload/dns), and a "
    "stack carries at most one `workload/<name>` tag. Keep one in the stack's `tags` and remove "
    "the rest."
)


def test_plan_detect_names_every_two_tag_stack_and_every_gap_in_one_refusal(monkeypatch, tmp_path):
    """stacks/a sits in two environments and is named once; stacks/c's gap follows.

    Mutations: raise the two-tag messages before the table is read in `main` -- the gap line
    is missing; return the first workload instead of `None` from `workload_of`'s list branch --
    the gap line also names stacks/a and stacks/b; raise inside `workload_of` when given a list
    -- only stacks/a is named; call `workload_of` per cell in `full_tree` -- stacks/a is named
    twice."""
    tree = {
        "stacks/a": ["env/dev", "env/prod", "workload/net", "workload/web"],
        "stacks/b": ["env/dev", "workload/db", "workload/dns"],
        "stacks/c": ["env/dev", "workload/app"],
        "stacks/d": ["env/prod", "workload/core"],
    }
    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch, tmp_path, _PLAN_ENV, head_sha="a" * 40, table=_CORE_ONLY, stacks=tree
        )
    assert exc.value.code == "\n".join(
        [
            _TWO_TAG_A,
            _TWO_TAG_B,
            (
                "::error::1 cell(s) carry a workload tag their environment's workloads list does "
                "not name: stacks/c in dev (workload/app; dev lists core). A listed workload is "
                "the "
                "only one the default branch grants a role to. Retag the stack, or add the "
                "workload "
                "to environments.<env>.workloads in .github/shipmate-config.yml on the default "
                "branch, "
                "which is where this table is read from: merge it there on its own pull request "
                "first."
            ),
        ]
    )


def test_plan_detect_names_a_two_tag_stack_and_a_table_refusal_together(monkeypatch, tmp_path):
    """Mutation: raise the two-tag messages before the table is read in `main` -- the table
    error is missing."""
    tree = {"stacks/a": ["env/dev", "workload/net", "workload/web"]}
    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch,
            tmp_path,
            _PLAN_ENV,
            head_sha="a" * 40,
            table={"layout": "bogus"},
            stacks=tree,
        )
    assert exc.value.code == (
        _TWO_TAG_A + "\n::error::layout is 'bogus'; it must be one of tf_vars, workspace, folder."
    )


def test_a_cell_the_table_no_longer_lists_stamps_with_no_role():
    """`stamp_rows` resolves every cell, completed ones included, before the caller filters
    and refuses; a table-only change that drops a workload must strand no applied cell.

    Mutation: make `resolve` raise for a tag outside the list -- this raises.
    """
    cells = [{"stack": "stacks/db", "environment": "dev-eu", "workload": "db"}]
    assert bm.stamp_rows(cells, _LISTED, "apply") == [
        {
            "stack": "stacks/db",
            "environment": "dev-eu",
            "workload": "db",
            "role_arn": "",
            "cred_region": "eu-west-1",
            "tf_vars": {},
            "config_path": "apply",
            "env_binding": "dev-eu-apply",
        }
    ]


_EMPTY_TERM_ERROR = (
    "::error::the `tags` filter has an empty term: '{query}'. Terms are separated "
    "by ',' (OR) and ':' (AND), and each must be a tag in its on-disk form -- "
    "'env/dev-eu,env/dev-us' or 'env/dev-eu:workload/app'."
)
_NO_MATCH_NOTICE = (
    "::notice::the drift tags query '{query}' matches no stack x environment cell, so this "
    "sweep is empty."
)
_ON_DISK = " Tags match in their on-disk form, such as 'env/dev-eu'."


def _filtered(envs, stacks_by_env, tags_by_stack, query):
    return bm.filter_cells(bm.full_tree(envs, stacks_by_env, tags_by_stack), tags_by_stack, query)


def test_tag_filter_matches_a_cell_against_its_own_env_only():
    """A stack in two envs, filtered by one of them, yields that env's cell alone.

    Mutation: `_cell_tags` returns the raw `tags_by_stack` entry -- the prod-eu cell then
    carries `env/dev-eu` too and both cells survive.
    """
    cells = _filtered(
        ["dev-eu", "prod-eu"],
        {"dev-eu": ["stacks/app"], "prod-eu": ["stacks/app"]},
        {"stacks/app": ["env/dev-eu", "env/prod-eu"]},
        "env/dev-eu",
    )
    assert cells == [{"stack": "stacks/app", "environment": "dev-eu", "workload": ""}]


def test_tag_filter_on_a_non_env_tag_keeps_every_env_of_that_stack():
    """`workload/app` is not narrowed by the cell's env, so both cells survive.

    Mutation: `_cell_tags` keeps only the cell's own `env/*` tag -- nothing then carries
    `workload/app` and the sweep is empty.
    """
    cells = _filtered(
        ["dev-eu", "prod-eu"],
        {"dev-eu": ["stacks/app"], "prod-eu": ["stacks/app"]},
        {"stacks/app": ["env/dev-eu", "env/prod-eu", "workload/app"]},
        "workload/app",
    )
    assert [(c["environment"], c["stack"]) for c in cells] == [
        ("dev-eu", "stacks/app"),
        ("prod-eu", "stacks/app"),
    ]


def test_colon_binds_tighter_than_comma():
    """`a:b,c` is `(a AND b) OR c`, not `a OR (b AND c)`.

    Mutation: split on ':' outermost and ',' within -- the clauses become `[{a}, {b, c}]`,
    which keeps the a-only stack and drops the c-only one.
    """
    cells = _filtered(
        ["dev-eu", "prod-eu"],
        {"dev-eu": ["stacks/bare", "stacks/app"], "prod-eu": ["stacks/other"]},
        {
            "stacks/bare": ["env/dev-eu"],
            "stacks/app": ["env/dev-eu", "workload/app"],
            "stacks/other": ["env/prod-eu"],
        },
        "env/dev-eu:workload/app,env/prod-eu",
    )
    assert [(c["environment"], c["stack"]) for c in cells] == [
        ("dev-eu", "stacks/app"),
        ("prod-eu", "stacks/other"),
    ]


def test_comma_is_or_across_clauses():
    """A cell matching either clause is kept.

    Mutation: `all` replaces `any` over the clauses -- no stack carries both workloads, so
    the sweep is empty.
    """
    cells = _filtered(
        ["dev-eu"],
        {"dev-eu": ["stacks/a", "stacks/b"]},
        {
            "stacks/a": ["env/dev-eu", "workload/a"],
            "stacks/b": ["env/dev-eu", "workload/b"],
        },
        "workload/a,workload/b",
    )
    assert [c["stack"] for c in cells] == ["stacks/a", "stacks/b"]


def test_colon_is_and_within_a_clause():
    """A two-term clause keeps only a stack carrying both terms.

    Mutation: test the clause by intersection rather than subset -- the stack carrying
    `workload/app` alone then matches too.
    """
    cells = _filtered(
        ["dev-eu"],
        {"dev-eu": ["stacks/both", "stacks/one"]},
        {
            "stacks/both": ["env/dev-eu", "workload/app", "team/core"],
            "stacks/one": ["env/dev-eu", "workload/app"],
        },
        "workload/app:team/core",
    )
    assert [c["stack"] for c in cells] == ["stacks/both"]


def test_spaces_around_terms_match_as_without_them():
    """Terms are stripped, so a query written with spaces after its commas sweeps the same cells.

    Mutation: drop `.strip()` in `_parse_tag_query` -- `env/dev-eu ` is carried by no stack and
    the spaced query sweeps nothing.
    """
    args = (
        ["dev-eu", "dev-us", "prod-eu"],
        {"dev-eu": ["stacks/app"], "dev-us": ["stacks/app"], "prod-eu": ["stacks/app"]},
        {"stacks/app": ["env/dev-eu", "env/dev-us", "env/prod-eu"]},
    )
    expected = [("dev-eu", "stacks/app"), ("dev-us", "stacks/app")]
    for query in ("env/dev-eu , env/dev-us", "env/dev-eu,env/dev-us"):
        assert [(c["environment"], c["stack"]) for c in _filtered(*args, query)] == expected


def test_a_term_no_stack_carries_drops_only_its_own_clause(monkeypatch, tmp_path, capsys):
    """`env/dev-eu,env/nope` sweeps the dev-eu cells, and one notice names `env/nope`: a query
    written before an environment's first stack still sweeps the environments that exist.

    Mutations: return `[]` whenever a term is unknown -- the matrix is empty; drop the notice
    on the matching path -- stdout lacks it.
    """
    outputs, _ = _run_main(
        monkeypatch,
        tmp_path,
        {**_DRIFT_ENV, "SHIPMATE_TAGS": "env/dev-eu,env/nope"},
        stacks=_MULTI_ENV_TREE,
    )
    include = json.loads(outputs["matrix"])["include"]
    assert [(c["environment"], c["stack"]) for c in include] == [
        ("dev-eu", "stacks/app"),
        ("dev-eu", "stacks/db"),
    ]
    assert capsys.readouterr().out.splitlines() == [
        "::notice::the drift tags query 'env/dev-eu,env/nope' has clause(s) matching no cell: "
        "env/nope. No stack carries: env/nope." + _ON_DISK,
        "2 cell(s): dev-eu/stacks/app, dev-eu/stacks/db",
    ]


def test_the_drift_query_notice_keeps_a_percent_sequence_in_the_query(
    monkeypatch, tmp_path, capsys
):
    """The `tags` input is free text: escaped as workflow-command data, a `%0A` in it shows
    verbatim instead of breaking the notice. Mutation: drop `command_data` from the drift
    query notice's `print` -- red."""
    _run_main(
        monkeypatch,
        tmp_path,
        {**_DRIFT_ENV, "SHIPMATE_TAGS": "env/dev-eu,env/n%0A"},
        stacks=_MULTI_ENV_TREE,
    )
    assert capsys.readouterr().out.splitlines()[0] == (
        "::notice::the drift tags query 'env/dev-eu,env/n%250A' has clause(s) matching no cell: "
        "env/n%250A. No stack carries: env/n%250A." + _ON_DISK
    )


def test_conceptual_tag_form_names_both_halves_as_carried_by_no_stack(capsys):
    """`env:dev-eu` is the documentation form; on disk the tag is `env/dev-eu`.

    Mutation: translate ':' to '/' before matching -- the typo then silently sweeps.
    """
    cells = _filtered(
        ["dev-eu"], {"dev-eu": ["stacks/app"]}, {"stacks/app": ["env/dev-eu"]}, "env:dev-eu"
    )
    assert cells == []
    assert capsys.readouterr().out.splitlines() == [
        _NO_MATCH_NOTICE.format(query="env:dev-eu") + " No stack carries: dev-eu, env." + _ON_DISK
    ]


@pytest.mark.parametrize("query", ["env/dev-eu,", "env/dev-eu::workload/app", "env/dev-eu,   "])
def test_empty_term_is_refused(query):
    """A trailing comma, a doubled separator and a whitespace-only clause abort.

    Mutation: drop the `"" in terms` check -- `env/dev-eu,` reaches the matcher with the term
    `""` and returns an empty sweep instead of refusing.
    """
    with pytest.raises(SystemExit) as exc_info:
        _filtered(
            ["dev-eu"],
            {"dev-eu": ["stacks/app"]},
            {"stacks/app": ["env/dev-eu", "workload/app"]},
            query,
        )
    assert str(exc_info.value) == _EMPTY_TERM_ERROR.format(query=query)


def test_known_terms_that_co_occur_nowhere_empty_the_sweep(capsys):
    """Every term exists, but no cell carries the conjunction: an empty sweep and a notice
    naming the query, with no `No stack carries` clause.

    Mutation: append the clause unconditionally -- the notice gains ` No stack carries: .`.
    """
    cells = _filtered(
        ["dev-eu", "prod-eu"],
        {"dev-eu": ["stacks/db"], "prod-eu": ["stacks/app"]},
        {
            "stacks/db": ["env/dev-eu", "workload/db"],
            "stacks/app": ["env/prod-eu", "workload/app"],
        },
        "env/dev-eu:workload/app",
    )
    assert cells == []
    assert capsys.readouterr().out.splitlines() == [
        _NO_MATCH_NOTICE.format(query="env/dev-eu:workload/app")
    ]


def test_a_clause_of_known_terms_matching_no_cell_is_named_beside_a_live_one(capsys):
    """`env/prod-eu` and `workload/app` both exist but never share a cell, so the first clause
    sweeps nothing while `env/dev-eu` sweeps its cells. The dead clause is named, with no `No
    stack carries` sentence, because every term is carried.

    Mutation: notice only when a term is unknown -- stdout is empty.
    """
    cells = _filtered(
        ["dev-eu", "prod-eu"],
        {"dev-eu": ["stacks/app", "stacks/db"], "prod-eu": ["stacks/net"]},
        {
            "stacks/app": ["env/dev-eu", "workload/app"],
            "stacks/db": ["env/dev-eu"],
            "stacks/net": ["env/prod-eu"],
        },
        "env/prod-eu:workload/app,env/dev-eu",
    )
    assert [(c["environment"], c["stack"]) for c in cells] == [
        ("dev-eu", "stacks/app"),
        ("dev-eu", "stacks/db"),
    ]
    assert capsys.readouterr().out.splitlines() == [
        (
            "::notice::the drift tags query 'env/prod-eu:workload/app,env/dev-eu' has clause(s) "
            "matching no cell: env/prod-eu:workload/app."
        )
    ]


def test_a_repeated_dead_clause_is_named_once(capsys):
    """Mutation: drop the `dict.fromkeys` dedupe -- the notice names `env/nope` twice."""
    cells = _filtered(
        ["dev-eu"],
        {"dev-eu": ["stacks/app"]},
        {"stacks/app": ["env/dev-eu"]},
        "env/nope,env/nope,env/dev-eu",
    )
    assert [(c["environment"], c["stack"]) for c in cells] == [("dev-eu", "stacks/app")]
    assert capsys.readouterr().out.splitlines() == [
        "::notice::the drift tags query 'env/nope,env/nope,env/dev-eu' has clause(s) matching "
        "no cell: env/nope. No stack carries: env/nope." + _ON_DISK
    ]


@pytest.mark.parametrize("query", ["", "   "])
def test_an_empty_query_is_no_filter_at_all(query):
    """An absent `tags` input is not an empty term.

    Mutation: let an empty query fall through to the parser -- it refuses with the empty-term
    error instead of returning the unfiltered cells.
    """
    args = (
        ["dev-eu"],
        {"dev-eu": ["stacks/app", "stacks/db"]},
        {"stacks/app": ["env/dev-eu"], "stacks/db": ["env/dev-eu"]},
    )
    assert _filtered(*args, query) == bm.full_tree(*args)


#: `stacks/app` sits in both environments; `stacks/db` only in dev-eu, `stacks/web` only in
#: dev-us.
_MULTI_ENV_TREE = {
    "stacks/app": ["env/dev-eu", "env/dev-us"],
    "stacks/db": ["env/dev-eu"],
    "stacks/web": ["env/dev-us"],
}
#: The full tree of `_MULTI_ENV_TREE`, hand-written in matrix order.
_MULTI_ENV_CELLS = [
    {"environment": "dev-eu", "stack": "stacks/app"},
    {"environment": "dev-eu", "stack": "stacks/db"},
    {"environment": "dev-us", "stack": "stacks/app"},
    {"environment": "dev-us", "stack": "stacks/web"},
]


def test_a_one_env_query_plans_that_env_and_outputs_the_whole_tree(monkeypatch, tmp_path):
    """`env/dev-eu` plans the dev-eu cells alone, and `cells` still names the dev-us ones, so
    drift-issues does not close their Issues.

    Mutations: write `cells` from the filtered list in `main`; build the tree from the filtered
    cells in `compute_cells`; `_cell_tags` returns the raw stack tags (the dev-us `stacks/app`
    cell enters the matrix).
    """
    outputs, _ = _run_main(
        monkeypatch,
        tmp_path,
        {**_DRIFT_ENV, "SHIPMATE_TAGS": "env/dev-eu"},
        stacks=_MULTI_ENV_TREE,
    )
    include = json.loads(outputs["matrix"])["include"]
    assert [(c["environment"], c["stack"]) for c in include] == [
        ("dev-eu", "stacks/app"),
        ("dev-eu", "stacks/db"),
    ]
    assert json.loads(outputs["cells"]) == _MULTI_ENV_CELLS


def test_a_detect_runs_the_shared_sweep_chain_once_each(monkeypatch, tmp_path):
    """`compute_cells` selects through `select_cells` and `main` checks through
    `check_cells`, each once, so a refusal added inside either helper reaches detect and
    onboard's drift-sweep item alike. `test_onboard.py` pins onboard's side.

    Mutations: inline `cap_cells(filter_cells(...))` back into `compute_cells` -- no
    `select_cells` call; inline `stamp_rows` and `refuse_workload_gaps` back into `main` --
    no `check_cells` call.
    """
    calls = record_calls(monkeypatch, bm, "select_cells", "check_cells")
    _run_main(
        monkeypatch,
        tmp_path,
        {**_DRIFT_ENV, "SHIPMATE_TAGS": "env/dev-eu"},
        stacks={"stacks/app": ["env/dev-eu"], "stacks/web": ["env/dev-us"]},
    )
    assert [name for name, _, _ in calls] == ["select_cells", "check_cells"]
    assert calls[1][1][0] == [{"stack": "stacks/app", "environment": "dev-eu", "workload": ""}]


def test_a_query_matching_no_cell_is_an_empty_sweep_with_a_notice(monkeypatch, tmp_path, capsys):
    """Exit 0 with an empty matrix: a drift file may precede its first tagged stack. `cells`
    stays the whole tree, so no Issue is closed for a cell the query never planned.

    Mutations: refuse a no-match query with `SystemExit` as before; drop the ` No stack carries`
    clause. The whole stdout is compared, so a second notice or a lost sentence reddens too.
    """
    outputs, _ = _run_main(
        monkeypatch,
        tmp_path,
        {**_DRIFT_ENV, "SHIPMATE_TAGS": "env/dev-eu:workload/nope"},
        stacks=_MULTI_ENV_TREE,
    )
    assert outputs["empty"] == "true"
    assert outputs["count"] == "0"
    assert json.loads(outputs["cells"]) == _MULTI_ENV_CELLS
    assert capsys.readouterr().out.splitlines() == [
        _NO_MATCH_NOTICE.format(query="env/dev-eu:workload/nope")
        + " No stack carries: workload/nope."
        + _ON_DISK,
        "0 cell(s): (none)",
    ]


def test_a_filtered_sweep_still_checks_every_environment_against_a_tf_vars_table(
    monkeypatch, tmp_path
):
    """The table covers dev-eu alone and the query sweeps dev-eu alone, yet a dev-us stack is
    still refused: the coverage check runs over the whole tree.

    Mutation: `env_config(cells, ...)` in `main` -- the dev-eu sweep plans.
    """
    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch,
            tmp_path,
            {**_DRIFT_ENV, "SHIPMATE_TAGS": "env/dev-eu"},
            table={"layout": "tf_vars", "environments": {"dev-eu": {"region": "eu-west-1"}}},
            stacks=_MULTI_ENV_TREE,
        )
    assert str(exc.value) == (
        "::error::layout: tf_vars derives TF_VAR_env and TF_VAR_region from the "
        "environment table, and dev-us has no entry in it."
    )


def test_a_filtered_sweep_still_refuses_a_slug_collision_outside_it(monkeypatch):
    """`a/b` and `a-b` collide in dev-us only, and the query sweeps dev-eu: still refused.

    Mutation: run `guard_slug_collisions` over the filtered cells instead of the tree.
    """
    tree = {"stacks/app": ["env/dev-eu"], "a/b": ["env/dev-us"], "a-b": ["env/dev-us"]}
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(tree))
    monkeypatch.setattr(bm, "all_tags", lambda: tree)
    with pytest.raises(SystemExit) as exc_info:
        bm.compute_cells(all_stacks=True, tags="env/dev-eu")
    assert str(exc_info.value) == (
        "::error::a-b, a/b all map to the plan artifact 'plan.dev-us.a-b': distinct "
        "stack paths sharing one artifact name would make an apply download another "
        "stack's plan. Rename one so the path->'-' slug is unique."
    )


def test_a_filtered_sweep_still_refuses_two_workload_tags_outside_it(monkeypatch):
    """stacks/dns is outside the `env/dev-eu` sweep, and its two workload tags still refuse it.

    Mutation: stamp `workload` on the filtered cells instead of the tree -- the sweep plans.
    """
    tree = {
        "stacks/app": ["env/dev-eu"],
        "stacks/dns": ["env/prod-eu", "workload/network", "workload/net"],
    }
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(tree))
    monkeypatch.setattr(bm, "all_tags", lambda: tree)
    with pytest.raises(SystemExit) as exc_info:
        bm.compute_cells(all_stacks=True, tags="env/dev-eu")
    assert exc_info.value.code == _TWO_WORKLOADS_ERROR


def test_a_tag_filter_does_not_hide_a_workload_from_drift(monkeypatch, tmp_path, capsys):
    """The filter drops `stacks/net`, the only stack tagging `workload/net`, from the cells; the
    tag still exists, so drift must not report `net` as untagged on every sweep.

    Mutation: derive `tagged` from the filtered cells in `compute_cells` -- the warning names
    `net`."""
    _run_main(
        monkeypatch,
        tmp_path,
        {**_DRIFT_ENV, "SHIPMATE_TAGS": "workload/app"},
        table=_LISTING_TABLE,
        stacks=_WORKLOAD_TREE,
    )
    assert capsys.readouterr().out.splitlines() == ["1 cell(s): dev-eu/stacks/app"]


_TAG_FILTER_ERROR = (
    "::error::the `tags` filter is only for a workflow with no pull request at all "
    "(a drift sweep), and this run did not pass `sweep: true`. In a plan "
    "job it would drop changed stacks from the matrix: a dropped stack gets no plan "
    "cell and no apply check, `shipmate / gate` greens over it, and the change merges "
    "and never applies. Remove the input from the plan job."
)


def test_main_refuses_a_tag_filter_on_a_plan_run_and_does_not_enumerate(monkeypatch, tmp_path):
    """A `tags` value outside a sweep aborts before the stacks are listed.

    Mutation: drop `tag_filter_error` from `main`'s `or` chain -- the run plans a narrowed
    matrix and `called` records the enumeration.
    """
    called = []
    with pytest.raises(SystemExit) as exc_info:
        _run_main(
            monkeypatch,
            tmp_path,
            {
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_REPOSITORY": "acme/iac",
                "SHIPMATE_HEAD_REPO": "acme/iac",
                "SHIPMATE_TAGS": "env/dev-eu",
            },
            called=called,
            head_sha="cafe1234",
        )
    assert str(exc_info.value) == _TAG_FILTER_ERROR
    assert called == []


def test_the_tag_filter_refusal_is_keyed_on_the_value():
    """A composite action's `required:`/`default:` is not enforced by GitHub Actions, so the
    refusal reads the value: any non-empty query outside a sweep is refused,
    and an absent input arrives as "" and is no filter at all.

    Mutation: `tag_filter_error` returns "" unconditionally -- the first case reddens."""
    assert bm.tag_filter_error("env/dev-eu", False) == _TAG_FILTER_ERROR
    assert bm.tag_filter_error("", False) == ""
    assert bm.tag_filter_error("   ", False) == ""
    assert bm.tag_filter_error("env/dev-eu", True) == ""


def test_a_drift_run_passes_the_query_through_verbatim(monkeypatch, tmp_path):
    """`compute_cells` is handed the query exactly as the input stated it.

    Mutation: hard-code `""` as main's `compute_cells` query, or strip the query there --
    the surrounding and inner whitespace here is the parser's to strip, and a second reading
    of the grammar is a second grammar.
    """
    _, called = _run_main(
        monkeypatch,
        tmp_path,
        {**_DRIFT_ENV, "SHIPMATE_TAGS": " env/dev-eu ,workload/app"},
    )
    assert called == [(True, "", " env/dev-eu ,workload/app")]


def test_an_env_tag_naming_no_table_entry_still_refuses_under_tf_vars(monkeypatch, tmp_path):
    """`env/nope` is a tag naming an environment the table lacks, not an unmanaged stack.

    Mutation: in `env_membership`, drop `env/*` tags naming no table entry -- the sweep plans.
    """
    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch,
            tmp_path,
            _DRIFT_ENV,
            table={"layout": "tf_vars", "environments": {"dev-eu": {"region": "eu-west-1"}}},
            stacks={"stacks/app": ["env/dev-eu"], "stacks/typo": ["env/nope"]},
        )
    assert str(exc.value) == (
        "::error::layout: tf_vars derives TF_VAR_env and TF_VAR_region from the "
        "environment table, and nope has no entry in it."
    )


@pytest.mark.parametrize(
    ("stacks", "expected"),
    [
        (
            {"stacks/app": ["env/dev-eu"], "stacks/z": [], "stacks/a": ["workload/x"]},
            {"count": 2, "paths": ["stacks/a", "stacks/z"]},
        ),
        ({"stacks/app": ["env/dev-eu"]}, {"count": 0, "paths": []}),
        (
            _TWENTY_UNMANAGED,
            {"count": 20, "paths": [f"stacks/u{i:02}" for i in range(10)]},
        ),
    ],
    ids=["two", "none", "twenty"],
)
def test_main_writes_the_unmanaged_count_and_the_first_ten_paths(
    monkeypatch, tmp_path, stacks, expected
):
    """Written on every run, `{"count": 0, "paths": []}` when none; the paths are capped so the
    value stays small enough for one environment variable. Mutation: omit the line when there
    are none. Mutation: emit every path -- `twenty` gets twenty."""
    outputs, _ = _run_main(monkeypatch, tmp_path, _DRIFT_ENV, stacks=stacks)
    assert json.loads(outputs["unmanaged"]) == expected


def test_a_pull_request_plan_writes_its_changed_unmanaged_stacks(monkeypatch, tmp_path):
    """The plan path (not a sweep) is what the comment line reads. Mutation: write
    `unmanaged` only in a sweep -- the key is missing."""
    outputs, _ = _run_main(
        monkeypatch,
        tmp_path,
        {
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REPOSITORY": "acme/iac",
            "SHIPMATE_HEAD_REPO": "acme/iac",
        },
        head_sha="cafe1234",
        stacks={"stacks/app": ["env/dev-eu"], "stacks/new": []},
    )
    matrix = json.loads(outputs.pop("matrix"))
    assert [c["stack"] for c in matrix["include"]] == ["stacks/app"]
    assert outputs == {
        "empty": "false",
        "count": "1",
        "max_parallel": "10",
        "command_hints": "false",
        "auto_envs": "[]",
        "cells": '[{"environment": "dev-eu", "stack": "stacks/app"}]',
        "unmanaged": '{"count": 1, "paths": ["stacks/new"]}',
        "lost": "",
        "env_order": '["dev-eu"]',
    }


def test_the_env_order_output_follows_the_needs_levels_not_the_names(monkeypatch, tmp_path):
    """`live` needs `staging`, so it is on env-level 1 and comes second although it sorts first by
    name.

    Mutation: sort by name alone in `waves.env_sequence` -- `["live", "staging"]`, red."""
    outputs, _ = _run_main(
        monkeypatch,
        tmp_path,
        _PR_ENV,
        cells=(("stacks/app", "live"), ("stacks/app", "staging")),
        head_sha="cafe1234",
        table={"layout": "folder", "environments": {"live": {"needs": ["staging"]}, "staging": {}}},
    )
    assert outputs["env_order"] == '["staging", "live"]'


@pytest.mark.parametrize(
    ("table", "expected"),
    [({"layout": "folder", "command_hints": True}, "true"), ({"layout": "folder"}, "false")],
    ids=["set", "unset"],
)
def test_detect_writes_the_tables_command_hints(monkeypatch, tmp_path, table, expected):
    """The plan summary reads this output to end the comment with its command section.

    Mutation: `write_outputs(command_hints=False)` -- "set" reads "false"; write
    `command_hints=True` -- "unset" reads "true"."""
    outputs, _ = _run_main(monkeypatch, tmp_path, _PR_ENV, head_sha="cafe1234", table=table)
    assert outputs["count"] == "1"  # Not vacuous: a cell exists.
    assert outputs["command_hints"] == expected


@pytest.mark.parametrize(
    ("modes", "expected"),
    [({"a": "auto", "b": "auto", "c": "bare"}, '["a", "b"]'), ({"a": "bare"}, "[]")],
    ids=["two-auto", "none"],
)
def test_detect_writes_the_tables_auto_envs(monkeypatch, tmp_path, modes, expected):
    """The plan summary reads this output to decide an auto apply and to say why none follows.

    Mutation: write every table environment -- "two-auto" names `c`, "none" names `a`. Mutation:
    write `command_hints`' value -- both rows read "false"."""
    table = {
        "layout": "folder",
        "environments": {e: {"apply_mode": m, "review": False} for e, m in modes.items()},
    }
    outputs, _ = _run_main(
        monkeypatch,
        tmp_path,
        _PR_ENV,
        cells=(("stacks/app", "a"),),
        head_sha="cafe1234",
        table=table,
    )
    assert outputs["count"] == "1"  # Not vacuous: a cell exists.
    assert outputs["auto_envs"] == expected


def _git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def _commit(files, message):
    """Writes `files` (`{path: text}`, `None` deletes) and commits them, returning the SHA."""
    for path, text in files.items():
        target = pathlib.Path(path)
        if text is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode())
    _git("add", "-A")
    _git("commit", "-q", "-m", message)
    return _git("rev-parse", "HEAD").strip()


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """An empty repository in `tmp_path` as the cwd, on branch `main`, with this machine's git
    config out of the way and an identity CI runners lack."""
    (tmp_path / "gitconfig").write_bytes(b"")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    for k in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(k, "shipmate tests")
    for k in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(k, "tests@example.invalid")
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    _git("init", "-q", "-b", "main")
    return repo


def _stack(tags, name="s"):
    return f'stack {{\n  name = "{name}"\n  tags = {tags}\n}}\n'


def test_lost_env_tags_names_a_stack_whose_diff_removes_an_env_line(git_repo):
    """`s` drops `env/dev`; `t` edits its name. Mutation: widen the capture to `"([^"]*)"` --
    the name edit yields a name and `t` appears."""
    base = _commit(
        {"s/stack.tm.hcl": _stack('["env/dev"]'), "t/stack.tm.hcl": _stack("[]", "t")}, "base"
    )
    _git("checkout", "-q", "-b", "pr")
    _commit({"s/stack.tm.hcl": _stack("[]"), "t/stack.tm.hcl": _stack("[]", "t2")}, "untag")
    assert bm.lost_env_tags(base, {"s": set(), "t": set()}) == {"s": ["dev"]}


def test_lost_env_tags_attributes_one_diff_to_each_stack(git_repo, monkeypatch):
    """Four stacks, one nested in another, one under a path git prints unquoted with a space,
    one under a path git quotes as octal escapes (`ø`), each losing its own tag, read through one
    `git diff`.

    Mutations: one `git diff` per stack again -- four `_run` calls; attribute a file to the
    first sorted stack whose path prefixes it (shortest prefix) -- `a/b`'s `prod` lands on `a`;
    return `rest[2:]` for an unquoted header -- each file's directory ends ` b/<stack>`, so no
    tag is attributed; skip the C-unquoting -- `ø` reads as its escape."""
    stacks = ["a", "a/b", "with space", "ø"]
    tags = ["dev", "prod", "qa", "uat"]
    base = _commit(
        {f"{s}/stack.tm.hcl": _stack(f'["env/{t}"]') for s, t in zip(stacks, tags, strict=True)},
        "base",
    )
    _git("checkout", "-q", "-b", "pr")
    _commit({f"{s}/stack.tm.hcl": _stack("[]") for s in stacks}, "untag")
    calls = record_calls(monkeypatch, bm, "_run")
    assert bm.lost_env_tags(base, dict.fromkeys(stacks, set())) == {
        "a": ["dev"],
        "a/b": ["prod"],
        "with space": ["qa"],
        "ø": ["uat"],
    }
    assert len(calls) == 1


def test_lost_env_tags_reads_a_file_moved_between_stacks_as_a_removal(git_repo):
    """Mutation: drop `--no-renames` -- git pairs the move as a rename and `s` loses nothing."""
    base = _commit(
        {"s/tags.tm.hcl": _stack('["env/dev"]'), "t/stack.tm.hcl": _stack("[]", "t")}, "base"
    )
    _git("checkout", "-q", "-b", "pr")
    _commit({"s/tags.tm.hcl": None, "t/tags.tm.hcl": _stack('["env/dev"]')}, "move")
    assert bm.lost_env_tags(base, {"s": set(), "t": {"dev"}}) == {"s": ["dev"]}


def test_lost_env_tags_ignores_a_file_in_no_stacks_directory(git_repo):
    """`modules/shared.tm.hcl`, `s/sub/extra.tm.hcl` and the root's `root.tm.hcl` each drop
    `"env/dev"`; none of their directories is a stack. Mutation: drop `and stack in head_envs`
    -- `KeyError`."""
    files = ["modules/shared.tm.hcl", "s/sub/extra.tm.hcl", "root.tm.hcl"]
    base = _commit(
        {"s/stack.tm.hcl": _stack("[]")} | dict.fromkeys(files, 'tags = ["env/dev"]\n'), "base"
    )
    _git("checkout", "-q", "-b", "pr")
    _commit(dict.fromkeys(files, "tags = []\n"), "untag")
    assert bm.lost_env_tags(base, {"s": set()}) == {}


def test_lost_env_tags_of_no_stacks_runs_no_git(monkeypatch):
    """No stack, no diff and no blob fetch. Mutation: drop `if not head_envs: return {}` --
    `_run` is called."""
    monkeypatch.setattr(bm, "_run", lambda args: pytest.fail(f"ran {args}"))
    assert bm.lost_env_tags("deadbeef", {}) == {}


def test_lost_env_tags_names_the_root_stack(git_repo):
    """Terramate lists the root stack as `.`, and a root file's directory is empty. Mutation:
    drop `or "."` from the attribution -- the file reads as stack `""` and is skipped."""
    base = _commit({"stack.tm.hcl": _stack('["env/dev"]')}, "base")
    _git("checkout", "-q", "-b", "pr")
    _commit({"stack.tm.hcl": _stack("[]")}, "untag")
    assert bm.lost_env_tags(base, {".": set()}) == {".": ["dev"]}


def test_lost_env_tags_ignores_an_env_directory_in_the_file_header(git_repo):
    """The diff's `--- a/stacks/env/app/...` header holds `env/app`, unquoted. Mutation: drop
    the quotes from `_ENV_TAG_LITERAL`."""
    base = _commit({"stacks/env/app/stack.tm.hcl": _stack("[]")}, "base")
    _git("checkout", "-q", "-b", "pr")
    _commit({"stacks/env/app/stack.tm.hcl": _stack("[]", "renamed")}, "edit")
    assert bm.lost_env_tags(base, {"stacks/env/app": set()}) == {}


def test_lost_env_tags_diffs_from_the_merge_base(git_repo):
    """`main` tags the stack after the branch point; the branch only renames it. Mutation:
    `f"{base}..HEAD"` -- the two-dot diff reverses `main`'s tag into a removal."""
    _commit({"s/stack.tm.hcl": _stack("[]")}, "base")
    _git("checkout", "-q", "-b", "pr")
    _commit({"s/stack.tm.hcl": _stack("[]", "renamed")}, "edit")
    _git("checkout", "-q", "main")
    base = _commit({"s/stack.tm.hcl": _stack('["env/dev"]')}, "tag on main")
    _git("checkout", "-q", "pr")
    assert bm.lost_env_tags(base, {"s": set()}) == {}


def test_lost_env_tags_reads_only_the_stacks_own_terramate_files(git_repo):
    """A child stack's retag and the parent's `.tf` losing `"env/prod"` both remove an `env/`
    tag literal under the parent's directory. Mutation: pathspec `":(glob)**"` -- the `.tf`
    removal lands on `stacks/app`."""
    base = _commit(
        {
            "stacks/app/stack.tm.hcl": _stack("[]"),
            "stacks/app/main.tf": 'locals {\n  key = "env/prod"\n}\n',
            "stacks/app/child/stack.tm.hcl": _stack('["env/dev"]', "child"),
        },
        "base",
    )
    _git("checkout", "-q", "-b", "pr")
    _commit(
        {
            "stacks/app/main.tf": "locals {\n}\n",
            "stacks/app/child/stack.tm.hcl": _stack('["env/prod"]', "child"),
        },
        "retag child",
    )
    assert bm.lost_env_tags(base, {"stacks/app": set()}) == {}


def test_lost_env_tags_ignores_an_env_prefixed_backend_key(git_repo):
    """An untagged stack's `.tm.hcl` drops a backend key under `env/`, not a tag literal.
    Mutation: widen the capture to `"env/([^"]*)"` -- `s` appears."""
    key = '  key = "env/${global.x}/state"\n'
    base = _commit({"s/stack.tm.hcl": _stack("[]"), "s/backend.tm.hcl": key}, "base")
    _git("checkout", "-q", "-b", "pr")
    _commit({"s/backend.tm.hcl": "\n"}, "drop key")
    assert bm.lost_env_tags(base, {"s": set()}) == {}


def test_lost_env_tags_names_each_env_removed_from_a_stack_that_keeps_another(git_repo):
    """One line holds two literals; the head keeps `dev-eu`. Mutations: drop the head filter
    -- `dev-eu` appears; `.search` instead of `.finditer` -- only the first literal is read,
    the head carries it, and nothing is named."""
    base = _commit({"s/stack.tm.hcl": _stack('["env/dev-eu", "env/prod"]')}, "base")
    _git("checkout", "-q", "-b", "pr")
    _commit({"s/stack.tm.hcl": _stack('["env/dev-eu"]')}, "untag prod")
    assert bm.lost_env_tags(base, {"s": {"dev-eu"}}) == {"s": ["prod"]}


def test_lost_env_tags_ignores_a_reformatted_tag_line(git_repo):
    """The tag line is removed and re-added with other spacing. Mutation: drop the head filter
    -- both names appear."""
    base = _commit({"s/stack.tm.hcl": _stack('["env/dev-eu", "env/prod"]')}, "base")
    _git("checkout", "-q", "-b", "pr")
    _commit({"s/stack.tm.hcl": _stack('[ "env/dev-eu",  "env/prod" ]')}, "reformat")
    assert bm.lost_env_tags(base, {"s": {"dev-eu", "prod"}}) == {}


def test_lost_env_tags_ignores_an_added_env_literal(git_repo):
    """A tagged stack adds a comment holding `"env/legacy"`, an environment it does not carry.
    Mutation: drop the `line.startswith("-")` filter -- `legacy` appears."""
    base = _commit({"s/stack.tm.hcl": _stack('["env/dev-eu"]')}, "base")
    _git("checkout", "-q", "-b", "pr")
    _commit({"s/stack.tm.hcl": _stack('["env/dev-eu"]') + '# was "env/legacy"\n'}, "comment")
    assert bm.lost_env_tags(base, {"s": {"dev-eu"}}) == {}


def test_merge_base_is_the_branch_point_of_a_branch_behind_its_base(git_repo):
    """`main` moves past the branch point. Mutation: return `base` unchanged -- red."""
    point = _commit({"s/stack.tm.hcl": _stack("[]")}, "base")
    _git("checkout", "-q", "-b", "pr")
    _commit({"s/stack.tm.hcl": _stack("[]", "renamed")}, "edit")
    _git("checkout", "-q", "main")
    base = _commit({"t/stack.tm.hcl": _stack("[]", "t")}, "moved on main")
    _git("checkout", "-q", "pr")
    assert bm.merge_base(base) == point


def test_merge_base_refuses_a_head_with_no_common_history(git_repo):
    """`base` is an orphan commit: `git merge-base` exits 1 with no output. Mutation: answer
    exit 1 with the other refusal -- red."""
    _commit({"s/stack.tm.hcl": _stack("[]")}, "head")
    _git("checkout", "-q", "--orphan", "other")
    base = _commit({"t/stack.tm.hcl": _stack("[]", "t")}, "orphan")
    _git("checkout", "-q", "main")
    with pytest.raises(SystemExit) as exc_info:
        bm.merge_base(base)
    assert str(exc_info.value) == (
        f"::error::the pull request's head shares no history with its base {base}, so shipmate "
        "cannot tell which stacks the pull request changes. Rebase the branch onto the base, or "
        "recreate it from the base, and push."
    )


def test_merge_base_names_gits_error_for_a_base_missing_from_the_clone(git_repo):
    """`git merge-base` fails for an unknown object; git's own wording is not pinned. Mutation:
    answer every failure with the no-history refusal -- red."""
    _commit({"s/stack.tm.hcl": _stack("[]")}, "head")
    base = "1" * 40
    with pytest.raises(SystemExit) as exc_info:
        bm.merge_base(base)
    text = str(exc_info.value)
    prefix = (
        f"::error::could not resolve the merge base of the pull request's base {base} and its "
        "head: "
    )
    suffix = (
        ". Re-run the workflow; if it fails again, the base commit is not reachable from this "
        "checkout."
    )
    assert text.startswith(prefix)
    assert text.endswith(suffix)
    assert text[len(prefix) : -len(suffix)].strip()
    assert "shares no history" not in text


@pytest.mark.parametrize(
    ("returncode", "stderr", "cause"),
    [
        (128, "fatal: bad ref a%20b\nhint: try again\n", "fatal: bad ref a%20b | hint: try again"),
        (128, "", "git merge-base exited 128 with no message"),
        (-9, "\n", "git merge-base exited -9 with no message"),
    ],
    ids=["stderr", "empty-stderr", "signal"],
)
def test_merge_base_names_the_git_cause_raw_on_one_line(monkeypatch, returncode, stderr, cause):
    """`run_annotated` escapes on print, so the refusal stays raw. Mutations: escape it with
    `command_data` -- `stderr` red (`%2520`); join with a space -- `stderr` red; drop the
    no-message fallback -- `empty-stderr` and `signal` red."""
    monkeypatch.setattr(
        bm.subprocess,
        "run",
        lambda args, **kw: subprocess.CompletedProcess(args, returncode, "", stderr),
    )
    with pytest.raises(SystemExit) as exc_info:
        bm.merge_base("b" * 40)
    assert str(exc_info.value) == (
        f"::error::could not resolve the merge base of the pull request's base {'b' * 40} and "
        f"its head: {cause}. Re-run the workflow; if it fails again, the base commit is not "
        "reachable from this checkout."
    )


def test_detect_lists_changed_stacks_from_the_branch_point(git_repo, monkeypatch):
    """A branch behind its base, through `main()` with the real `merge_base` and `_list_stacks`:
    `main` changed `b` after the branch point, the branch changed `a`. Mutation: pass the raw
    base to `compute_cells` -- `-B` names the base tip."""
    _commit({"a/stack.tm.hcl": _stack('["env/dev"]', "a")}, "a")
    point = _commit({"b/stack.tm.hcl": _stack('["env/dev"]', "b")}, "b")
    _git("checkout", "-q", "-b", "pr")
    head = _commit({"a/stack.tm.hcl": _stack('["env/dev"]', "a2")}, "edit a")
    _git("checkout", "-q", "main")
    base = _commit({"b/stack.tm.hcl": _stack('["env/dev"]', "b2")}, "edit b")
    _git("checkout", "-q", "pr")
    list_calls = []
    _run_main(
        monkeypatch,
        git_repo,
        {**_PR_ENV, "SHIPMATE_BASE_SHA": base},
        head_sha=head,
        stacks={"a": ["env/dev"]},
        list_calls=list_calls,
        merge_base=bm.merge_base,
    )
    assert list_calls == [["terramate", "list", "--changed", "-B", point]]


_LOST_ENV_WARNING = (
    "::warning::1 stack(s) lost their last env/* tag in this pull request, so shipmate no "
    "longer plans or deploys them: stacks/gone. If that is deliberate (CONTRACT.md §Taking a "
    "stack out of CI), nothing is wrong; otherwise restore the tag."
)
_REMOVED_ENV_LINE = '@@ -3 +3 @@\n-  tags = ["env/dev"]\n+  tags = []\n'
_REMOVED_PROD_LINE = '@@ -3 +3 @@\n-  tags = ["env/dev-eu", "env/prod"]\n+  tags = ["env/dev-eu"]\n'
_PR_ENV = {
    "GITHUB_EVENT_NAME": "pull_request",
    "GITHUB_REPOSITORY": "acme/iac",
    "SHIPMATE_HEAD_REPO": "acme/iac",
    "SHIPMATE_BASE_SHA": "b" * 40,
}


def _warnings(capsys):
    return [line for line in capsys.readouterr().out.splitlines() if line.startswith("::warning::")]


def test_detect_warns_about_a_stack_that_lost_its_last_env_tag(monkeypatch, tmp_path, capsys):
    """`stacks/app`, in the tree, changed without touching a tag. Mutations: drop the call in
    `main`; let the pair warning include unmanaged stacks -- `stacks/gone / dev` is named
    too."""
    _run_main(
        monkeypatch,
        tmp_path,
        _PR_ENV,
        head_sha="cafe1234",
        unmanaged=["stacks/gone"],
        diffs={"stacks/gone": _REMOVED_ENV_LINE, "stacks/app": ""},
    )
    assert _warnings(capsys) == [_LOST_ENV_WARNING]


def test_main_counts_no_local_stack_as_unmanaged(monkeypatch, tmp_path):
    """Every changed stack is local: the plan comment's unmanaged line reads this output.
    Mutation: build `named` from the unfiltered unmanaged list in `main` -- count 1."""
    outputs, _ = _run_main(
        monkeypatch,
        tmp_path,
        _PR_ENV,
        head_sha="cafe1234",
        stacks={"a": ["shipmate/local"]},
    )
    assert json.loads(outputs["unmanaged"]) == {"count": 0, "paths": []}


def test_detect_draws_no_lost_tag_warning_for_a_stack_made_local(monkeypatch, tmp_path, capsys):
    """`stacks/lab` swaps its last `env/*` tag for `shipmate/local`; `stacks/gone` drops its
    tag and stays unmanaged. Mutation: pass the unfiltered unmanaged list to
    `warn_lost_env_tags` -- `stacks/lab` is named too."""
    _run_main(
        monkeypatch,
        tmp_path,
        _PR_ENV,
        head_sha="cafe1234",
        stacks={"stacks/lab": ["shipmate/local"], "stacks/gone": []},
        diffs={
            "stacks/lab": '@@ -3 +3 @@\n-  tags = ["env/dev"]\n+  tags = ["shipmate/local"]\n',
            "stacks/gone": _REMOVED_ENV_LINE,
        },
    )
    assert _warnings(capsys) == [_LOST_ENV_WARNING]


def test_the_lost_tag_warning_keeps_a_percent_in_a_stack_path(monkeypatch, tmp_path, capsys):
    """A stack path is a directory name and can hold `%`: escaped as workflow-command data, the
    warning shows it verbatim. `env-config`'s three warnings go through the same escaper but
    have no such case: `validate_structure` runs first, and its `_check_entries` holds every name
    they print to the lowercase tag charset (entry names through `check_env_name`, `needs`
    through `validate_env_name_list`, workloads through `_check_workloads`' `_is_workload`), and
    any refusal there raises before the warnings print.

    Mutation: drop `command_data` from the last-tag warning's `print` -- red."""
    _run_main(
        monkeypatch,
        tmp_path,
        _PR_ENV,
        head_sha="cafe1234",
        unmanaged=["stacks/c100%"],
        diffs={"stacks/c100%": _REMOVED_ENV_LINE, "stacks/app": ""},
    )
    assert _warnings(capsys) == [
        (
            "::warning::1 stack(s) lost their last env/* tag in this pull request, so shipmate "
            "no longer plans or deploys them: stacks/c100%25. If that is deliberate (CONTRACT.md "
            "§Taking a stack out of CI), nothing is wrong; otherwise restore the tag."
        )
    ]


def test_detect_warns_about_each_env_a_tagged_stack_lost(monkeypatch, tmp_path, capsys):
    """`stacks/app` keeps `dev-eu` and loses `prod`. Mutation: delete the pair warning's
    `print`."""
    _run_main(
        monkeypatch,
        tmp_path,
        _PR_ENV,
        head_sha="cafe1234",
        diffs={"stacks/app": _REMOVED_PROD_LINE},
    )
    assert _warnings(capsys) == [
        (
            "::warning::1 stack / environment pair(s) lost their env/* tag in this pull request, "
            "so "
            "shipmate no longer plans or deploys them: stacks/app / prod. If that is deliberate "
            "(CONTRACT.md §Taking a stack out of CI), nothing is wrong; otherwise restore the tag."
        )
    ]


def test_detect_diffs_every_scanned_stack_once(monkeypatch, tmp_path):
    """`stacks/app` is in the tree in two environments. Mutation: call `lost_env_tags` once
    per tree row -- three diffs."""
    diff_calls = []
    _run_main(
        monkeypatch,
        tmp_path,
        _PR_ENV,
        cells=(("stacks/app", "dev-eu"), ("stacks/app", "prod"), ("stacks/db", "dev-eu")),
        head_sha="cafe1234",
        unmanaged=["stacks/gone"],
        diffs={"stacks/gone": "", "stacks/app": "", "stacks/db": ""},
        diff_calls=diff_calls,
    )
    assert diff_calls == [
        [
            "git",
            "diff",
            "-U0",
            "--no-renames",
            f"{'b' * 40}...HEAD",
            "--",
            ":(glob)**/*.tm.hcl",
            ":(glob)**/*.tm",
        ]
    ]


@pytest.mark.parametrize(
    "env",
    [
        {"SHIPMATE_SWEEP": "true"},
        {"SHIPMATE_BASE_SHA": "0" * 40},
        {"SHIPMATE_BASE_SHA": ""},
    ],
    ids=["sweep", "zero-base", "no-base"],
)
def test_detect_runs_no_diff_without_a_pull_request_base(monkeypatch, tmp_path, capsys, env):
    """A drift sweep, a force-push or first push, and a run with no base; `stacks/app` loses
    `prod` too, so both warnings are reachable. Mutation: drop each clause of the guard in turn
    -- its case diffs and warns."""
    diff_calls = []
    outputs, _ = _run_main(
        monkeypatch,
        tmp_path,
        {**_PR_ENV, **env},
        head_sha="cafe1234",
        unmanaged=["stacks/gone"],
        diffs={"stacks/gone": _REMOVED_ENV_LINE, "stacks/app": _REMOVED_PROD_LINE},
        diff_calls=diff_calls,
    )
    assert diff_calls == []
    assert "::warning::" not in capsys.readouterr().out
    assert outputs["lost"] == ""


def test_detect_diffs_from_the_merge_base_of_base_and_head(monkeypatch, tmp_path):
    """Mutations: pass the raw base to `compute_cells` -- red; pass it to `warn_lost_env_tags`
    -- red."""
    resolved, diff_calls = [], []

    def merge_base(base):
        resolved.append(base)
        return "c" * 40

    _, called = _run_main(
        monkeypatch,
        tmp_path,
        _PR_ENV,
        head_sha="cafe1234",
        diffs={"stacks/app": ""},
        diff_calls=diff_calls,
        merge_base=merge_base,
    )
    assert resolved == ["b" * 40]
    assert called == [(False, "c" * 40, "")]
    assert diff_calls == [
        [
            "git",
            "diff",
            "-U0",
            "--no-renames",
            f"{'c' * 40}...HEAD",
            "--",
            ":(glob)**/*.tm.hcl",
            ":(glob)**/*.tm",
        ]
    ]


@pytest.mark.parametrize(
    "env",
    [
        {"SHIPMATE_SWEEP": "true"},
        {"SHIPMATE_BASE_SHA": "0" * 40},
        {"SHIPMATE_BASE_SHA": ""},
    ],
    ids=["sweep", "zero-base", "no-base"],
)
def test_detect_resolves_no_merge_base_without_a_pull_request_base(monkeypatch, tmp_path, env):
    """The sweep case keeps `_PR_ENV`'s base. Mutation: drop each clause of the guard in turn --
    its case resolves one."""
    resolved = []
    _run_main(
        monkeypatch,
        tmp_path,
        {**_PR_ENV, **env},
        head_sha="cafe1234",
        diffs={"stacks/app": ""},
        merge_base=lambda base: resolved.append(base) or base,
    )
    assert resolved == []


def test_detect_writes_the_lost_pairs_and_leaves_out_an_untagged_stack(monkeypatch, tmp_path):
    """`stacks/gone` lost its last tag, so it is the unmanaged line's; `stacks/app` keeps
    `dev-eu` and loses `prod`. Mutation: drop `if s not in untagged` from the pairs --
    `stacks/gone / dev` is written too. Mutation: write `lost=` empty -- red."""
    outputs, _ = _run_main(
        monkeypatch,
        tmp_path,
        _PR_ENV,
        head_sha="cafe1234",
        unmanaged=["stacks/gone"],
        diffs={"stacks/gone": _REMOVED_ENV_LINE, "stacks/app": _REMOVED_PROD_LINE},
    )
    assert outputs["lost"] == '{"count": 1, "pairs": ["stacks/app / prod"]}'


def test_detect_writes_no_lost_pair_when_none_was_untagged(monkeypatch, tmp_path):
    """Mutation: write `lost=` empty whenever `pairs` is falsy -- the zero count is lost."""
    outputs, _ = _run_main(
        monkeypatch, tmp_path, _PR_ENV, head_sha="cafe1234", diffs={"stacks/app": ""}
    )
    assert outputs["lost"] == '{"count": 0, "pairs": []}'


def test_detect_writes_the_first_ten_lost_pairs_sorted(monkeypatch, tmp_path):
    """Eleven tree stacks, listed in reverse, each lose `prod`. Mutation: `UNMANAGED_NAMES + 1`
    at the `lost` output -- `stacks/t10 / prod` is written. Mutation: iterate `head_envs`
    unsorted in `lost_env_tags` -- `stacks/t10 / prod` leads."""
    stacks = [f"stacks/t{i:02}" for i in range(11)]
    outputs, _ = _run_main(
        monkeypatch,
        tmp_path,
        _PR_ENV,
        cells=tuple((s, "dev-eu") for s in reversed(stacks)),
        head_sha="cafe1234",
        diffs=dict.fromkeys(stacks, _REMOVED_PROD_LINE),
    )
    assert json.loads(outputs["lost"]) == {
        "count": 11,
        "pairs": [f"{s} / prod" for s in stacks[:10]],
    }


def test_detect_names_the_first_ten_lost_stacks(monkeypatch, tmp_path, capsys):
    """Mutation: `UNMANAGED_NAMES + 1` at the last-tag warning's `named(...)` call --
    `stacks/u10` is named."""
    lost = [f"stacks/u{i:02}" for i in range(11)]
    _run_main(
        monkeypatch,
        tmp_path,
        _PR_ENV,
        cells=(),
        head_sha="cafe1234",
        unmanaged=lost,
        diffs=dict.fromkeys(lost, _REMOVED_ENV_LINE),
    )
    assert _warnings(capsys) == [
        (
            "::warning::11 stack(s) lost their last env/* tag in this pull request, so shipmate no "
            f"longer plans or deploys them: {', '.join(lost[:10])}, and 1 more. If that is "
            "deliberate (CONTRACT.md §Taking a stack out of CI), nothing is wrong; otherwise "
            "restore the tag."
        )
    ]


def test_detect_names_the_first_ten_lost_pairs(monkeypatch, tmp_path, capsys):
    """Eleven tree stacks each lose `prod`. Mutation: `UNMANAGED_NAMES + 1` at the pair
    warning's `named(...)` call -- `stacks/t10 / prod` is named."""
    stacks = [f"stacks/t{i:02}" for i in range(11)]
    _run_main(
        monkeypatch,
        tmp_path,
        _PR_ENV,
        cells=tuple((s, "dev-eu") for s in stacks),
        head_sha="cafe1234",
        diffs=dict.fromkeys(stacks, _REMOVED_PROD_LINE),
    )
    assert _warnings(capsys) == [
        (
            "::warning::11 stack / environment pair(s) lost their env/* tag in this pull request, "
            "so shipmate no longer plans or deploys them: "
            f"{', '.join(f'{s} / prod' for s in stacks[:10])}, and 1 more. If that is deliberate "
            "(CONTRACT.md §Taking a stack out of CI), nothing is wrong; otherwise restore the tag."
        )
    ]


@pytest.mark.parametrize(
    ("env", "head_sha"), [(_PLAN_ENV, "a" * 40), (_DRIFT_ENV, None)], ids=["plan", "sweep"]
)
@pytest.mark.parametrize(
    ("table", "expected"),
    [({"layout": "folder"}, "10"), ({"layout": "folder", "max_parallel": "3"}, "3")],
    ids=["default", "set"],
)
def test_detect_writes_the_tables_max_parallel(
    monkeypatch, tmp_path, env, head_sha, table, expected
):
    """The plan and drift matrices' `max-parallel` read this output, and an unwritten one fails
    the run.

    Mutations: delete the `max_parallel` write -- every row loses the key; write
    `DEFAULT_MAX_PARALLEL` instead of `max_parallel(table)` -- the "set" rows read 10."""
    outputs, _ = _run_main(monkeypatch, tmp_path, env, head_sha=head_sha, table=table)
    assert outputs["count"] == "1"  # Not vacuous: a cell exists.
    assert outputs["max_parallel"] == expected


def test_a_refused_max_parallel_writes_no_matrix(monkeypatch, tmp_path):
    """A refusal after `matrix=` and `empty=false` were written leaves a matrix job reading an
    empty cap, which fails the run with an expression error instead of the refusal.

    Mutation: write `matrix`, `empty` and `count` in their own `write_outputs` call before
    `cap = ec.max_parallel(table)` -- they are written before the refusal."""

    def refuse(table):
        raise SystemExit("::error::max_parallel is '0'; refused")

    monkeypatch.setattr(bm.ec, "max_parallel", refuse)
    _annotated(monkeypatch)
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, tmp_path, _PLAN_ENV, head_sha="a" * 40)
    assert github_outputs(tmp_path / "out.txt") == {
        "refusal": "max_parallel is '0'; refused",
    }


def _base_moved_a_module(git_repo):
    """`main` changes only `modules/m` after `pr` branched, `pr` changes `stacks/app`: the shape
    the overlap probe ran. Returns (merge base, base tip), with `pr` checked out."""
    since = _commit({"modules/m/main.tf": "v1\n", "stacks/app/main.tf": "app\n"}, "common")
    _git("checkout", "-q", "-b", "pr")
    _commit({"stacks/app/main.tf": "app edit\n"}, "pr")
    _git("checkout", "-q", "main")
    tip = _commit({"modules/m/main.tf": "v2\n"}, "module only")
    _git("checkout", "-q", "pr")
    return since, tip


def _merge_stub(monkeypatch, listing):
    """Stubs Terramate: each listing calls `listing(tree)` and answers what it returns. Returns
    the terramate argvs seen."""
    real, seen = bm._run, []

    def _run(args):
        if args[0] != "terramate":
            return real(args)
        seen.append(args)
        return listing(pathlib.Path(args[2]))

    monkeypatch.setattr(bm, "_run", _run)
    return seen


def test_base_changed_stacks_lists_head_stacks_wired_to_a_module_the_base_changed(
    git_repo, monkeypatch
):
    """The branch wires `stacks/s` to `modules/m`; the base edits `main.tf` and deletes `old.tf`
    there. Only the branch's tree knows `stacks/s` consumes the module; the one listing, run on
    the merge of the base into HEAD as a child of HEAD, against HEAD, finds it. The caller's
    checkout is untouched.

    Mutation: drop the merge listing from the union -- the result is empty, red.
    Mutation: commit the base tip's tree instead of the merged one -- `mod.tf` is missing, red.
    Mutation: drop `-p <head>` from `commit-tree` -- the merge commit has no parent, red.
    Mutation: list the merge with `-B <merge base>` -- the argv, red.
    Mutation: restore a listing on a worktree of the base tip -- that worktree's `HEAD^` is not
    the head, red.
    Mutation: drop the `finally` worktree removal -- two worktrees are left, red."""
    _commit(
        {"modules/m/main.tf": "v1\n", "modules/m/old.tf": "old\n", "stacks/s/main.tf": "s\n"},
        "common",
    )
    _git("checkout", "-q", "-b", "pr")
    head = _commit({"stacks/s/mod.tf": "wire\n"}, "wire")
    _git("checkout", "-q", "main")
    tip = _commit({"modules/m/main.tf": "v2\n", "modules/m/old.tf": None}, "base")
    _git("checkout", "-q", "pr")
    index = _git("ls-files", "-s")

    def listing(tree):
        assert _git("-C", str(tree), "rev-parse", "HEAD^").strip() == head
        assert (tree / "modules/m/main.tf").is_file()
        assert (tree / "modules/m/main.tf").read_text() == "v2\n"
        assert not (tree / "modules/m/old.tf").exists()
        assert (tree / "stacks/s/mod.tf").is_file()
        assert (tree / "stacks/s/mod.tf").read_text() == "wire\n"
        return "stacks/s\n"

    seen = _merge_stub(monkeypatch, listing)
    assert bm.base_changed_stacks(tip, {"stacks/s"}) == {"stacks/s"}
    assert seen == [["terramate", "-C", seen[0][2], "list", "--changed", "-B", head]]
    assert len(_git("worktree", "list").splitlines()) == 1
    assert _git("rev-parse", "HEAD").strip() == head
    assert _git("ls-files", "-s") == index
    assert _git("status", "--porcelain", "--untracked-files=all") == ""


def test_base_changed_stacks_fetches_a_base_tip_the_clone_lacks(git_repo, monkeypatch, tmp_path):
    """A base tip pushed after the checkout is absent from the clone; it is fetched from `origin`
    by sha before the merge base. Mutation: drop the fetch -- `merge_base` refuses, red."""
    since, tip = _base_moved_a_module(git_repo)
    _git("config", "uploadpack.allowAnySHA1InWant", "true")
    clone = tmp_path / "clone"
    _git("clone", "-q", "--no-local", "--single-branch", "-b", "pr", str(git_repo), str(clone))
    monkeypatch.chdir(clone)
    assert subprocess.run(["git", "cat-file", "-e", tip], capture_output=True).returncode
    real = bm._run
    monkeypatch.setattr(
        bm, "_run", lambda args: "stacks/app\n" if args[0] == "terramate" else real(args)
    )
    assert bm.base_changed_stacks(tip, {"stacks/app"}) == {"stacks/app"}


def test_base_changed_stacks_adds_candidate_stacks_the_base_deleted_or_renamed(
    git_repo, monkeypatch
):
    """Terramate lists only the stacks the base tip still has, so a stack the base deleted or
    moved away is read from the diff. `stacks/go` is a prefix of `stacks/gone` but no parent.

    Mutation: drop `| gone` from the return -- red.
    Mutation: drop `"R"` from the statuses -- `stacks/old` missing, red.
    Mutation: add the new path of a rename (`fields[i + 2]`) -- `stacks/old` missing, red.
    Mutation: `p.startswith(s)` without the `/` -- `stacks/go` added, red.
    Mutation: step 2 fields past a rename (`i += 2`) -- `stacks/zed`, deleted after it in path
    order, missing, red."""
    files = {f"stacks/{s}/main.tf": f"{s}\n" for s in ("gone", "old", "kept", "zed")}
    _commit(files, "common")
    _git("checkout", "-q", "-b", "pr")
    _commit({"stacks/kept/main.tf": "kept edit\n"}, "pr")
    _git("checkout", "-q", "main")
    _git("mv", "stacks/old", "stacks/new")
    tip = _commit({"stacks/gone/main.tf": None, "stacks/zed/main.tf": None}, "delete, rename")
    _git("checkout", "-q", "pr")
    real = bm._run
    monkeypatch.setattr(bm, "_run", lambda args: "" if args[0] == "terramate" else real(args))
    candidates = {"stacks/gone", "stacks/old", "stacks/kept", "stacks/go", "stacks/zed"}
    assert bm.base_changed_stacks(tip, candidates) == {"stacks/gone", "stacks/old", "stacks/zed"}


def test_base_changed_stacks_holds_the_root_stack_for_a_path_no_other_stack_holds(
    git_repo, monkeypatch
):
    """git reports `main.tf`, never `./main.tf`, so the root stack `.` is matched by exclusion.

    Mutation: drop the `stack == "."` branch of `_inside` (`.` never matches, red). Mutation:
    return True for `.` (a deletion inside `stacks/app` alone holds the root too, red)."""
    _commit({"main.tf": "root\n", "stacks/app/main.tf": "app\n", "stacks/app/x.tf": "x\n"}, "c")
    _git("checkout", "-q", "-b", "pr")
    _commit({"stacks/app/x.tf": "x edit\n"}, "pr")
    _git("checkout", "-q", "main")
    only_app = _commit({"stacks/app/main.tf": None}, "delete in a child stack")
    root_too = _commit({"main.tf": None}, "delete in the root")
    _git("checkout", "-q", "pr")
    real = bm._run
    monkeypatch.setattr(bm, "_run", lambda args: "" if args[0] == "terramate" else real(args))
    assert bm.base_changed_stacks(only_app, {".", "stacks/app"}) == {"stacks/app"}
    assert bm.base_changed_stacks(root_too, {".", "stacks/app"}) == {".", "stacks/app"}


@pytest.mark.parametrize(
    ("base_change", "held"),
    [
        pytest.param({"stacks/a/main.tf": "base edit\n"}, {"stacks/b"}, id="base-edited"),
        pytest.param(
            {"stacks/a/main.tf": None},
            "::error::change detection on the merge of the base into the branch failed: "
            "CONFLICT (rename/delete): stacks/a/main.tf renamed to stacks/b/main.tf in {head}, "
            "but deleted in {tip}. Update the branch from the base and push.",
            id="base-deleted",
        ),
    ],
)
def test_base_changed_stacks_holds_a_stack_the_branch_renamed(
    git_repo, monkeypatch, base_change, held
):
    """The branch renames `stacks/a` to `stacks/b`; the base commits `base_change`. git's merge
    carries a base edit of `stacks/a/main.tf` to `stacks/b/main.tf`, so the listing finds
    `stacks/b`; a base deletion of it is a merge conflict and refuses.

    Mutation: commit the base tip's tree instead of the merged one -- `stacks/a` is back, red.
    Mutation: ignore merge-tree's exit status -- base-deleted returns instead of refusing,
    red."""
    _commit({"stacks/a/main.tf": "a\n", "other/x.tf": "x\n"}, "common")
    _git("checkout", "-q", "-b", "pr")
    _git("mv", "stacks/a", "stacks/b")
    head = _commit({}, "rename a to b")
    _git("checkout", "-q", "main")
    tip = _commit(base_change, "base edit")
    _git("checkout", "-q", "pr")

    def listing(tree):
        assert not (tree / "stacks/a").exists()
        assert (tree / "stacks/b/main.tf").read_text() == "base edit\n"
        return "stacks/b\n"

    _merge_stub(monkeypatch, listing)
    if isinstance(held, set):
        assert bm.base_changed_stacks(tip, {"stacks/b"}) == held
        return
    with pytest.raises(SystemExit) as e:
        bm.base_changed_stacks(tip, {"stacks/b"})
    assert e.value.code == held.format(head=head, tip=tip)


def test_moved_from_reads_a_copy_record_as_three_fields(monkeypatch):
    """A repository with `diff.renames=copies` makes `--find-renames` report copies too.

    Mutation: step 3 fields past an `R` alone (`i += 3 if status == "R" else 2`) -- the copy's
    new path is read as a status and `stacks/b`'s deletion is missed, red. Mutation: count a
    copy's source (`status in ("D", "R", "C")`) -- `stacks/a` is added, red."""
    out = "C075\0stacks/a/x.tf\0stacks/c/x.tf\0D\0stacks/b/y.tf\0"
    monkeypatch.setattr(bm, "_run", lambda args: out)
    assert bm._moved_from(bm._diff_records("s", "u")) == {"stacks/b/y.tf"}


def test_base_changed_stacks_holds_nothing_when_the_branch_holds_the_base_change_and_more(
    git_repo, monkeypatch
):
    """The base fixes `modules/m/main.tf`; the branch cherry-picks the fix, edits the file
    further and wires `stacks/s` to the module. The merged tree is HEAD's, so nothing is held and
    nothing is committed, checked out or listed.

    Mutation: drop the early return -- the merge is listed, red. Mutation: commit the base tip's
    tree instead of the merged one -- it is listed, red."""
    _commit({"modules/m/main.tf": "a\nb\nc\nd\ne\n", "stacks/s/main.tf": "s\n"}, "c")
    _git("checkout", "-q", "-b", "pr")
    _git("checkout", "-q", "main")
    tip = _commit({"modules/m/main.tf": "a\nB\nc\nd\ne\n"}, "base fix")
    _git("checkout", "-q", "pr")
    # `-x`: a cherry-pick in the same second as its source would reproduce the source's sha.
    _git("cherry-pick", "-x", tip)
    _commit({"modules/m/main.tf": "a\nB\nc\nd\nE\n", "stacks/s/mod.tf": "wire\n"}, "more")
    listed = _merge_stub(monkeypatch, lambda tree: "stacks/s\n")
    seen = _spy_git(monkeypatch)
    assert bm.base_changed_stacks(tip, {"stacks/s"}) == set()
    assert listed == []
    assert ["git", "rev-parse", "HEAD"] in seen  # Not vacuous: the spy records.
    assert [a for a in seen if "commit-tree" in a or "worktree" in a] == []


def test_base_changed_stacks_holds_no_stack_for_a_removal_the_branch_holds(git_repo, monkeypatch):
    """The base deletes `stacks/s/old.tf` and edits `other/x.tf`; the branch cherry-picks the
    deletion. The merge removes nothing from HEAD, so `stacks/s` is not held, though the base
    deleted a file in it since the merge base.

    Mutation: read the removed files from the merge base to the base tip instead of from HEAD to
    the merge -- `stacks/s` is held, red."""
    files = {"stacks/s/main.tf": "s\n", "stacks/s/old.tf": "old\n", "other/x.tf": "x\n"}
    _commit(files, "common")
    _git("checkout", "-q", "-b", "pr")
    _git("checkout", "-q", "main")
    drop = _commit({"stacks/s/old.tf": None}, "base deletes")
    tip = _commit({"other/x.tf": "x edit\n"}, "base edits")
    _git("checkout", "-q", "pr")
    _git("cherry-pick", "-x", drop)

    def listing(tree):
        assert (tree / "other/x.tf").read_text() == "x edit\n"  # Not vacuous: the merge is listed.
        return ""

    _merge_stub(monkeypatch, listing)
    assert bm.base_changed_stacks(tip, {"stacks/s"}) == set()


def test_base_changed_stacks_refuses_a_failed_head_read_as_a_merge_step(git_repo, monkeypatch):
    """A failed `git rev-parse HEAD` is a merge step: it refuses with the merge refusal, its
    stderr as the cause, and the update remedy.

    Mutation: read `HEAD` before the `try` through `_run` -- the bare command failure, red."""
    _, tip = _base_moved_a_module(git_repo)
    real = bm.run

    def run(args, **kw):
        if args == ["git", "rev-parse", "HEAD"]:
            raise SystemExit("command failed (128): git rev-parse HEAD\nfatal: bad HEAD")
        return real(args, **kw)

    monkeypatch.setattr(bm, "run", run)
    monkeypatch.setattr(bm, "_run", lambda args: bm.run(args) if args[0] == "git" else "")
    with pytest.raises(SystemExit) as e:
        bm.base_changed_stacks(tip, {"stacks/app"})
    assert e.value.code == (
        "::error::change detection on the merge of the base into the branch failed: fatal: bad "
        "HEAD. Update the branch from the base and push."
    )


def _spy_git(monkeypatch):
    """Records every argv `bm.run` and `bm._run` are given; returns the list."""
    seen = []
    for name in ("run", "_run"):
        real = getattr(bm, name)

        def spy(args, _real=real, **kw):
            seen.append(args)
            return _real(args, **kw)

        monkeypatch.setattr(bm, name, spy)
    return seen


def test_base_changed_stacks_refuses_a_merge_conflict_before_any_worktree(git_repo, monkeypatch):
    """The branch deletes `stacks/a`; the base edits `stacks/a/main.tf`. The merge conflicts, the
    refusal names git's conflict line, and no worktree was ever added.

    Mutation: ignore merge-tree's exit status -- the merge is listed and nothing refuses, red.
    Mutation: read the cause from stderr alone -- `git merge-tree exited 1`, red.
    Mutation: run merge-tree after the `worktree add` -- the add is recorded, red."""
    _commit({"stacks/a/main.tf": "a\n", "stacks/b/main.tf": "b\n"}, "common")
    _git("checkout", "-q", "-b", "pr")
    head = _commit({"stacks/a/main.tf": None}, "delete a")
    _git("checkout", "-q", "main")
    tip = _commit({"stacks/a/main.tf": "a edit\n"}, "base edit")
    _git("checkout", "-q", "pr")
    seen = _spy_git(monkeypatch)
    with pytest.raises(SystemExit) as e:
        bm.base_changed_stacks(tip, {"stacks/b"})
    assert e.value.code == (
        "::error::change detection on the merge of the base into the branch failed: "
        f"CONFLICT (modify/delete): stacks/a/main.tf deleted in {head} and modified in {tip}.  "
        f"Version {tip} of stacks/a/main.tf left in tree. Update the branch from the base and "
        "push."
    )
    assert ["git", "rev-parse", "HEAD"] in seen  # Not vacuous: the spy records.
    assert [a for a in seen if "worktree" in a] == []


_LINES = "".join(f"line {n}\n" for n in range(8))
_EXACT = ({"modules/m/main.tf": "m\n"}, None)
# Four renames past a limit of 1, each with an appended line so no exact match pairs them.
_INEXACT = (
    {f"modules/m/f{n}.tf": f"{n}\n{_LINES}" for n in range(1, 5)},
    {f"modules/m2/g{n}.tf": f"{n}\n{_LINES}edit\n" for n in range(1, 5)},
)


@pytest.mark.parametrize(
    ("scope", "config", "files"),
    [
        pytest.param(None, None, _EXACT, id="default"),
        pytest.param("repo", "merge.renames=false", _EXACT, id="merge-renames-false"),
        pytest.param("repo", "diff.renames=false", _EXACT, id="diff-renames-false"),
        pytest.param("repo", "merge.renameLimit=1", _INEXACT, id="rename-limit-1"),
        pytest.param("global", "merge.renames=false", _EXACT, id="global-merge-renames-false"),
    ],
)
def test_base_changed_stacks_refuses_a_file_added_under_a_renamed_directory(
    git_repo, monkeypatch, scope, config, files
):
    """The branch renames `modules/m` to `modules/m2` and wires `stacks/s` to it; the base adds
    `modules/m/variables.tf`. git's rename detection would move the file into `modules/m2`; the
    pinned `merge.directoryRenames=conflict` makes it a conflict instead. A runner config that
    skips rename detection would leave the file under `modules/m` and hold nothing; the pins
    keep the conflict.

    Mutation: `-c merge.directoryRenames=true` -- the merge succeeds and holds `stacks/s`, red.
    Mutation: drop `-c merge.renames=true` -- the three `false` rows merge cleanly, red.
    Mutation: drop `-c merge.renameLimit=32767` -- the rename-limit row merges cleanly, red."""
    common, renamed = files
    if scope == "repo":
        _git("config", *config.split("="))
    elif scope == "global":
        key, value = config.split("=")
        section, name = key.split(".")
        (git_repo.parent / "gitconfig").write_text(f"[{section}]\n\t{name} = {value}\n")
    _commit({**common, "stacks/s/main.tf": "s\n"}, "common")
    _git("checkout", "-q", "-b", "pr")
    if renamed is None:
        _git("mv", "modules/m", "modules/m2")
    else:
        _commit({**dict.fromkeys(common), **renamed}, "rename")
    head = _commit({"stacks/s/mod.tf": "wire m2\n"}, "rename and wire")
    _git("checkout", "-q", "main")
    tip = _commit({"modules/m/variables.tf": "v\n"}, "base adds")
    _git("checkout", "-q", "pr")
    _merge_stub(monkeypatch, lambda tree: "stacks/s\n")
    with pytest.raises(SystemExit) as e:
        bm.base_changed_stacks(tip, {"stacks/s"})
    assert e.value.code == (
        "::error::change detection on the merge of the base into the branch failed: "
        "CONFLICT (file location): modules/m/variables.tf added in "
        f"{tip} inside a directory that was renamed in {head}, suggesting it should perhaps be "
        "moved to modules/m2/variables.tf. Update the branch from the base and push."
    )


def test_merge_pins_are_the_rename_content_driver_and_isolation_settings():
    """0 is git's default rename limit, not unlimited, so the pin is the highest limit.

    Mutation: `merge.renameLimit=0` -- red. Mutation: drop `GIT_ATTR_NOSYSTEM` -- red."""
    assert bm._MERGE_PINS == (
        "-c",
        "merge.renames=true",
        "-c",
        "merge.directoryRenames=conflict",
        "-c",
        "merge.renameLimit=32767",
        "-c",
        "merge.default=text",
        "-c",
        "core.attributesFile=",
        "-c",
        "safe.directory=*",
    )
    assert {
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": bm.os.devnull,
        "GIT_ATTR_NOSYSTEM": "1",
    } == bm._MERGE_ENV


_KEEP_HEAD = ("merge.keep-head.driver", "cp %B %A")
# Per route: `git config` argvs run before the commits, env set after them; `{tmp}` is tmp_path.
_DRIVER_ROUTES = {
    "default-driver": ([_KEEP_HEAD, ("merge.default", "keep-head")], {}),
    "attributes-file": ([_KEEP_HEAD, ("core.attributesFile", "{tmp}/attributes")], {}),
    "text-driver-redefined": ([], {"HOME": "{tmp}/home"}),
    "xdg-attributes": ([], {"XDG_CONFIG_HOME": "{tmp}/xdg"}),
    "env-config-count": (
        [],
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "merge.text.driver",
            "GIT_CONFIG_VALUE_0": "cp %B %A",
        },
    ),
    "env-config-parameters": ([], {"GIT_CONFIG_PARAMETERS": "'merge.text.driver'='cp %B %A'"}),
    "replace-ref-base": ([], {"GIT_REPLACE_REF_BASE": "refs/evil/"}),
    "dubious-owner": (
        [("--global", "safe.directory", "*")],
        {"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"},
    ),
}


@pytest.mark.parametrize("route", list(_DRIVER_ROUTES))
def test_base_changed_stacks_ignores_a_runner_merge_driver(git_repo, monkeypatch, tmp_path, route):
    """Git config selects a driver that keeps the branch's side of every file: repository config
    as the default driver or through an attributes file, the runner's `~/.gitconfig` or
    inherited `GIT_CONFIG_*` env by redefining the `text` driver. An XDG attributes file selects
    `binary`, which conflicts. The base prepends a line to a module the branch wires `stacks/s`
    to, the branch appends one; the merge still holds both edits, so `stacks/s` is held. An
    inherited `GIT_REPLACE_REF_BASE` replaces the base's blob with the merge base's, so the base
    side looks unchanged and the merge resolves to HEAD's tree. `dubious-owner` uses no driver:
    the workspace belongs to another user and only the runner's global `safe.directory` admits
    it.

    Mutation: drop `merge.default=text` -- `default-driver` merges to HEAD's tree, red.
    Mutation: drop `core.attributesFile=` -- `attributes-file` merges to HEAD's tree and
    `xdg-attributes` conflicts, red.
    Mutation: drop `GIT_CONFIG_GLOBAL` -- `text-driver-redefined` merges to HEAD's tree, red.
    Mutation: pass `{**os.environ, **_MERGE_ENV}` -- `env-config-count`, `env-config-parameters`
    and `replace-ref-base` merge to HEAD's tree, red.
    Mutation: drop `safe.directory=*` -- `dubious-owner` merge-tree dies on ownership, red.
    Mutation: drop only `GIT_CONFIG*` and `GIT_ATTR*` -- `replace-ref-base` merges to HEAD's
    tree, red."""
    (tmp_path / "attributes").write_bytes(b"* merge=keep-head\n")
    (tmp_path / "xdg/git").mkdir(parents=True)
    (tmp_path / "xdg/git/attributes").write_bytes(b"* merge=binary\n")
    (tmp_path / "home").mkdir()
    (tmp_path / "home/.gitconfig").write_bytes(b'[merge "text"]\n\tdriver = cp %B %A\n')
    configs, env = _DRIVER_ROUTES[route]
    for argv in configs:
        _git("config", *(a.format(tmp=tmp_path.as_posix()) for a in argv))
    _commit({"modules/m/main.tf": "a\nb\nc\n", "stacks/s/main.tf": "s\n"}, "common")
    _git("checkout", "-q", "-b", "pr")
    head = _commit({"modules/m/main.tf": "a\nb\nc\nhead\n", "stacks/s/mod.tf": "wire\n"}, "wire")
    _git("checkout", "-q", "main")
    tip = _commit({"modules/m/main.tf": "base\na\nb\nc\n"}, "base")
    _git("checkout", "-q", "pr")
    if route == "replace-ref-base":
        _git(
            "update-ref",
            f"refs/evil/{_git('rev-parse', f'{tip}:modules/m/main.tf').strip()}",
            _git("rev-parse", f"{tip}~1:modules/m/main.tf").strip(),
        )
    for k, v in env.items():
        monkeypatch.setenv(k, v.format(tmp=tmp_path.as_posix()))
        if k.startswith("GIT_TEST_"):
            # merge-tree drops every inherited `GIT_*`; a real foreign owner is not env-borne.
            monkeypatch.setitem(bm._MERGE_ENV, k, v)

    def listing(tree):
        assert (tree / "modules/m/main.tf").read_text() == "base\na\nb\nc\nhead\n"
        return "stacks/s\n"

    seen = _merge_stub(monkeypatch, listing)
    assert bm.base_changed_stacks(tip, {"stacks/s"}) == {"stacks/s"}
    assert seen == [["terramate", "-C", seen[0][2], "list", "--changed", "-B", head]]


def test_base_changed_stacks_ignores_an_inherited_attribute_source(git_repo, monkeypatch):
    """`GIT_ATTR_SOURCE` names a tree whose `.gitattributes` selects `union`, which would resolve
    the base's and the branch's edits of one line silently; the merge ignores it and refuses
    the conflict.

    Mutation: pass `{**os.environ, **_MERGE_ENV}` -- the union merge holds `stacks/s`, red."""
    _commit({"modules/m/main.tf": "a\n", "stacks/s/main.tf": "s\n"}, "common")
    _git("checkout", "-q", "-b", "attrs")
    _commit({".gitattributes": "* merge=union\n"}, "attrs")
    _git("checkout", "-q", "main")
    _git("checkout", "-q", "-b", "pr")
    _commit({"modules/m/main.tf": "head\n", "stacks/s/mod.tf": "wire\n"}, "wire")
    _git("checkout", "-q", "main")
    tip = _commit({"modules/m/main.tf": "base\n"}, "base")
    _git("checkout", "-q", "pr")
    monkeypatch.setenv("GIT_ATTR_SOURCE", "attrs")
    _merge_stub(monkeypatch, lambda tree: "stacks/s\n")
    with pytest.raises(SystemExit) as e:
        bm.base_changed_stacks(tip, {"stacks/s"})
    assert "CONFLICT (content): Merge conflict in modules/m/main.tf" in str(e.value.code)


def _merge_tree_answers(monkeypatch, result):
    """Stubs `bm.subprocess.run` for merge-tree alone, answering `result(argv)`. Returns the
    keyword arguments of each merge-tree call."""
    real, calls = subprocess.run, []

    def run(args, **kw):
        if "merge-tree" not in args:
            return real(args, **kw)
        calls.append(kw)
        return result(args)

    monkeypatch.setattr(bm.subprocess, "run", run)
    return calls


def test_base_changed_stacks_refuses_a_failed_merge_with_its_stderr(git_repo, monkeypatch):
    """A merge-tree exit other than 1 is no conflict; its cause is git's stderr.

    Mutation: read the cause from stdout's `CONFLICT` lines on every nonzero exit, dropping the
    stderr fallback -- `git merge-tree exited 128`, red."""
    _, tip = _base_moved_a_module(git_repo)
    failed = subprocess.CompletedProcess([], 128, "", "fatal: bad object x\n")
    _merge_tree_answers(monkeypatch, lambda args: failed)
    _merge_stub(monkeypatch, lambda tree: "stacks/app\n")
    with pytest.raises(SystemExit) as e:
        bm.base_changed_stacks(tip, {"stacks/app"})
    assert e.value.code == (
        "::error::change detection on the merge of the base into the branch failed: fatal: bad "
        "object x. Update the branch from the base and push."
    )


def test_base_changed_stacks_runs_merge_tree_in_the_c_locale(git_repo, monkeypatch):
    """git localizes `CONFLICT`, which the refusal's cause is read by, so merge-tree runs with
    `LC_ALL=C` on top of the caller's environment.

    Mutation: drop the `env=` argument -- the call carries no environment, red.
    Mutation: pass `{"LC_ALL": "C"}` alone -- `PATH` is missing, red."""
    _, tip = _base_moved_a_module(git_repo)
    calls = _merge_tree_answers(
        monkeypatch, lambda args: subprocess.CompletedProcess(args, 1, "", "fatal: x\n")
    )
    with pytest.raises(SystemExit):
        bm.base_changed_stacks(tip, {"stacks/app"})
    assert len(calls) == 1
    env = calls[0].get("env") or {}
    assert env.get("LC_ALL") == "C"
    assert env.get("PATH") == bm.os.environ["PATH"]


def test_base_changed_stacks_refuses_a_merge_tree_that_printed_no_tree(git_repo, monkeypatch):
    """Exit 0 with nothing on stdout names no tree to commit; it refuses, never raises an
    `IndexError` or lists anything.

    Mutation: `_merge_tree_failure` returns None on every exit 0 -- `IndexError`, red."""
    _, tip = _base_moved_a_module(git_repo)
    _merge_tree_answers(monkeypatch, lambda args: subprocess.CompletedProcess(args, 0, "", ""))
    seen = _merge_stub(monkeypatch, lambda tree: "stacks/app\n")
    with pytest.raises(SystemExit) as e:
        bm.base_changed_stacks(tip, {"stacks/app"})
    assert e.value.code == (
        "::error::change detection on the merge of the base into the branch failed: git "
        "merge-tree printed no tree. Update the branch from the base and push."
    )
    assert seen == []


def test_base_changed_stacks_refuses_when_the_merge_listing_fails(git_repo, monkeypatch):
    """A failed listing refuses with Terramate's stderr, never holds: a hold would name the base
    as the cause for stacks it never touched.

    Mutation: catch the failure and return the `gone` stacks only -- no refusal, red.
    Mutation: use the error's first line instead of its stderr -- the cause names the command,
    red.
    Mutation: remove the worktree only on success -- two worktrees are left, red."""
    _, tip = _base_moved_a_module(git_repo)

    def listing(tree):
        raise SystemExit(
            f"::error::command failed (1): terramate -C {tree} list --changed -B x\n"
            "Error: duplicated ID found on stacks"
        )

    _merge_stub(monkeypatch, listing)
    with pytest.raises(SystemExit) as e:
        bm.base_changed_stacks(tip, {"stacks/app"})
    assert e.value.code == (
        "::error::change detection on the merge of the base into the branch failed: Error: "
        "duplicated ID found on stacks. Update the branch from the base and push."
    )
    assert len(_git("worktree", "list").splitlines()) == 1


def test_base_changed_stacks_lists_nothing_when_the_base_changed_nothing(git_repo, monkeypatch):
    """A base tip that changed no file since the merge base has nothing to merge into HEAD, so
    merge-tree does not run.

    Mutation: drop the empty-diff skip -- a merge-tree call, red."""
    _commit({"stacks/s/main.tf": "s\n"}, "common")
    _git("checkout", "-q", "-b", "pr")
    _commit({"stacks/s/main.tf": "s edit\n"}, "pr")
    _git("checkout", "-q", "main")
    _git("commit", "-q", "--allow-empty", "-m", "empty")
    tip = _git("rev-parse", "HEAD").strip()
    _git("checkout", "-q", "pr")
    seen = _merge_stub(monkeypatch, lambda tree: "stacks/s\n")
    merges = _merge_tree_answers(monkeypatch, lambda args: pytest.fail("merge-tree ran"))
    assert bm.base_changed_stacks(tip, {"stacks/s"}) == set()
    assert seen == []
    assert merges == []


def test_base_changed_stacks_refuses_a_base_tip_it_cannot_fetch(git_repo, monkeypatch):
    """The remedy is a push from the updated branch, never a bare `shipmate apply`, which runs
    no overlap check.

    Mutation: skip the fetch's exit status -- `merge_base` refuses instead, red. Mutation: end
    with "Push again, or comment `shipmate apply`." -- red."""
    _commit({"stacks/app/main.tf": "app\n"}, "common")
    monkeypatch.setattr(bm, "_run", lambda args: pytest.fail(f"ran {args}"))
    missing = "f" * 40
    with pytest.raises(SystemExit) as e:
        bm.base_changed_stacks(missing, set())
    assert e.value.code == (
        f"::error::could not fetch the base tip {missing} (fatal: 'origin' does not appear to be "
        "a git repository | fatal: Could not read from remote repository. | Please make sure you "
        "have the correct access rights | and the repository exists.). Update the branch from "
        "the base and push."
    )


def test_base_changed_stacks_names_the_update_remedy_when_the_merge_base_fails(monkeypatch):
    """Mutation: drop `retry=_BASE_REMEDY` from the `merge_base` call -- "Re-run the
    workflow", red."""
    tip = "c" * 40

    def run(args, **kw):
        code = 0 if args[1] == "cat-file" else 128
        return subprocess.CompletedProcess(args, code, "", "fatal: bad object\n")

    monkeypatch.setattr(bm.subprocess, "run", run)
    with pytest.raises(SystemExit) as e:
        bm.base_changed_stacks(tip, set())
    assert e.value.code == (
        f"::error::could not resolve the merge base of the pull request's base {tip} and its "
        "head: fatal: bad object. Update the branch from the base and push; if it fails again, "
        "the base commit is not reachable from this checkout."
    )

import json

import pytest
from _detect_fixtures import APP_ID, spy_env_config, stub_read_table
from _detect_fixtures import check_run as _check
from _loader import github_outputs, load_script

ud = load_script("unlock-detect")


def _unlock_env(monkeypatch, tmp_path, table=None, reads=None, **overrides):
    """Env for a main() run; returns the GITHUB_OUTPUT path. No review decision is set.

    `shipmate unlock <env>` applies no plan and consumes no plan artifact: a lock outlives the
    run that stranded it, so those artifacts may be long expired."""
    out = tmp_path / "out"
    env = {
        "GITHUB_REPOSITORY": "acme/iac",
        "SHIPMATE_ENV": "dev-eu",
        "SHIPMATE_HEAD_SHA": "a" * 40,
        "GITHUB_OUTPUT": str(out),
    }
    env.update(overrides)
    monkeypatch.delenv("SHIPMATE_REVIEW_DECISION", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    stub_read_table(monkeypatch, [ud.bm.ec], base=table, reads=reads)
    return out


_DEV_EU_PENDING_CHECKS = [
    _check(name=f"apply / {stack} / dev-eu", status="in_progress", conclusion=None)
    for stack in ("stacks/app", "stacks/dns", "stacks/db")
]


def _stub_unlock_tree(monkeypatch, cells, checks=None):
    """Stub the tag walk and the check-run listing; returns the kwargs `env_membership` was
    called with.

    Only the walk is stubbed. The real `build_matrix` turns its output into cells, so the
    matrix-limit and slug-collision guards it carries stay on the unlock path instead of being
    stubbed out of it.

    `checks` are stubbed as the raw JSONL `gh` emits, not as a set of names, so the queue's
    membership rule itself is under test rather than assumed: a construction that asks the
    wrong question of the same listing reddens here. Default: every dev-eu cell has a pending
    check."""
    seen = {}

    def _membership(all_stacks=False, base="", check_names=True):
        seen.update(all_stacks=all_stacks, base=base, check_names=check_names)
        stacks_by_env, tags_by_stack = {}, {}
        for c in cells:
            stacks_by_env.setdefault(c["environment"], []).append(c["stack"])
            tags = tags_by_stack.setdefault(c["stack"], [])
            for tag in (f"env/{c['environment']}", f"workload/{c['workload']}"):
                if tag not in tags:
                    tags.append(tag)
        return stacks_by_env, tags_by_stack

    runs = _DEV_EU_PENDING_CHECKS if checks is None else checks
    monkeypatch.setattr(ud.bm, "_run", lambda args: "\n".join(json.dumps(r) for r in runs))
    monkeypatch.setenv("SHIPMATE_APP_ID", APP_ID)
    monkeypatch.setattr(ud.bm, "env_membership", _membership)
    return seen


_DEV_EU_CELLS = [
    {"stack": "stacks/app", "environment": "dev-eu", "workload": "app"},
    {"stack": "stacks/dns", "environment": "dev-eu", "workload": "net"},
    {"stack": "stacks/db", "environment": "dev-eu", "workload": "app"},
    {"stack": "stacks/app", "environment": "prod-eu", "workload": "app"},
]


def test_unlock_queue_is_the_pending_cells_of_the_target_env(monkeypatch, tmp_path):
    """stacks/app has a pending check and is queued; stacks/dns has a completed check and is
    not; stacks/db has no check at all and is not queued either. Every queued cell takes a real
    state lock and may force-break one, so a stack this pull request never planned must not be
    in range. The foreign-App pending check on stacks/db must not enrol it.

    Mutation: pass `require_env_tag=False` to `env_membership` -- TypeError.
    Mutation: pass `"15368"` instead of `os.environ["SHIPMATE_APP_ID"]` to `app_runs` --
    stacks/db is queued and stacks/app is not."""
    out = _unlock_env(monkeypatch, tmp_path)
    seen = _stub_unlock_tree(
        monkeypatch,
        _DEV_EU_CELLS,
        [
            _check(name="apply / stacks/app / dev-eu", status="in_progress", conclusion=None),
            _check(name="apply / stacks/dns / dev-eu"),
            _check(
                name="apply / stacks/db / dev-eu",
                status="in_progress",
                conclusion=None,
                app={"id": 15368},
            ),
        ],
    )
    ud.main()
    # all_stacks=True is the point: a cell whose plan artifacts expired long ago is exactly the
    # cell that can hold a stranded lock.
    assert seen == {"all_stacks": True, "base": "", "check_names": False}
    assert json.loads(github_outputs(out)["cells"]) == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "app",
            "role_arn": "",
            "cred_region": "",
            "tf_vars": {},
            "config_path": "apply",
            "env_binding": "dev-eu-apply",
        },
    ]


def test_unlock_empty_queue_warns_that_nothing_was_probed(monkeypatch, tmp_path, capsys):
    """An empty queue is legitimate, and since the queue narrowed to the cells that have a
    pending check it is the normal outcome for the case the runbook names. It must not be a
    silent green run.

    Mutation: write `cells` only for a non-empty queue -- the output file is empty."""
    out = _unlock_env(monkeypatch, tmp_path)
    _stub_unlock_tree(monkeypatch, _DEV_EU_CELLS, [_check(name="apply / stacks/app / dev-eu")])
    ud.main()
    # Whole file: unlock.yml reads `cells` alone, so nothing else is written.
    assert out.read_text(encoding="utf-8") == "cells=[]\n"
    assert (
        "::warning::no cell in dev-eu has a pending apply check, so no lock was "
        "probed; a lock on a cell whose check already completed, or on a stack "
        "applied out of band, is released out of band; see the state-lock "
        "section of docs/troubleshooting.md." in capsys.readouterr().out.splitlines()
    )


def test_unlock_non_empty_queue_does_not_warn(monkeypatch, tmp_path, capsys):
    # The other half: the warning is about an empty queue, not decoration on every unlock run.
    _unlock_env(monkeypatch, tmp_path)
    _stub_unlock_tree(monkeypatch, _DEV_EU_CELLS)
    ud.main()
    out = capsys.readouterr().out
    assert "cells=3 pending=3" in out  # Not vacuous: there is a queue.
    assert "no cell in dev-eu has a pending apply check" not in out


def test_unlock_is_not_capped_by_the_whole_tree_matrix_limit(monkeypatch, tmp_path):
    """build_matrix refuses a cell set above the GHA matrix limit, and over a whole-tree walk
    that ceiling counts every stack x every environment. Built for all envs and filtered
    afterwards, a repository past the limit could never unlock any environment however short
    its queue, and the refusal would tell the operator to split a pull request that does not
    exist. Only the target env's cells are built, so the ceiling bounds what the matrix holds."""
    out = _unlock_env(monkeypatch, tmp_path)
    _stub_unlock_tree(
        monkeypatch,
        [
            {
                "stack": "stacks/app",
                "environment": f"env-{i}",
                "workload": "app",
            }
            for i in range(ud.bm.MATRIX_LIMIT + 10)
        ]
        + [_DEV_EU_CELLS[0]],
    )
    ud.main()
    assert json.loads(github_outputs(out)["cells"]) == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "app",
            "role_arn": "",
            "cred_region": "",
            "tf_vars": {},
            "config_path": "apply",
            "env_binding": "dev-eu-apply",
        }
    ]


def test_unlock_emits_no_wave_array_with_any_member(monkeypatch, tmp_path):
    """unlock.yml's matrix reads `cells` alone; a wave output here would be an apply matrix
    with nothing reading it.

    Mutation: write an `envlevel0_waves=` line from `main` -- the filter reddens."""
    out = _unlock_env(monkeypatch, tmp_path)
    _stub_unlock_tree(monkeypatch, _DEV_EU_CELLS)
    ud.main()
    parsed = github_outputs(out)
    assert len(json.loads(parsed["cells"])) == 3  # Not vacuous: there is a queue.
    wave_keys = [k for k in parsed if "waves" in k or "empty" in k]
    assert wave_keys == []


def test_unlock_does_not_refuse_an_unreviewed_pr(monkeypatch, tmp_path):
    """An approval reviews a diff and unlock applies none, and `scripts/authorize` makes the
    same call at comment time. `_unlock_env` sets no review decision.

    Mutation: call `ad.refuse_unreviewed(env, table, "")` in `main` -- the run refuses."""
    out = _unlock_env(monkeypatch, tmp_path)
    _stub_unlock_tree(monkeypatch, _DEV_EU_CELLS)
    ud.main()
    assert len(json.loads(github_outputs(out)["cells"])) == 3


def test_unlock_passes_the_whole_tree_workload_map(monkeypatch, tmp_path):
    """Mutation: pass `set(stacks_by_env)` at `main`'s `env_config` call -- the spy records
    the environment set."""
    _unlock_env(monkeypatch, tmp_path)
    _stub_unlock_tree(
        monkeypatch,
        [*_DEV_EU_CELLS, {"stack": "stacks/web", "environment": "dev-us", "workload": "web"}],
    )
    seen = spy_env_config(monkeypatch, ud.bm)
    ud.main()
    assert seen == [
        {
            "dev-eu": frozenset({"app", "net"}),
            "prod-eu": frozenset({"app"}),
            "dev-us": frozenset({"web"}),
        }
    ]


def test_unlock_path_loads_the_environment_table_exactly_once(monkeypatch, tmp_path):
    """`env_config` is where an unlock cell gets its identity and its credentials, so the
    invariant is one read, not none: two reads of the default branch can disagree if the
    branch moves mid-run.

    Mutation: read `bm.ec.read_table()` in `main` before `env_config` without passing it on --
    the count becomes 2."""
    reads = []
    _unlock_env(monkeypatch, tmp_path, reads=reads)
    _stub_unlock_tree(monkeypatch, _DEV_EU_CELLS)
    ud.main()
    assert len(reads) == 1


def test_unlock_notice_names_the_env_and_counts(monkeypatch, tmp_path, capsys):
    _unlock_env(monkeypatch, tmp_path)
    _stub_unlock_tree(
        monkeypatch,
        _DEV_EU_CELLS,
        [
            _check(name="apply / stacks/app / dev-eu", status="in_progress", conclusion=None),
            _check(name="apply / stacks/dns / dev-eu"),
            _check(name="apply / stacks/db / dev-eu", status="queued", conclusion=None),
        ],
    )
    ud.main()
    assert (
        f"::notice title=unlock-detect::env=dev-eu head={'a' * 40} cells=3 pending=2"
        in capsys.readouterr().out.splitlines()
    )


@pytest.mark.parametrize(
    ("name", "value", "error"),
    [
        (
            "SHIPMATE_HEAD_SHA",
            "../x",
            "::error::SHIPMATE_HEAD_SHA must be a 40-char lowercase hex SHA (got: '../x')",
        ),
        ("SHIPMATE_ENV", "", "::error::SHIPMATE_ENV is empty; this path is single-env."),
        (
            "SHIPMATE_ENV",
            "dev.eu",
            "::error::SHIPMATE_ENV 'dev.eu' is not an environment name: lowercase letters, "
            "digits, '-' and '_', with no quotes, spaces or path separators.",
        ),
    ],
    ids=["head-sha", "empty-env", "dotted-env"],
)
def test_main_validates_its_inputs_before_any_read(monkeypatch, tmp_path, name, value, error):
    """The head SHA is interpolated into the check-run URL, and the env names the queue.

    Mutation: delete the `ad.validate_head_sha` call -- the head-sha case reaches the tree walk.
    Mutation: delete the `ad.validate_env` call -- both env cases reach the tree walk."""
    _unlock_env(monkeypatch, tmp_path, **{name: value})
    monkeypatch.setenv("SHIPMATE_APP_ID", APP_ID)

    def _boom(*a, **kw):
        raise AssertionError("unlock-detect read the tree or the API before validating")

    monkeypatch.setattr(ud.bm, "_run", _boom)
    monkeypatch.setattr(ud.ad, "_check_run_lines", _boom)
    with pytest.raises(SystemExit) as exc_info:
        ud.main()
    assert str(exc_info.value) == error


def _stub_one_pending_check(monkeypatch):
    """One pending App-authored check, for `stacks/app / dev-eu`, as the raw JSONL `gh` emits."""
    line = json.dumps(_check(name="apply / stacks/app / dev-eu", status="queued", conclusion=None))
    monkeypatch.setattr(ud.bm, "_run", lambda args: line)
    monkeypatch.setenv("SHIPMATE_APP_ID", APP_ID)


def test_unlock_tolerates_an_untagged_stack_elsewhere_in_the_tree(monkeypatch, tmp_path):
    # Through the real env_membership: a refusal of `stacks/orphan` would make unlock
    # unavailable for every environment, precisely when the pipeline is already degraded
    # enough to strand a lock.
    out = _unlock_env(monkeypatch, tmp_path)
    _stub_one_pending_check(monkeypatch)
    monkeypatch.setattr(
        ud.bm, "_list_stacks", lambda all_stacks, base: ["stacks/app", "stacks/orphan"]
    )
    monkeypatch.setattr(
        ud.bm, "_tags", lambda s: ["env/dev-eu", "workload/app"] if s == "stacks/app" else []
    )
    ud.main()
    assert json.loads(github_outputs(out)["cells"]) == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "app",
            "role_arn": "",
            "cred_region": "",
            "tf_vars": {},
            "config_path": "apply",
            "env_binding": "dev-eu-apply",
        }
    ]


def test_unlock_tolerates_an_unusable_env_tag_elsewhere_in_the_tree(monkeypatch, tmp_path):
    """Through the real env_membership: `stacks/other` carries `env/a.b`, which refuses every
    other run, and must not make `dev-eu` unable to unlock.

    Mutation: pass `check_names=True` from `main` -- the tag refusal fires."""
    out = _unlock_env(monkeypatch, tmp_path)
    _stub_one_pending_check(monkeypatch)
    monkeypatch.setattr(
        ud.bm, "_list_stacks", lambda all_stacks, base: ["stacks/app", "stacks/other"]
    )
    monkeypatch.setattr(
        ud.bm,
        "_tags",
        lambda s: ["env/dev-eu", "workload/app"] if s == "stacks/app" else ["env/a.b"],
    )
    ud.main()
    assert json.loads(github_outputs(out)["cells"]) == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "app",
            "role_arn": "",
            "cred_region": "",
            "tf_vars": {},
            "config_path": "apply",
            "env_binding": "dev-eu-apply",
        }
    ]


def test_unlock_ignores_two_workload_tags_on_a_stack_outside_the_queue(monkeypatch, tmp_path):
    """stacks/other is in dev-eu with two `workload/*` tags and no apply check, so it is not
    released and must not refuse the unlock of stacks/app.

    Mutation: build the cells from the whole of `stacks_by_env` in `main` -- the
    two-workload-tag refusal fires."""
    out = _unlock_env(monkeypatch, tmp_path)
    _stub_one_pending_check(monkeypatch)
    monkeypatch.setattr(
        ud.bm,
        "env_membership",
        lambda **kw: (
            {"dev-eu": ["stacks/app", "stacks/other"]},
            {
                "stacks/app": ["env/dev-eu", "workload/app"],
                "stacks/other": ["env/dev-eu", "workload/a", "workload/b"],
            },
        ),
    )
    ud.main()
    assert json.loads(github_outputs(out)["cells"]) == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "app",
            "role_arn": "",
            "cred_region": "",
            "tf_vars": {},
            "config_path": "apply",
            "env_binding": "dev-eu-apply",
        }
    ]

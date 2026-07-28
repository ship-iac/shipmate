"""Pin rewriters: engine-internal and consumer-facing.

Text-level tests on tmp_path fixtures. The git-dependent selection logic
(which pins are stale) is already covered by test_internal_pins.py and
test_pin_status.py; what needs covering here is that the rewrite touches
exactly the intended refs and preserves everything else on the line --
trailing comments especially, since three engine refs carry one and a
consumer's pins carry the release annotation.
"""

import importlib.util
from importlib.machinery import SourceFileLoader

import pinrefs

_DEV = pinrefs.ROOT / "dev"

OLD = "a" * 40
NEW = "b" * 40
OTHER = "c" * 40

# A real commit in this repo: the main tip this branch forked from. The two
# main()-level tests need a SHA that survives `rev-parse --verify <sha>^{commit}`,
# because refusing an unresolvable SHA is the point of that check -- a placeholder
# would exit 3 before reaching the pin_status gate under test.
REAL = "4914d074df71f8c3d0b4ccb73a22c153cacaca7c"


def _load_cli(fname):
    loader = SourceFileLoader(fname.replace("-", "_").removesuffix(".py"), str(_DEV / fname))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ri = _load_cli("repin-internal.py")


def _repo(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


def test_rewrite_replaces_only_the_targeted_path_and_sha(tmp_path):
    root = _repo(
        tmp_path,
        {
            ".github/workflows/apply.yml": (
                f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n"
                f"      - uses: ship-iac/shipmate/actions/apply-cell@{OTHER}\n"
            )
        },
    )

    changed = ri.rewrite(root, {("actions/setup", OLD)}, NEW)

    text = (root / ".github/workflows/apply.yml").read_text(encoding="utf-8")
    assert changed == [(".github/workflows/apply.yml", 1)]
    assert f"actions/setup@{NEW}" in text
    assert f"actions/apply-cell@{OTHER}" in text  # untargeted ref untouched


def test_rewrite_preserves_a_trailing_comment(tmp_path):
    # Three engine refs carry "# pinned to a commit on main"; losing it on a
    # bump would be a silent doc regression.
    root = _repo(
        tmp_path,
        {
            "actions/apply-cell/action.yml": (
                f"      uses: ship-iac/shipmate/actions/state@{OLD} # pinned to a commit on main\n"
            )
        },
    )

    ri.rewrite(root, {("actions/state", OLD)}, NEW)

    text = (root / "actions/apply-cell/action.yml").read_text(encoding="utf-8")
    assert text.strip().endswith("# pinned to a commit on main")
    assert f"actions/state@{NEW}" in text


def test_rewrite_counts_every_occurrence_in_a_file(tmp_path):
    root = _repo(
        tmp_path,
        {
            ".github/workflows/apply-env-level.yml": (
                f"  - uses: ship-iac/shipmate/actions/setup@{OLD}\n"
                f"  - uses: ship-iac/shipmate/actions/setup@{OLD}\n"
            )
        },
    )

    assert ri.rewrite(root, {("actions/setup", OLD)}, NEW) == [
        (".github/workflows/apply-env-level.yml", 2)
    ]


def test_rewrite_reports_no_change_when_nothing_matches(tmp_path):
    root = _repo(
        tmp_path,
        {".github/workflows/apply.yml": f"  - uses: ship-iac/shipmate/actions/setup@{OTHER}\n"},
    )

    assert ri.rewrite(root, {("actions/setup", OLD)}, NEW) == []


def test_targets_excludes_pins_whose_diff_could_not_be_verified(monkeypatch):
    # "error" (git failed) and "missing" (pin commit absent) mean we do not know
    # whether the pin is stale. Rewriting one would be a guess presented as a
    # fix -- the reason selection reads PinIssue.kind and never a message.
    refs = [
        ("actions/setup", OLD, "a.yml"),
        ("actions/state", OTHER, "b.yml"),
    ]
    monkeypatch.setattr(pinrefs, "release_baseline", lambda: NEW)
    monkeypatch.setattr(
        pinrefs,
        "pin_issues",
        lambda *a: [
            pinrefs.PinIssue("actions/setup", OLD, "a.yml", "stale"),
            pinrefs.PinIssue("actions/state", OTHER, "b.yml", "error", error="boom"),
        ],
    )

    targets, notes = ri._targets(refs, NEW, bump_all=False)

    assert targets == {("actions/setup", OLD)}
    assert len(notes) == 1
    assert "boom" in notes[0]


def test_rewrite_ignores_files_outside_the_two_source_shapes(tmp_path):
    # docs/ carries a grep example with a pin-shaped string; rewriting it would
    # corrupt documentation. Only workflows and action.yml files are sources.
    root = _repo(
        tmp_path,
        {
            "docs/releasing.md": f"pin example: ship-iac/shipmate/actions/setup@{OLD}\n",
            ".github/workflows/apply.yml": f"  - uses: ship-iac/shipmate/actions/setup@{OLD}\n",
        },
    )

    changed = ri.rewrite(root, {("actions/setup", OLD)}, NEW)

    assert changed == [(".github/workflows/apply.yml", 1)]
    assert OLD in (root / "docs/releasing.md").read_text(encoding="utf-8")


rc = _load_cli("repin-consumer.py")


def test_rewrite_consumer_bumps_every_engine_ref_regardless_of_path(tmp_path):
    # All-or-nothing by design: actions/summary creates the pending apply check
    # and actions/apply-cell (pinned inside apply-env-level.yml) completes it,
    # so a straddling pin pair makes one check name and looks for another.
    root = _repo(
        tmp_path,
        {
            ".github/workflows/plan.yml": (
                f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n"
                f"      - uses: ship-iac/shipmate/actions/summary@{OTHER}\n"
            ),
            ".github/workflows/apply.yml": (
                f"    uses: ship-iac/shipmate/.github/workflows/apply.yml@{OLD}\n"
            ),
        },
    )

    changed = rc.rewrite_consumer(root, NEW, None)

    plan = (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")
    apply_ = (root / ".github/workflows/apply.yml").read_text(encoding="utf-8")
    assert dict(changed) == {".github/workflows/plan.yml": 2, ".github/workflows/apply.yml": 1}
    assert plan.count(f"@{NEW}") == 2
    assert f"apply.yml@{NEW}" in apply_


def test_rewrite_consumer_sets_the_release_label_comment(tmp_path):
    root = _repo(
        tmp_path,
        {".github/workflows/plan.yml": f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n"},
    )

    rc.rewrite_consumer(root, NEW, "v0.2.0")

    text = (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")
    assert text.rstrip("\n").endswith(f"@{NEW} # v0.2.0")


def test_rewrite_consumer_replaces_a_stale_label_comment(tmp_path):
    root = _repo(
        tmp_path,
        {
            ".github/workflows/plan.yml": (
                f"      - uses: ship-iac/shipmate/actions/setup@{OLD} # v0.1.0\n"
            )
        },
    )

    rc.rewrite_consumer(root, NEW, "v0.2.0")

    text = (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")
    assert "v0.1.0" not in text
    assert text.rstrip("\n").endswith(f"@{NEW} # v0.2.0")


def test_rewrite_consumer_does_not_swallow_a_following_comment_line(tmp_path):
    # The trailing-comment capture must not cross a newline: with \s* it would
    # eat the standalone comment below and a --label rewrite would delete it,
    # joining the lines. Latent corruption in a tool whose only job is safe
    # mechanical rewriting.
    root = _repo(
        tmp_path,
        {
            ".github/workflows/plan.yml": (
                f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n"
                "      # keep this comment\n"
                "        with:\n"
            )
        },
    )

    rc.rewrite_consumer(root, NEW, "v0.2.0")

    text = (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")
    assert "      # keep this comment\n" in text
    assert f"actions/setup@{NEW} # v0.2.0\n" in text
    assert text.count("\n") == 3


def test_rewrite_consumer_leaves_third_party_pins_alone(tmp_path):
    root = _repo(
        tmp_path,
        {
            ".github/workflows/plan.yml": (
                f"      - uses: actions/checkout@{OTHER} # v7.0.1\n"
                f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n"
            )
        },
    )

    rc.rewrite_consumer(root, NEW, "v0.2.0")

    text = (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")
    assert f"actions/checkout@{OTHER} # v7.0.1" in text


def test_main_refuses_a_commit_that_is_not_safe_to_pin(tmp_path, capsys, monkeypatch):
    root = _repo(
        tmp_path,
        {".github/workflows/plan.yml": f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n"},
    )
    monkeypatch.setattr(
        rc,
        "pin_status",
        lambda _sha: [
            pinrefs.PinIssue("actions/apply-cell", OTHER, "apply-env-level.yml", "stale")
        ],
    )

    rc_code = rc.main(["--repo", str(root), "--sha", REAL])

    out = capsys.readouterr().out
    assert rc_code == 1
    assert "refusing" in out
    assert "apply-cell" in out
    # Nothing written.
    assert OLD in (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")


def test_main_force_overrides_the_refusal(tmp_path, capsys, monkeypatch):
    root = _repo(
        tmp_path,
        {".github/workflows/plan.yml": f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n"},
    )
    monkeypatch.setattr(
        rc,
        "pin_status",
        lambda _sha: [
            pinrefs.PinIssue("actions/apply-cell", OTHER, "apply-env-level.yml", "stale")
        ],
    )

    rc_code = rc.main(["--repo", str(root), "--sha", REAL, "--force"])

    assert rc_code == 0
    assert "overriding" in capsys.readouterr().out
    assert REAL in (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")


def test_main_rejects_a_nonexistent_full_length_sha(tmp_path, capsys):
    # `git rev-parse --verify <40-hex>` exits 0 for a SHA that does not exist --
    # only the ^{commit} peel rejects it. Without that peel a typo'd SHA resolves,
    # refs_at() on it yields nothing, pin_status() finds no issues, and the tool
    # cheerfully writes a pin that cannot resolve at runtime.
    root = _repo(
        tmp_path,
        {".github/workflows/plan.yml": f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n"},
    )

    assert rc.main(["--repo", str(root), "--sha", NEW]) == 3
    assert "does not resolve" in capsys.readouterr().out
    assert OLD in (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")

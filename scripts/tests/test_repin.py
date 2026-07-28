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

"""Pin rewriters: engine-internal and consumer-facing.

Text-level tests on tmp_path fixtures. Which pins are stale is git-dependent and already
covered by test_internal_pins.py and test_pin_status.py. What needs covering here is that the
rewrite touches exactly the intended refs and preserves everything else on the line --
trailing comments especially, since engine refs carry one and a consumer's pins carry the
release annotation.

The main()-level tests read real history. A shallow clone lacks the fixture commits, so those
tests would fail on an unrelated "does not resolve" exit 3 instead of exercising the gate
under test; the module-level skipif turns that into a loud skip, because a bare `assert` would
abort this module's collection and redden an unrelated scripts/ edit's run. CI checks out with
fetch-depth: 0, where the condition is always False.
"""

import pinrefs
import pytest
import repin_consumer as rc
import repin_internal as ri

OLD = "a" * 40
NEW = "b" * 40
OTHER = "c" * 40

# A real commit in this repo: the main tip this branch forked from. The main()-level tests need
# a SHA that survives `rev-parse --verify <sha>^{commit}`, because refusing an unresolvable SHA
# is the point of that check -- a placeholder exits 3 before reaching the pin_status gate.
REAL = "4914d074df71f8c3d0b4ccb73a22c153cacaca7c"

# The engine's own root commit. Its tree predates today's actions/ and .github/workflows/
# layout entirely, so refs_at() on it finds no shipmate self-references -- exactly the "commit
# whose pins cannot be judged" shape that must be refused rather than treated as clean.
NO_REFS_COMMIT = "52f6aad901fe634d6f99d9a499c3c6d25bb737f7"

_MISSING_FIXTURES = [sha for sha in (REAL, NO_REFS_COMMIT) if not pinrefs.commit_present(sha)]
pytestmark = pytest.mark.skipif(
    bool(_MISSING_FIXTURES),
    reason=(
        "history fixture commit(s) not in this clone: "
        + ", ".join(sha[:12] for sha in _MISSING_FIXTURES)
        + " -- these tests read real history; check out with fetch-depth: 0"
    ),
)


def _repo(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


def _rewrite(root, targets, new_sha):
    """Plan-then-commit, the pair each CLI's write path runs. Composed here rather than exported
    from the module: a one-call wrapper with no production caller would let these tests pass
    over an ordering the CLI never takes."""
    return ri._commit(root, ri._plan(root, targets, new_sha))


def _rewrite_consumer(root, new_sha, label):
    return rc._commit_consumer(root, rc._plan_consumer(root, new_sha, label))


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

    changed = _rewrite(root, {("actions/setup", OLD)}, NEW)

    text = (root / ".github/workflows/apply.yml").read_text(encoding="utf-8")
    assert changed == [(".github/workflows/apply.yml", 1)]
    assert f"actions/setup@{NEW}" in text
    assert f"actions/apply-cell@{OTHER}" in text  # The untargeted ref is untouched.


def test_rewrite_preserves_a_trailing_comment(tmp_path):
    # Engine refs carry "# pinned to a commit on main"; losing it on a bump would be a silent
    # doc regression.
    root = _repo(
        tmp_path,
        {
            "actions/apply-cell/action.yml": (
                f"      uses: ship-iac/shipmate/actions/state@{OLD} # pinned to a commit on main\n"
            )
        },
    )

    _rewrite(root, {("actions/state", OLD)}, NEW)

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

    assert _rewrite(root, {("actions/setup", OLD)}, NEW) == [
        (".github/workflows/apply-env-level.yml", 2)
    ]


def test_rewrite_reports_no_change_when_nothing_matches(tmp_path):
    root = _repo(
        tmp_path,
        {".github/workflows/apply.yml": f"  - uses: ship-iac/shipmate/actions/setup@{OTHER}\n"},
    )

    assert _rewrite(root, {("actions/setup", OLD)}, NEW) == []


def test_rewrite_writes_lf_not_crlf(tmp_path):
    # pathlib's default write_text(newline=None) translates every "\n" to os.linesep, so on
    # Windows a one-line pin bump flips the whole file to CRLF. read_text re-normalizes CRLF
    # back to "\n" on the way in and cannot catch it, so the assertion is on raw bytes.
    root = _repo(
        tmp_path,
        {".github/workflows/apply.yml": f"  - uses: ship-iac/shipmate/actions/setup@{OLD}\n"},
    )
    p = root / ".github/workflows/apply.yml"
    p.write_bytes(f"  - uses: ship-iac/shipmate/actions/setup@{OLD}\n".encode())

    _rewrite(root, {("actions/setup", OLD)}, NEW)

    assert b"\r\n" not in p.read_bytes()


def test_rewrite_preserves_existing_crlf(tmp_path):
    # Mirror image of test_rewrite_writes_lf_not_crlf: read_text(encoding="utf-8") normalizes
    # CRLF to "\n" on the way in, so a write_text emitting LF-only flips a CRLF-committed
    # workflow whole-file. Fails when write_text hardcodes newline="\n": 2 CRLFs become 0.
    root = _repo(
        tmp_path,
        {".github/workflows/apply.yml": f"  - uses: ship-iac/shipmate/actions/setup@{OLD}\n"},
    )
    p = root / ".github/workflows/apply.yml"
    crlf_body = f"  - uses: ship-iac/shipmate/actions/setup@{OLD}\r\n  - uses: x/y@{OTHER}\r\n"
    p.write_bytes(crlf_body.encode())
    before = p.read_bytes().count(b"\r\n")

    changed = _rewrite(root, {("actions/setup", OLD)}, NEW)

    after = p.read_bytes()
    assert changed == [(".github/workflows/apply.yml", 1)]
    assert after.count(b"\r\n") == before
    assert f"actions/setup@{NEW}".encode() in after


def test_targets_excludes_pins_whose_diff_could_not_be_verified(monkeypatch):
    # "error" (git failed) and "missing" (pin commit absent) both mean the pin's staleness is
    # unknown. Rewriting one would be a guess presented as a fix, which is why selection reads
    # PinIssue.kind and never a message.
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

    targets, notes, staleness_unknown = ri._targets(refs, NEW, bump_all=False)

    assert targets == {("actions/setup", OLD)}
    assert len(notes) == 1
    assert "boom" in notes[0]
    assert staleness_unknown is False


def test_targets_reports_staleness_unknown_when_no_mainline_resolves(monkeypatch):
    # release_baseline() returning None means no mainline ref resolved at all (shallow clone,
    # detached HEAD), which is not "checked, and nothing is stale". staleness_unknown is what
    # lets main() tell those apart instead of printing a verified "none stale" it never checked.
    monkeypatch.setattr(pinrefs, "release_baseline", lambda: None)

    targets, notes, staleness_unknown = ri._targets(
        [("actions/setup", OLD, "a.yml")], NEW, bump_all=False
    )

    assert targets == set()
    assert staleness_unknown is True
    assert "cannot tell which pins are stale" in notes[0]


def test_main_exits_three_when_no_internal_refs_are_found(capsys, monkeypatch):
    """refs_at() returning [] means the detection surface broke: workflows moved, the
    action.yml shape changed, the slug drifted. Fails when the `if not refs` refusal that
    returns 3 goes -- _targets() then reports "nothing to bump" and main() exits 0, which a
    release owner reads as convergence when the tool cannot see any pins at all."""
    monkeypatch.setattr(pinrefs, "refs_at", lambda *a, **k: [])

    code = ri.main(["--to", REAL])

    out = capsys.readouterr().out
    assert code == 3
    assert "regex or repo layout" in out


def test_main_reports_staleness_unknown_rather_than_none_stale(capsys, monkeypatch):
    # The message main() prints, not only _targets: when staleness could not be determined,
    # main() must not claim "none stale against the mainline" one line under a note saying the
    # opposite.
    monkeypatch.setattr(pinrefs, "release_baseline", lambda: None)
    monkeypatch.setattr(pinrefs, "refs_at", lambda *a, **k: [("actions/setup", OLD, "a.yml")])

    code = ri.main(["--to", REAL])

    out = capsys.readouterr().out
    assert code == 0
    assert "staleness could not be determined" in out
    assert "none stale against the mainline" not in out


def _planned_survivors(root, new_sha, targets=frozenset()):
    """The survivor scan as `_write_and_report` runs it: over the planned text of every
    candidate file, before anything is written. A working-tree read would cover a path the CLI
    never takes."""
    planned = ri._plan(root, set(targets), new_sha)
    return pinrefs.scan_survivors([(p.path, p.text) for p in planned], new_sha)


def test_all_survivor_scan_flags_a_tag_pinned_ref_left_behind(tmp_path):
    # --all promises to flatten every internal pin to one SHA. A tag-pinned ref is invisible to
    # REF, which matches only 40 lowercase hex, and so survives the rewrite untouched -- the
    # plan-level survivor scan is what must notice.
    root = _repo(
        tmp_path,
        {
            ".github/workflows/apply.yml": (
                "      - uses: ship-iac/shipmate/actions/setup@v0.1.0\n"
                f"      - uses: ship-iac/shipmate/actions/state@{NEW}\n"
            )
        },
    )

    survivors = _planned_survivors(root, NEW)

    assert len(survivors) == 1
    assert "actions/setup@v0.1.0" in survivors[0]


def test_all_survivor_scan_sees_a_quoted_tag_ref(tmp_path):
    """A quote directly after the `@` is never canonical and no rewriter can touch it, so the
    ref must be reported: a regex that refuses to match past that quote reports a clean flatten
    while a mutable tag stays pinned in the released tree. Reported verbatim, quote included,
    because the shape is the defect."""
    root = _repo(
        tmp_path,
        {
            ".github/workflows/apply.yml": (
                "      - uses: \"ship-iac/shipmate/actions/setup@'v0.1.0'\"\n"
            )
        },
    )

    survivors = _planned_survivors(root, NEW)

    assert len(survivors) == 1
    assert "actions/setup@'v0.1.0" in survivors[0]


def test_all_survivor_scan_flags_a_quoted_ref_even_at_the_new_sha(tmp_path):
    # The inner value already reads as the target SHA, but `@'<sha>'` is a shape REF cannot
    # rewrite: the pin stays malformed and GitHub cannot resolve the `uses:` ref. Comparing the
    # value alone would report a clean flatten.
    root = _repo(
        tmp_path,
        {".github/workflows/apply.yml": f"      - uses: ship-iac/shipmate/actions/setup@'{NEW}'\n"},
    )

    survivors = _planned_survivors(root, NEW)

    assert len(survivors) == 1
    assert "actions/setup@'" in survivors[0]


def test_all_survivor_scan_is_clean_when_every_ref_is_the_new_sha(tmp_path):
    root = _repo(
        tmp_path,
        {".github/workflows/apply.yml": f"      - uses: ship-iac/shipmate/actions/setup@{NEW}\n"},
    )

    assert _planned_survivors(root, NEW) == []


def test_main_all_exits_one_when_a_tag_ref_survives_the_flatten(tmp_path, capsys, monkeypatch):
    # End to end: --all must report failure because the tag-shaped ref cannot be flattened
    # alongside the SHA-shaped one, and must write nothing at all -- not even the SHA-shaped
    # ref it could have moved.
    root = tmp_path
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "apply.yml").write_text(
        "      - uses: ship-iac/shipmate/actions/setup@v0.1.0\n"
        f"      - uses: ship-iac/shipmate/actions/state@{OLD}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(pinrefs, "ROOT", root)

    class _R:
        returncode = 0
        stdout = NEW + "\n"

    monkeypatch.setattr(pinrefs, "git", lambda *a: _R())

    code = ri.main(["--all", "--to", NEW])

    out = capsys.readouterr().out
    assert code == 1
    assert "partial flatten" in out
    assert "actions/setup@v0.1.0" in out
    # Nothing was written -- the refusal happened before any commit.
    text = (root / ".github" / "workflows" / "apply.yml").read_text(encoding="utf-8")
    assert f"actions/state@{OLD}" in text
    assert f"actions/state@{NEW}" not in text


def test_main_all_exits_zero_when_nothing_survives_the_flatten(tmp_path, capsys, monkeypatch):
    root = tmp_path
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "apply.yml").write_text(
        f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n", encoding="utf-8"
    )
    monkeypatch.setattr(pinrefs, "ROOT", root)

    class _R:
        returncode = 0
        stdout = NEW + "\n"

    monkeypatch.setattr(pinrefs, "git", lambda *a: _R())

    code = ri.main(["--all", "--to", NEW])

    out = capsys.readouterr().out
    assert code == 0
    assert "partial flatten" not in out
    text = (root / ".github" / "workflows" / "apply.yml").read_text(encoding="utf-8")
    assert f"actions/setup@{NEW}" in text


def test_plan_touches_nothing_on_disk(tmp_path):
    # The point of the plan/validate/commit split: computing the edit must not write anything,
    # so a failed validation afterward has nothing to undo.
    root = _repo(
        tmp_path,
        {".github/workflows/apply.yml": f"  - uses: ship-iac/shipmate/actions/setup@{OLD}\n"},
    )
    p = root / ".github/workflows/apply.yml"
    before = p.read_bytes()

    planned = ri._plan(root, {("actions/setup", OLD)}, NEW)

    assert p.read_bytes() == before
    entry = next(e for e in planned if e.path == ".github/workflows/apply.yml")
    assert entry.count == 1
    assert f"actions/setup@{NEW}" in entry.text


def test_commit_writes_the_planned_edit_and_leaves_no_temp_file_behind(tmp_path):
    root = _repo(
        tmp_path,
        {".github/workflows/apply.yml": f"  - uses: ship-iac/shipmate/actions/setup@{OLD}\n"},
    )
    planned = ri._plan(root, {("actions/setup", OLD)}, NEW)

    changed = ri._commit(root, planned)

    assert changed == [(".github/workflows/apply.yml", 1)]
    text = (root / ".github/workflows/apply.yml").read_text(encoding="utf-8")
    assert f"actions/setup@{NEW}" in text
    # atomic_write_text's temp file must not survive a successful replace.
    assert list((root / ".github/workflows").glob("*.tmp")) == []


def test_rewrite_ignores_files_outside_the_two_source_shapes(tmp_path):
    # docs/ carries a grep example with a pin-shaped string, and rewriting it would corrupt
    # documentation. Only workflows and action.yml files are sources.
    root = _repo(
        tmp_path,
        {
            "docs/releasing.md": f"pin example: ship-iac/shipmate/actions/setup@{OLD}\n",
            ".github/workflows/apply.yml": f"  - uses: ship-iac/shipmate/actions/setup@{OLD}\n",
        },
    )

    changed = _rewrite(root, {("actions/setup", OLD)}, NEW)

    assert changed == [(".github/workflows/apply.yml", 1)]
    assert OLD in (root / "docs/releasing.md").read_text(encoding="utf-8")


def test_rewrite_consumer_bumps_every_engine_ref_regardless_of_path(tmp_path):
    # All-or-nothing by design: actions/summary creates the pending apply check and
    # actions/apply-cell, pinned inside apply-env-level.yml, completes it -- so a straddling pin
    # pair makes one check name and looks for another.
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

    changed, matched = _rewrite_consumer(root, NEW, None)

    plan = (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")
    apply_ = (root / ".github/workflows/apply.yml").read_text(encoding="utf-8")
    assert dict(changed) == {".github/workflows/plan.yml": 2, ".github/workflows/apply.yml": 1}
    assert matched == 3
    assert plan.count(f"@{NEW}") == 2
    assert f"apply.yml@{NEW}" in apply_


def test_rewrite_consumer_sets_the_release_label_comment(tmp_path):
    root = _repo(
        tmp_path,
        {".github/workflows/plan.yml": f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n"},
    )

    _rewrite_consumer(root, NEW, "v0.2.0")

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

    _rewrite_consumer(root, NEW, "v0.2.0")

    text = (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")
    assert "v0.1.0" not in text
    assert text.rstrip("\n").endswith(f"@{NEW} # v0.2.0")


def test_rewrite_consumer_does_not_swallow_a_following_comment_line(tmp_path):
    # The trailing-comment capture must not cross a newline: with \s* it would eat the
    # standalone comment below, and a --label rewrite would delete it, joining the lines --
    # latent corruption in a tool whose only job is safe mechanical rewriting.
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

    _rewrite_consumer(root, NEW, "v0.2.0")

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

    _rewrite_consumer(root, NEW, "v0.2.0")

    text = (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")
    assert f"actions/checkout@{OTHER} # v7.0.1" in text


def test_rewrite_consumer_writes_lf_not_crlf(tmp_path):
    # Same hazard as test_rewrite_writes_lf_not_crlf, higher stakes here: this rewriter targets
    # arbitrary consumer repos holding deploy credentials, and one without a .gitattributes eol
    # rule would get a whole-file CRLF diff burying the one-line pin change.
    root = _repo(
        tmp_path,
        {".github/workflows/plan.yml": f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n"},
    )
    p = root / ".github/workflows/plan.yml"
    p.write_bytes(f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n".encode())

    _rewrite_consumer(root, NEW, None)

    assert b"\r\n" not in p.read_bytes()


def test_rewrite_consumer_preserves_existing_crlf(tmp_path):
    # Same mirror-image regression as test_rewrite_preserves_existing_crlf, higher stakes here
    # since this rewriter targets arbitrary consumer repos. Fails when write_text emits LF only:
    # this file's 2 CRLFs become 0.
    root = _repo(
        tmp_path,
        {".github/workflows/plan.yml": f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n"},
    )
    p = root / ".github/workflows/plan.yml"
    crlf_body = (
        f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\r\n"
        f"      - uses: actions/checkout@{OTHER}\r\n"
    )
    p.write_bytes(crlf_body.encode())
    before = p.read_bytes().count(b"\r\n")

    _rewrite_consumer(root, NEW, None)

    after = p.read_bytes()
    assert after.count(b"\r\n") == before
    assert f"actions/setup@{NEW}".encode() in after


def test_rewrite_consumer_preserves_a_double_quoted_ref(tmp_path):
    # A closing quote matches neither the SHA nor the trailing-comment pattern, so a
    # substitution that ignores it pushes the quote past the new SHA: `"...@<old>"` becomes
    # `"...@<new> # label"`, one YAML string Actions cannot resolve as an action reference.
    root = _repo(
        tmp_path,
        {
            ".github/workflows/plan.yml": (
                f'      - uses: "ship-iac/shipmate/actions/setup@{OLD}"\n'
            )
        },
    )

    _rewrite_consumer(root, NEW, None)

    text = (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")
    assert f'"ship-iac/shipmate/actions/setup@{NEW}"' in text
    # The quote is the last character, not stranded after a comment.
    assert f'@{NEW}"' in text.rstrip("\n")


def test_rewrite_consumer_requires_the_same_quote_to_close_the_ref(tmp_path):
    # The half `["']?` would not cover: an opening `"` must not read as closed by an unrelated
    # `'` later on the line. If it did, `sub` would re-emit the opening quote on both sides and
    # produce the same unresolvable YAML string, so the ref is left alone instead.
    line = f"      - uses: \"ship-iac/shipmate/actions/setup@{OLD} # it's pinned\n"
    root = _repo(tmp_path, {".github/workflows/plan.yml": line})

    _rewrite_consumer(root, NEW, None)

    text = (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")
    assert f"@{NEW}" in text
    assert f'"ship-iac/shipmate/actions/setup@{NEW}"' not in text  # No closing quote is invented.


def test_rewrite_consumer_preserves_a_single_quoted_ref_with_label(tmp_path):
    root = _repo(
        tmp_path,
        {
            ".github/workflows/plan.yml": (
                f"      - uses: 'ship-iac/shipmate/actions/setup@{OLD}'\n"
            )
        },
    )

    _rewrite_consumer(root, NEW, "v0.2.0")

    text = (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")
    assert text.rstrip("\n").endswith(f"'ship-iac/shipmate/actions/setup@{NEW}' # v0.2.0")


def test_plan_consumer_touches_nothing_on_disk(tmp_path):
    root = _repo(
        tmp_path,
        {".github/workflows/plan.yml": f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n"},
    )
    p = root / ".github/workflows/plan.yml"
    before = p.read_bytes()

    planned = rc._plan_consumer(root, NEW, None)

    assert p.read_bytes() == before
    entry = next(e for e in planned if e.path == ".github/workflows/plan.yml")
    assert entry.matched == 1
    assert entry.changed is True
    assert f"actions/setup@{NEW}" in entry.text


def test_commit_consumer_writes_the_planned_edit_and_leaves_no_temp_file_behind(tmp_path):
    root = _repo(
        tmp_path,
        {".github/workflows/plan.yml": f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n"},
    )
    planned = rc._plan_consumer(root, NEW, None)

    changed, matched = rc._commit_consumer(root, planned)

    assert matched == 1
    assert changed == [(".github/workflows/plan.yml", 1)]
    text = (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")
    assert f"actions/setup@{NEW}" in text
    assert list((root / ".github/workflows").glob("*.tmp")) == []


def test_main_refuses_a_target_commit_with_no_internal_refs(tmp_path, capsys):
    """repin_consumer's own safety check must not be bypassable by pointing it at a commit
    whose tree has no shipmate self-references at all -- the engine's own root commit, here.
    pin_status() on such a commit vacuously reports zero issues, so without this check the tool
    accepts it as "safe" and writes an unresolvable pin."""
    root = _repo(
        tmp_path,
        {".github/workflows/plan.yml": f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n"},
    )

    code = rc.main(["--repo", str(root), "--sha", NO_REFS_COMMIT])

    out = capsys.readouterr().out
    assert code == 3
    assert "no shipmate self-references" in out
    # Nothing was written.
    assert OLD in (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")


def test_main_force_does_not_bypass_the_no_refs_refusal(tmp_path, capsys):
    # A bad-target check, not a staleness override: --force says "pin it anyway despite known
    # staleness", never "judge a commit whose pins cannot be judged at all".
    root = _repo(
        tmp_path,
        {".github/workflows/plan.yml": f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n"},
    )

    code = rc.main(["--repo", str(root), "--sha", NO_REFS_COMMIT, "--force"])

    assert code == 3
    assert OLD in (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")


def test_consumer_survivor_scan_ignores_a_correctly_rewritten_quoted_ref(tmp_path):
    """The scan must not misread a quote the rewrite correctly re-emitted around the new SHA as
    a leftover. Over the planned text, as _rewrite_and_report runs it -- a post-write disk read
    covers a path the CLI never takes. The positive case, a tag-pinned ref surviving, is covered
    end to end by test_main_reports_partial_rewrite_when_a_ref_survives."""
    root = _repo(
        tmp_path,
        {
            ".github/workflows/plan.yml": (
                f'      - uses: "ship-iac/shipmate/actions/setup@{OLD}"\n'
            )
        },
    )

    planned = rc._plan_consumer(root, NEW, None)

    assert pinrefs.scan_survivors([(p.path, p.text) for p in planned], NEW) == []


def test_main_reports_partial_rewrite_when_a_ref_survives(tmp_path, capsys):
    # End to end: a tag-pinned ref alongside a SHA-pinned one must not be silently dropped
    # while the tool prints a clean success, and nothing may be written at all -- not even the
    # SHA-pinned ref that could have been moved.
    root = _repo(
        tmp_path,
        {
            ".github/workflows/plan.yml": (
                f"      - uses: ship-iac/shipmate/actions/setup@v0.1.0\n"
                f"      - uses: ship-iac/shipmate/actions/state@{OLD}\n"
            )
        },
    )

    code = rc.main(["--repo", str(root), "--sha", REAL])

    out = capsys.readouterr().out
    assert code == 1
    assert "partial rewrite" in out
    assert "actions/setup@v0.1.0" in out
    # Nothing was written -- the refusal happened before any commit.
    text = (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")
    assert f"actions/state@{OLD}" in text
    assert f"actions/state@{REAL}" not in text


def test_main_reports_already_current_on_a_repeat_run(tmp_path, capsys):
    """The runbook loop over the sample repos re-runs this tool with the same --sha/--label. A
    no-op rewrite leaves `changed` empty, so only `matched` tells this case apart.

    Fails when the "no engine references found" message keys on `changed` instead of `matched`:
    a re-run then reads as a wrong --repo path rather than "already done"."""
    root = _repo(
        tmp_path,
        {
            ".github/workflows/plan.yml": (
                f"      - uses: ship-iac/shipmate/actions/setup@{REAL} # v0.1.1\n"
            )
        },
    )

    code = rc.main(["--repo", str(root), "--sha", REAL, "--label", "v0.1.1"])

    out = capsys.readouterr().out
    assert code == 0
    assert "already pinned" in out
    assert "no engine references found" not in out


def test_main_reports_no_references_when_truly_none_match(tmp_path, capsys):
    # The other side: matched == 0 must keep reporting the original message, since that really
    # is the "wrong --repo path" signal.
    root = _repo(tmp_path, {".github/workflows/plan.yml": "      - uses: actions/checkout@v4\n"})

    code = rc.main(["--repo", str(root), "--sha", REAL])

    out = capsys.readouterr().out
    assert code == 0
    assert "no engine references found" in out


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
    # Nothing was written.
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
    """`git rev-parse --verify <40-hex>` exits 0 for a SHA that does not exist; only the
    ^{commit} peel rejects it. Without that peel a typo'd SHA resolves, refs_at() on it yields
    nothing, pin_status() finds no issues, and the tool writes a pin that cannot resolve at
    runtime."""
    root = _repo(
        tmp_path,
        {".github/workflows/plan.yml": f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n"},
    )

    assert rc.main(["--repo", str(root), "--sha", NEW]) == 3
    assert "does not resolve" in capsys.readouterr().out
    assert OLD in (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")


def test_refusal_names_the_cascade_only_for_actionable_problems(capsys, monkeypatch):
    # The refusal text asserts "this is an intermediate commit of the internal-pin cascade",
    # which is only true when the problems are stale or dep_stale. That branch is what is under
    # test.
    monkeypatch.setattr(
        rc,
        "pin_status",
        lambda _sha: [pinrefs.PinIssue("actions/apply-cell", OTHER, "x.yml", "stale")],
    )

    assert rc._safe_to_pin(REAL, force=False) is False

    out = capsys.readouterr().out
    assert "intermediate commit of the internal-pin cascade" in out
    assert "could not be verified" not in out


def test_refusal_names_unverifiable_for_missing_and_error_problems(capsys, monkeypatch):
    # missing/error mean the target's pins could not be checked at all. Calling that "an
    # intermediate commit of the cascade" tells the operator to look for the wrong thing (a
    # pending re-pin) instead of the right one (why the pin commit can't be verified here).
    monkeypatch.setattr(
        rc,
        "pin_status",
        lambda _sha: [
            pinrefs.PinIssue("actions/apply-cell", OTHER, "x.yml", "missing"),
            pinrefs.PinIssue("actions/state", OTHER, "y.yml", "error", error="boom"),
        ],
    )

    assert rc._safe_to_pin(REAL, force=False) is False

    out = capsys.readouterr().out
    assert "could not be verified in this clone" in out
    assert "intermediate commit of the internal-pin cascade" not in out


def test_main_refuses_when_the_initial_refs_at_hits_a_git_failure(tmp_path, capsys, monkeypatch):
    # A GitFailure out of pinrefs.refs_at(new_sha) means the target cannot be judged, not "no
    # shipmate self-references" (exit 3). It must be refused (exit 1) and named as a git
    # failure, and nothing may be written.
    root = _repo(
        tmp_path,
        {".github/workflows/plan.yml": f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n"},
    )
    monkeypatch.setattr(
        pinrefs,
        "refs_at",
        lambda *a, **k: (_ for _ in ()).throw(pinrefs.GitFailure(REAL, "fatal: bad object")),
    )

    code = rc.main(["--repo", str(root), "--sha", REAL])

    out = capsys.readouterr().out
    assert code == 1
    assert "git failed" in out
    assert "no shipmate self-references" not in out
    assert OLD in (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")


def test_main_force_does_not_bypass_initial_git_failure(tmp_path, capsys, monkeypatch):
    # --force overrides a *known* unsafe verdict, never "we could not check."
    root = _repo(
        tmp_path,
        {".github/workflows/plan.yml": f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n"},
    )
    monkeypatch.setattr(
        pinrefs,
        "refs_at",
        lambda *a, **k: (_ for _ in ()).throw(pinrefs.GitFailure(REAL, "fatal: bad object")),
    )

    code = rc.main(["--repo", str(root), "--sha", REAL, "--force"])

    assert code == 1
    assert OLD in (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")


def test_main_refuses_when_safe_to_pin_hits_a_git_failure(tmp_path, capsys, monkeypatch):
    # Mirror of the test above for the second call site: refs_at(new_sha) succeeds, so main()
    # gets past the "no refs" check, but _safe_to_pin -- which calls pin_status(), which
    # re-derives refs internally -- is the one that hits the git failure.
    root = _repo(
        tmp_path,
        {".github/workflows/plan.yml": f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n"},
    )
    monkeypatch.setattr(
        rc,
        "pin_status",
        lambda _sha: (_ for _ in ()).throw(pinrefs.GitFailure(REAL, "fatal: bad object")),
    )

    code = rc.main(["--repo", str(root), "--sha", REAL])

    out = capsys.readouterr().out
    assert code == 1
    assert "git failed" in out
    assert "no shipmate self-references" not in out
    assert OLD in (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")

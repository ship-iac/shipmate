"""The consumer-facing pin rewriter, ``dev/repin_consumer.py``.

Text-level tests on tmp_path fixtures. What needs covering here is that the rewrite touches
exactly the intended refs and preserves everything else on the line -- trailing comments
especially, since a consumer's pins carry the release annotation.

The main()-level tests read real history. A shallow clone lacks the fixture commit, so those
tests would fail on an unrelated "does not resolve" exit 3 instead of exercising the gate
under test; the module-level skipif turns that into a loud skip, because a bare `assert` would
abort this module's collection and redden an unrelated scripts/ edit's run. CI checks out with
fetch-depth: 0, where the condition is always False.
"""

import pinrefs
import pytest
import repin_consumer as rc

OLD = "a" * 40
NEW = "b" * 40
OTHER = "c" * 40

# A real commit in this repo: the main tip this branch forked from. The main()-level tests need
# a SHA that survives `rev-parse --verify <sha>^{commit}` and is an ancestor of main, because
# refusing an unresolvable or off-main SHA is what those gates do -- a placeholder exits 3
# before reaching the rewrite.
REAL = "4914d074df71f8c3d0b4ccb73a22c153cacaca7c"

pytestmark = pytest.mark.skipif(
    pinrefs.resolve(REAL) is None,
    reason=(
        f"history fixture commit {REAL[:12]} not in this clone -- these tests read real "
        "history; check out with fetch-depth: 0"
    ),
)


def _repo(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


def _rewrite_consumer(root, new_sha, label):
    return rc._commit_consumer(root, rc._plan_consumer(root, new_sha, label))


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
    # This rewriter targets arbitrary consumer repos. Fails when atomic_write_text emits LF
    # only: this file's 2 CRLFs become 0.
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
    # The other side of test_main_reports_already_current_on_a_repeat_run: matched == 0 must
    # keep reporting the original message, since that really is the "wrong --repo path" signal.
    root = _repo(tmp_path, {".github/workflows/plan.yml": "      - uses: actions/checkout@v4\n"})

    code = rc.main(["--repo", str(root), "--sha", REAL])

    out = capsys.readouterr().out
    assert code == 0
    assert "no engine references found" in out


def test_main_rejects_a_nonexistent_full_length_sha(tmp_path, capsys):
    """`git rev-parse --verify <40-hex>` exits 0 for a SHA that does not exist; only the
    ^{commit} peel rejects it. Without that peel a typo'd SHA resolves and the tool writes a
    pin that cannot resolve at runtime."""
    root = _repo(
        tmp_path,
        {".github/workflows/plan.yml": f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n"},
    )

    assert rc.main(["--repo", str(root), "--sha", NEW]) == 3
    assert "does not resolve" in capsys.readouterr().out
    assert OLD in (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")


def test_ancestor_of_main_is_not_flagged_unreachable():
    assert rc.unreachable_from_main(REAL) is False


def test_non_ancestor_is_flagged_unreachable(monkeypatch):
    # A real force-pushed commit cannot be fixtured, so drive the git result. returncode 1 is
    # `merge-base --is-ancestor` saying "no".
    class _R:
        returncode = 1

    monkeypatch.setattr(pinrefs, "git", lambda *a: _R())
    assert rc.unreachable_from_main("0" * 40) is True


def test_git_error_on_origin_main_falls_through_to_main(monkeypatch):
    """Proves the loop structurally reaches the second base: dropping "main" from the tuple
    fails here, because fake_git raises on any base other than "origin/main" or "main".

    It does not catch the `== 1` -> `!= 0` collapse: under that mutation returncode 128 on the
    first base is itself misread as "not an ancestor" and the function still returns True, so
    this test passes for the wrong reason.
    test_git_error_on_every_base_is_not_reported_unreachable discriminates that one, every base
    erroring so the collapsed version returns True on the first base instead of the correct
    False."""

    class _R:
        def __init__(self, returncode):
            self.returncode = returncode

    def fake_git(*args):
        base = args[-1]
        if base == "origin/main":
            return _R(128)  # git error (ref missing/unresolvable), not "no"
        if base == "main":
            return _R(1)  # real "no" from merge-base --is-ancestor
        raise AssertionError(f"unexpected base {base!r}")

    monkeypatch.setattr(pinrefs, "git", fake_git)
    assert rc.unreachable_from_main("0" * 40) is True


def test_git_error_on_every_base_is_not_reported_unreachable(monkeypatch):
    # Every base ref erroring out, with no mainline ref resolving at all, is "cannot judge" and
    # not "unreachable". It must return False, never misreport a git failure as a positive
    # "not an ancestor" finding -- which here would refuse every pin in such a clone.
    class _R:
        def __init__(self, returncode):
            self.returncode = returncode

    monkeypatch.setattr(pinrefs, "git", lambda *a: _R(128))
    assert rc.unreachable_from_main("0" * 40) is False


def test_main_refuses_a_target_that_is_not_an_ancestor_of_main(tmp_path, capsys, monkeypatch):
    """The one remaining safety gate. A commit reachable only from a branch resolves here and
    rewrites cleanly, then stops existing when the branch is force-pushed, leaving the consumer
    pinned to nothing.

    Mutation: drop the `unreachable_from_main` call from `main()`, or invert it -- the rewrite
    then succeeds (exit 0) and the file carries the new SHA.
    """
    root = _repo(
        tmp_path,
        {".github/workflows/plan.yml": f"      - uses: ship-iac/shipmate/actions/setup@{OLD}\n"},
    )
    monkeypatch.setattr(rc, "unreachable_from_main", lambda _sha: True)

    code = rc.main(["--repo", str(root), "--sha", REAL])

    out = capsys.readouterr().out
    assert code == 1
    assert "not an ancestor of main" in out
    # Nothing was written.
    assert OLD in (root / ".github/workflows/plan.yml").read_text(encoding="utf-8")

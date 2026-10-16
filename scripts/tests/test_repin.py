"""Unit tests for `scripts/repin`, driven over real throwaway git repositories.

Threat model: accidental regression -- a dropped check, a widened whitespace class, a line
ending lost on the way through. Not a hostile edit to this repository, which is reviewed on
every pull request. Every expected output and file is hand-written; only the fixture commit's
SHA and the temporary paths are interpolated.
"""

import subprocess
import sys
import types

import pytest
from _loader import load_script

repin = load_script("repin")

TAG = "v1.2.3"
OLD = "0123456789abcdef0123456789abcdef01234567"
WF = "ship-iac/shipmate/.github/workflows"
PAGE = f"""# Getting started

```yaml
name: shipmate
on:
  push:
    branches: [main]
jobs:
  plan:
    uses: {WF}/plan.yml@<engine-sha>  # see the latest release
  apply:
    uses: {WF}/apply.yml@<engine-sha>  # see the latest release
  deploy:
    uses: {WF}/deploy.yml@<engine-sha>  # see the latest release
```
"""
CALLEES = ("plan.yml", "apply.yml", "deploy.yml", "unlock.yml", "drift.yml")


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo, files, message):
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode())
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """An engine checkout with a bare `origin`, `TAG` on its pushed `main` and pushed itself,
    holding `PAGE` and every callee in `CALLEES`; this machine's git config out of the way."""
    (tmp_path / "gitconfig").write_bytes(b"")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "shipmate tests")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "tests@example.invalid")
        monkeypatch.setenv(f"GIT_{who}_DATE", "2026-01-01T00:00:00Z")
    origin, repo = tmp_path / "origin.git", tmp_path / "engine"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    files = {f".github/workflows/{c}": "on: workflow_call\n" for c in CALLEES}
    sha = _commit(repo, {**files, "docs/getting-started.md": PAGE}, "release")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "origin", "main")
    # Annotated, as a release tag is: its own object SHA differs from the commit's.
    _git(repo, "tag", "-a", "-m", TAG, TAG)
    _git(repo, "push", "-q", "origin", TAG)
    monkeypatch.setattr(repin, "ENGINE", repo)
    return types.SimpleNamespace(path=repo, sha=sha)


def consumer(tmp_path, files):
    """A consumer checkout holding `files` (name -> bytes) under `.github/workflows/`."""
    wf = tmp_path / "consumer" / ".github" / "workflows"
    wf.mkdir(parents=True)
    for name, data in files.items():
        (wf / name).write_bytes(data)
    return wf


def run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["repin", *argv])
    with pytest.raises(SystemExit) as e:
        repin.main()
    return e.value.code


def on_disk(wf):
    return {p.name: p.read_bytes() for p in sorted(wf.iterdir())}


SHIM_BEFORE = f"""name: shipmate
on:
  push:
    branches: [main]
jobs:
  plan:
    uses: {WF}/plan.yml@{OLD} # v0.1.0
  apply:
    uses: {WF}/apply.yml@{OLD}
    # v0.40.0 note
  deploy:
    uses: "{WF}/deploy.yml@{OLD}" # v0.1.0
  unlock:
    uses: {WF}/unlock.yml@{OLD}  # v0.2.0
""".encode()
CI = b"jobs:\n  test:\n    uses: actions/checkout@v4\n"
SWEEP_BEFORE = (
    f"name: sweep\r\non: workflow_dispatch\r\njobs:\r\n  drift:\r\n"
    f"    uses: {WF}/drift.yml@{OLD} # v0.1.0\r\n"
).encode()


def test_every_pin_is_rewritten_in_place_keeping_its_line_endings(
    engine, tmp_path, monkeypatch, capsys
):
    """An LF `shipmate.yml` (a stale label, no label above an indented `# v0.40.0 note`, a
    quoted pin, a two-space label), a CRLF drift file carrying a stale label, and a file with
    no engine ref.

    Mutations, each red: read without `newline=""` (the CRLF file comes back LF); drop the
    quote group `("?)` (the quoted pin loses its closing quote's place); run the substitution
    over the whole file text with `\\s+` for `[ \\t]+` (the apply line swallows the note line);
    `# {sha}` for `# {tag}` in the replacement; print `updated` for every file; drop
    `^{commit}` from the `rev-parse` argument (the annotated tag's object SHA is not origin's
    peeled commit, so the run refuses);
    compare origin's unpeeled tag line instead of its `^{}` line (the run refuses as stale).
    """
    wf = consumer(tmp_path, {"ci.yml": CI, "shipmate.yml": SHIM_BEFORE, "sweep.yml": SWEEP_BEFORE})
    sha = engine.sha
    assert run(monkeypatch, str(tmp_path / "consumer"), TAG) == 2
    assert capsys.readouterr().out == (
        f"v1.2.3 -> {sha}\n"
        "unchanged ci.yml\n"
        "updated shipmate.yml\n"
        "updated sweep.yml\n"
        "--- shipmate.yml (fence)\n"
        "+++ shipmate.yml\n"
        "@@ -7,5 +7,8 @@\n"
        f"     uses: {WF}/plan.yml@<pin>\n"
        "   apply:\n"
        f"     uses: {WF}/apply.yml@<pin>\n"
        "+    # v0.40.0 note\n"
        "   deploy:\n"
        f"-    uses: {WF}/deploy.yml@<pin>\n"
        f'+    uses: "{WF}/deploy.yml@<pin>" # v1.2.3\n'
        "+  unlock:\n"
        f"+    uses: {WF}/unlock.yml@<pin>\n"
    )
    assert on_disk(wf) == {
        "ci.yml": b"jobs:\n  test:\n    uses: actions/checkout@v4\n",
        "shipmate.yml": f"""name: shipmate
on:
  push:
    branches: [main]
jobs:
  plan:
    uses: {WF}/plan.yml@{sha} # v1.2.3
  apply:
    uses: {WF}/apply.yml@{sha} # v1.2.3
    # v0.40.0 note
  deploy:
    uses: "{WF}/deploy.yml@{sha}" # v1.2.3
  unlock:
    uses: {WF}/unlock.yml@{sha} # v1.2.3
""".encode(),
        "sweep.yml": (
            f"name: sweep\r\non: workflow_dispatch\r\njobs:\r\n  drift:\r\n"
            f"    uses: {WF}/drift.yml@{sha} # v1.2.3\r\n"
        ).encode(),
    }


GOOD = f"jobs:\n  plan:\n    uses: {WF}/plan.yml@{OLD} # v0.1.0\n".encode()
LEFTOVER = (
    "engine refs not on `@{sha} # v1.2.3` alone at the end of their line; fix each by hand "
    "and run again:\n"
)


@pytest.mark.parametrize(
    "tag, files, message",
    [
        pytest.param(
            "1.2.3",
            {"shipmate.yml": GOOD},
            "'1.2.3' is not a vX.Y.Z release tag.",
            id="not-a-version",
        ),
        pytest.param(
            "v9.9.8",
            {"shipmate.yml": GOOD},
            "no tag v9.9.8 in the engine checkout at {engine}. Fetch it with "
            "`git -C {engine} fetch origin tag v9.9.8`.",
            id="unknown-tag",
        ),
    ],
)
def test_a_refusal_before_the_tag_resolves_prints_nothing(
    engine, tmp_path, monkeypatch, capsys, tag, files, message
):
    """Mutations, each red: delete the `_VERSION_RE` check (not-a-version); delete the
    `rev-parse` return-code check (unknown-tag)."""
    wf = consumer(tmp_path, files)
    expected = message.format(engine=engine.path)
    assert run(monkeypatch, str(tmp_path / "consumer"), tag) == expected
    assert capsys.readouterr().out == ""
    assert on_disk(wf) == files


@pytest.mark.parametrize(
    "files, message",
    [
        pytest.param(
            {"shipmate.yml": GOOD + f"  apply:\n    uses: {WF}/apply.yml@main\n".encode()},
            LEFTOVER + f"shipmate.yml:5:     uses: {WF}/apply.yml@main",
            id="at-main",
        ),
        pytest.param(
            {
                "shipmate.yml": GOOD
                + f"  x:\n    uses: Ship-IaC/shipmate/.github/workflows/apply.yml@{OLD}\n".encode()
            },
            LEFTOVER
            + f"shipmate.yml:5:     uses: Ship-IaC/shipmate/.github/workflows/apply.yml@{OLD}",
            id="case-variant",
        ),
        pytest.param(
            {"shipmate.yml": f"    uses: {WF}/plan.yml@{OLD} # v1.0.0,see-#12\n".encode()},
            LEFTOVER + f"shipmate.yml:1:     uses: {WF}/plan.yml@{OLD} # v1.0.0,see-#12",
            id="text-glued-to-the-label",
        ),
        pytest.param(
            {"shipmate.yml": f"    uses: {WF}/plan.yml@main # was {WF}/plan.yml@{OLD}\n".encode()},
            LEFTOVER + f"shipmate.yml:1:     uses: {WF}/plan.yml@main # was {WF}/plan.yml@{OLD}",
            id="at-main-with-a-pin-in-its-comment",
        ),
        pytest.param(
            {"shipmate.yml": f"    uses: '{WF}/plan.yml@{OLD}'\n".encode()},
            LEFTOVER + f"shipmate.yml:1:     uses: '{WF}/plan.yml@{OLD}'",
            id="single-quoted",
        ),
        pytest.param(
            {"shipmate.yml": GOOD + f"  gone:\n    uses: {WF}/gone.yml@{OLD}\n".encode()},
            "engine workflows missing at v1.2.3, so their pins would not resolve: make the "
            "consumer-file edit its CHANGELOG names:\n.github/workflows/gone.yml",
            id="callee-missing",
        ),
        pytest.param(
            {"ci.yml": CI},
            "no engine reference under {wf}.",
            id="no-engine-ref",
        ),
    ],
)
def test_a_refusal_writes_nothing(engine, tmp_path, monkeypatch, capsys, files, message):
    """Each refusal is the script's own whole message, only the resolved tag is printed, and
    every byte stays.

    Mutations, one per case, each red: delete the end-of-line `good` test (at-main,
    at-main-with-a-pin-in-its-comment, single-quoted); test from the line's last ref instead of
    its first (at-main-with-a-pin-in-its-comment); delete the `_check_callees` call
    (callee-missing); delete the `no engine reference` check (no-engine-ref); drop the `(?i:)`
    group from `_REF` (case-variant); restore the label class to `# v[^ \\t]*`
    (text-glued-to-the-label); drop the `<tag> -> <sha>` print (every case); list the
    rewritten line instead of the line on disk (at-main-with-a-pin-in-its-comment,
    single-quoted).
    """
    wf = consumer(tmp_path, files)
    expected = message.format(sha=engine.sha, wf=wf)
    assert run(monkeypatch, str(tmp_path / "consumer"), TAG) == expected
    assert capsys.readouterr().out == f"v1.2.3 -> {engine.sha}\n"
    assert on_disk(wf) == files


def test_a_branch_named_like_the_tag_does_not_resolve(engine, tmp_path, monkeypatch, capsys):
    """A branch `v9.9.9` on `origin/main` with no such tag. Mutation: drop `refs/tags/` from
    the `rev-parse` argument -- the branch resolves and the run writes."""
    _git(engine.path, "branch", "v9.9.9")
    wf = consumer(tmp_path, {"shipmate.yml": GOOD})
    assert run(monkeypatch, str(tmp_path / "consumer"), "v9.9.9") == (
        f"no tag v9.9.9 in the engine checkout at {engine.path}. Fetch it with "
        f"`git -C {engine.path} fetch origin tag v9.9.9`."
    )
    assert capsys.readouterr().out == ""
    assert on_disk(wf) == {"shipmate.yml": GOOD}


def test_a_tag_not_on_origin_main_is_refused(engine, tmp_path, monkeypatch, capsys):
    """Mutation: delete the `rc == 1` merge-base check -- the run writes."""
    _git(engine.path, "checkout", "-q", "-b", "side")
    side = _commit(engine.path, {"side.txt": "x\n"}, "side")
    _git(engine.path, "tag", "v2.0.0")
    wf = consumer(tmp_path, {"shipmate.yml": GOOD})
    assert run(monkeypatch, str(tmp_path / "consumer"), "v2.0.0") == (
        f"v2.0.0 ({side}) is not on origin/main. A commit reachable only from a branch stops "
        "resolving once that branch is force-pushed or deleted."
    )
    assert capsys.readouterr().out == ""
    assert on_disk(wf) == {"shipmate.yml": GOOD}


def test_a_page_with_two_shipmate_fences_refuses_before_any_write(
    engine, tmp_path, monkeypatch, capsys
):
    """Mutation: move the `_render` call after the write loop -- the message is the same and
    the file is rewritten."""
    two = _commit(engine.path, {"docs/getting-started.md": PAGE + PAGE}, "two fences")
    _git(engine.path, "push", "-q", "origin", "main")
    _git(engine.path, "tag", "v1.3.0")
    wf = consumer(tmp_path, {"shipmate.yml": GOOD})
    assert run(monkeypatch, str(tmp_path / "consumer"), "v1.3.0") == (
        "2 documented fences are named 'shipmate'; expected exactly one. The workflow file is "
        "rendered from the docs, so its fence name must agree."
    )
    assert capsys.readouterr().out == (
        f"v1.3.0 -> {two}\norigin has no tag v1.3.0: pinning to a local tag\n"
    )
    assert on_disk(wf) == {"shipmate.yml": GOOD}


def test_a_missing_workflows_directory_is_refused(engine, tmp_path, monkeypatch, capsys):
    """Mutation: delete the `is_dir` check -- `iterdir` raises `FileNotFoundError`."""
    assert run(monkeypatch, str(tmp_path / "nowhere"), TAG) == (
        f"no workflows directory at {tmp_path / 'nowhere' / '.github' / 'workflows'}."
    )
    assert capsys.readouterr().out == f"v1.2.3 -> {engine.sha}\n"


def test_a_usage_error_exits_1(monkeypatch, capsys):
    """Exit 2 means only "written, the shim differs", so a usage error is a string `SystemExit`.
    Mutation: delete the `len(sys.argv)` check -- the unpacking raises `ValueError`."""
    assert run(monkeypatch, "only-one") == "usage: python <engine>/scripts/repin <consumer> <tag>"
    assert capsys.readouterr().out == ""


def _fence_shim(branch, label, sep):
    lines = [
        "name: shipmate",
        "on:",
        "  push:",
        f"    branches: [{branch}]",
        "jobs:",
        "  plan:",
        f"    uses: {WF}/plan.yml@{label}",
        "  apply:",
        f"    uses: {WF}/apply.yml@{label}",
        "  deploy:",
        f"    uses: {WF}/deploy.yml@{label}",
    ]
    return "".join(line + sep for line in lines).encode()


def test_a_crlf_shim_equal_to_the_fence_but_for_its_pins_matches(
    engine, tmp_path, monkeypatch, capsys
):
    """Mutation: drop the `\\r\\n` normalization from `onboard.shim_diff` -- every line differs."""
    wf = consumer(tmp_path, {"shipmate.yml": _fence_shim("main", f"{OLD} # v0.1.0", "\r\n")})
    assert run(monkeypatch, str(tmp_path / "consumer"), TAG) == 0
    assert (
        capsys.readouterr().out
        == f"v1.2.3 -> {engine.sha}\nupdated shipmate.yml\nshipmate.yml matches the v1.2.3 fence\n"
    )
    assert on_disk(wf) == {"shipmate.yml": _fence_shim("main", f"{engine.sha} # v1.2.3", "\r\n")}


def test_a_crlf_shim_on_develop_matches_a_develop_render(engine, tmp_path, monkeypatch, capsys):
    """Mutation: find the trigger line before the `\\r\\n` normalization -- no line matches,
    the fence renders on `main`, and the run exits 2."""
    wf = consumer(tmp_path, {"shipmate.yml": _fence_shim("develop", f"{OLD} # v0.1.0", "\r\n")})
    assert run(monkeypatch, str(tmp_path / "consumer"), TAG) == 0
    assert (
        capsys.readouterr().out
        == f"v1.2.3 -> {engine.sha}\nupdated shipmate.yml\nshipmate.yml matches the v1.2.3 fence\n"
    )
    assert on_disk(wf) == {"shipmate.yml": _fence_shim("develop", f"{engine.sha} # v1.2.3", "\r\n")}


def test_a_shim_with_an_extra_line_prints_its_diff_and_exits_2(
    engine, tmp_path, monkeypatch, capsys
):
    """Mutation: diff without `_depin` in `onboard.shim_diff` -- the context lines carry the
    SHA and the label."""
    shim = _fence_shim("main", OLD, "\n").replace(
        b"plan.yml@" + OLD.encode() + b"\n", b"plan.yml@" + OLD.encode() + b"\n    with: {x: 1}\n"
    )
    wf = consumer(tmp_path, {"shipmate.yml": shim})
    assert run(monkeypatch, str(tmp_path / "consumer"), TAG) == 2
    assert capsys.readouterr().out == (
        f"v1.2.3 -> {engine.sha}\n"
        "updated shipmate.yml\n"
        "--- shipmate.yml (fence)\n"
        "+++ shipmate.yml\n"
        "@@ -5,6 +5,7 @@\n"
        " jobs:\n"
        "   plan:\n"
        f"     uses: {WF}/plan.yml@<pin>\n"
        "+    with: {x: 1}\n"
        "   apply:\n"
        f"     uses: {WF}/apply.yml@<pin>\n"
        "   deploy:\n"
    )
    sha = engine.sha
    assert on_disk(wf) == {
        "shipmate.yml": (
            "name: shipmate\non:\n  push:\n    branches: [main]\njobs:\n  plan:\n"
            f"    uses: {WF}/plan.yml@{sha} # v1.2.3\n    with: {{x: 1}}\n  apply:\n"
            f"    uses: {WF}/apply.yml@{sha} # v1.2.3\n  deploy:\n"
            f"    uses: {WF}/deploy.yml@{sha} # v1.2.3\n"
        ).encode()
    }


def test_a_shim_without_a_final_newline_ends_the_diff_on_one(engine, tmp_path, monkeypatch, capsys):
    """Mutation: drop `shim_diff`'s final-newline guarantee -- the output ends mid-line."""
    shim = _fence_shim("main", OLD, "\n").removesuffix(b"\n")
    consumer(tmp_path, {"shipmate.yml": shim})
    assert run(monkeypatch, str(tmp_path / "consumer"), TAG) == 2
    assert capsys.readouterr().out == (
        f"v1.2.3 -> {engine.sha}\n"
        "updated shipmate.yml\n"
        "--- shipmate.yml (fence)\n"
        "+++ shipmate.yml\n"
        "@@ -8,4 +8,4 @@\n"
        "   apply:\n"
        f"     uses: {WF}/apply.yml@<pin>\n"
        "   deploy:\n"
        f"-    uses: {WF}/deploy.yml@<pin>\n"
        f"+    uses: {WF}/deploy.yml@<pin>\n"
    )


def test_a_file_that_is_not_utf8_is_refused_before_any_write(engine, tmp_path, monkeypatch, capsys):
    """Mutation: drop the `UnicodeDecodeError` handler in `_read` -- the decode error escapes."""
    files = {"bin.yml": b"\xff\n", "shipmate.yml": GOOD}
    wf = consumer(tmp_path, files)
    assert run(monkeypatch, str(tmp_path / "consumer"), TAG) == (
        f"{wf / 'bin.yml'} is not UTF-8 text, so it cannot be re-pinned."
    )
    assert capsys.readouterr().out == f"v1.2.3 -> {engine.sha}\n"
    assert on_disk(wf) == files


def test_a_local_tag_on_another_commit_than_origins_is_refused(
    engine, tmp_path, monkeypatch, capsys
):
    """origin holds `TAG` on the fixture commit; the local `TAG` moves to a later commit on
    `main`. Mutation: delete the `_check_remote_tag` call -- the run writes."""
    later = _commit(engine.path, {"later.txt": "x\n"}, "later")
    _git(engine.path, "push", "-q", "origin", "main")
    _git(engine.path, "tag", "-f", "-a", "-m", TAG, TAG)
    wf = consumer(tmp_path, {"shipmate.yml": GOOD})
    assert run(monkeypatch, str(tmp_path / "consumer"), TAG) == (
        f"the local tag v1.2.3 names {later}, but origin's names {engine.sha}. Replace the "
        f"local tag: `git -C {engine.path} tag -d v1.2.3 && git -C {engine.path} fetch origin "
        "tag v1.2.3`."
    )
    assert capsys.readouterr().out == ""
    assert on_disk(wf) == {"shipmate.yml": GOOD}


def _other(engine, tmp_path, monkeypatch, capsys, files, out=None, tag=TAG):
    """Run over `files` holding no `shipmate.yml`, asserting the whole output `out` (by default
    the resolved tag and `updated other.yml`); return the bytes left on disk."""
    wf = consumer(tmp_path, files)
    assert run(monkeypatch, str(tmp_path / "consumer"), tag) == 2
    assert capsys.readouterr().out == (
        out or f"v1.2.3 -> {engine.sha}\nupdated other.yml\nshipmate.yml absent\n"
    )
    return on_disk(wf)


def _pinned(sha, sep="\n"):
    return f"jobs:{sep}  plan:{sep}    uses: {WF}/plan.yml@{sha} # v1.2.3{sep}"


def test_a_tag_origin_lacks_passes(engine, tmp_path, monkeypatch, capsys):
    """The release smoke re-pins before the tag is pushed, and the run says so. Mutations:
    refuse when origin lacks the tag (`peeled != sha`); drop the `origin has no tag` print."""
    _git(engine.path, "tag", "v1.2.4")
    out = (
        f"v1.2.4 -> {engine.sha}\norigin has no tag v1.2.4: pinning to a local tag\n"
        "updated other.yml\nshipmate.yml absent\n"
    )
    files = {"other.yml": GOOD}
    assert _other(engine, tmp_path, monkeypatch, capsys, files, out, "v1.2.4") == {
        "other.yml": _pinned(engine.sha).replace("v1.2.3", "v1.2.4").encode()
    }


def test_a_wildcard_trigger_renders_on_main_and_shows_in_the_diff(
    engine, tmp_path, monkeypatch, capsys
):
    """Mutation: drop the `_BRANCH_RE` test in `_render` -- the fence renders on `'**'` and
    the shim matches."""
    consumer(tmp_path, {"shipmate.yml": _fence_shim("'**'", f"{OLD} # v0.1.0", "\n")})
    assert run(monkeypatch, str(tmp_path / "consumer"), TAG) == 2
    assert capsys.readouterr().out == (
        f"v1.2.3 -> {engine.sha}\n"
        "updated shipmate.yml\n"
        "--- shipmate.yml (fence)\n"
        "+++ shipmate.yml\n"
        "@@ -1,7 +1,7 @@\n"
        " name: shipmate\n"
        " on:\n"
        "   push:\n"
        "-    branches: [main]\n"
        "+    branches: ['**']\n"
        " jobs:\n"
        "   plan:\n"
        f"     uses: {WF}/plan.yml@<pin>\n"
    )


def test_a_workflow_path_named_without_an_at_is_no_callee(engine, tmp_path, monkeypatch, capsys):
    """A CRLF comment ending in an engine workflow path that does not exist at the tag.
    Mutation: collect callees with `ship-iac/shipmate/(\\.github/workflows/[^@ \\t"]+)` over
    the whole text -- `gone.yml\\r\\n` is a missing callee and the run refuses."""
    note = f"# see {WF}/gone.yml\r\n"
    files = {"other.yml": (_pinned(OLD, "\r\n").replace("v1.2.3", "v0.1.0") + note).encode()}
    assert _other(engine, tmp_path, monkeypatch, capsys, files) == {
        "other.yml": (_pinned(engine.sha, "\r\n") + note).encode()
    }


def test_a_ref_under_another_owner_is_not_rewritten(engine, tmp_path, monkeypatch, capsys):
    """Mutation: drop the `(?<![\\w.-])` boundary from `_SLUG` -- the `acme-` line is rewritten."""
    acme = (
        b"  x:\n    uses: acme-ship-iac/shipmate/.github/workflows/plan.yml@" + OLD.encode() + b"\n"
    )
    files = {"other.yml": GOOD + acme}
    assert _other(engine, tmp_path, monkeypatch, capsys, files) == {
        "other.yml": _pinned(engine.sha).encode() + acme
    }


def test_a_case_variant_slug_already_on_the_pin_refuses(engine, tmp_path, monkeypatch, capsys):
    """Only the lowercase slug is rewritten, so a variant already on `@<sha> # <tag>` must
    still refuse. Mutation: drop the exact-slug test in `_check_refs` -- the line passes."""
    line = f"    uses: Ship-IaC/shipmate/.github/workflows/apply.yml@{engine.sha} # v1.2.3"
    files = {"shipmate.yml": GOOD + f"  x:\n{line}\n".encode()}
    wf = consumer(tmp_path, files)
    assert run(monkeypatch, str(tmp_path / "consumer"), TAG) == (
        LEFTOVER.format(sha=engine.sha) + f"shipmate.yml:5: {line}"
    )
    assert capsys.readouterr().out == f"v1.2.3 -> {engine.sha}\n"
    assert on_disk(wf) == files


@pytest.mark.parametrize(
    "before, after",
    [
        pytest.param(f"{OLD} # v0.1.0  ", "{sha} # v1.2.3  ", id="labelled"),
        pytest.param(f"{OLD}  ", "{sha} # v1.2.3  ", id="unlabelled"),
    ],
)
def test_trailing_blanks_after_a_pin_pass(engine, tmp_path, monkeypatch, capsys, before, after):
    """Mutation: drop the trailing `[ \\t]*` from `good` -- both lines refuse invisibly."""
    files = {"other.yml": f"jobs:\n  plan:\n    uses: {WF}/plan.yml@{before}\n".encode()}
    assert _other(engine, tmp_path, monkeypatch, capsys, files) == {
        "other.yml": (
            f"jobs:\n  plan:\n    uses: {WF}/plan.yml@{after.format(sha=engine.sha)}\n"
        ).encode()
    }


def test_a_yaml_file_is_rewritten_like_a_yml_file(engine, tmp_path, monkeypatch, capsys):
    """GitHub runs both extensions. Mutation: rewrite only names ending in `.yml` -- the
    `.yaml` pin is refused as a leftover."""
    out = f"v1.2.3 -> {engine.sha}\nupdated other.yaml\nshipmate.yml absent\n"
    assert _other(engine, tmp_path, monkeypatch, capsys, {"other.yaml": GOOD}, out) == {
        "other.yaml": _pinned(engine.sha).encode()
    }


def test_a_stale_local_tag_off_origin_main_gets_the_stale_tag_remedy(
    engine, tmp_path, monkeypatch, capsys
):
    """origin holds `TAG` on the fixture commit; the local `TAG` moves to a commit only on a
    side branch. Mutation: run `_check_remote_tag` after the merge-base check -- the run refuses
    as not on origin/main, naming no remedy for the tag."""
    _git(engine.path, "checkout", "-q", "-b", "side")
    side = _commit(engine.path, {"side.txt": "x\n"}, "side")
    _git(engine.path, "tag", "-f", "-a", "-m", TAG, TAG)
    wf = consumer(tmp_path, {"shipmate.yml": GOOD})
    assert run(monkeypatch, str(tmp_path / "consumer"), TAG) == (
        f"the local tag v1.2.3 names {side}, but origin's names {engine.sha}. Replace the "
        f"local tag: `git -C {engine.path} tag -d v1.2.3 && git -C {engine.path} fetch origin "
        "tag v1.2.3`."
    )
    assert capsys.readouterr().out == ""
    assert on_disk(wf) == {"shipmate.yml": GOOD}


def test_only_yml_and_yaml_files_are_read(engine, tmp_path, monkeypatch, capsys):
    """GitHub runs only those two extensions, so a `.DS_Store` beside them is not the script's
    to read. Mutation: drop the extension filter in `_read` -- `.DS_Store` is refused as not
    UTF-8."""
    files = {".DS_Store": b"\xff\x00", "other.yml": GOOD}
    assert _other(engine, tmp_path, monkeypatch, capsys, files) == {
        ".DS_Store": b"\xff\x00",
        "other.yml": _pinned(engine.sha).encode(),
    }


def test_an_absent_shim_is_reported_and_exits_2(engine, tmp_path, monkeypatch, capsys):
    """Mutation: exit 0 on the absent branch."""
    wf = consumer(tmp_path, {"sweep.yml": SWEEP_BEFORE})
    assert run(monkeypatch, str(tmp_path / "consumer"), TAG) == 2
    assert (
        capsys.readouterr().out
        == f"v1.2.3 -> {engine.sha}\nupdated sweep.yml\nshipmate.yml absent\n"
    )
    assert on_disk(wf) == {
        "sweep.yml": (
            f"name: sweep\r\non: workflow_dispatch\r\njobs:\r\n  drift:\r\n"
            f"    uses: {WF}/drift.yml@{engine.sha} # v1.2.3\r\n"
        ).encode()
    }

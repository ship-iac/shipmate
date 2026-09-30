"""Run comment-ops' repository-permission read over what the API can answer.

`permission` is the first input `scripts/authorize` weighs before a `shipmate apply` or
`shipmate unlock` dispatches, and bash in the action writes it rather than an importable
script. The read must fail closed: a failed call or an unexpected shape writes an empty value,
which authorize refuses as unreadable.
"""

import re
import shlex

import pytest
from _loader import action_steps, bash_only, run_step

_END = 'echo "permission=$perm" >> "$GITHUB_OUTPUT"'
_PIPE = re.compile(r"(?<!\|)\|(?!\|)")

#: The permission call, whole and hand-written. It runs on the step's `GH_TOKEN`, the
#: workflow token, so no App token scope enters the authorization path.
_CALL = (
    'if ! perm=$(gh api "repos/$GITHUB_REPOSITORY/collaborators/$USER/permission"'
    " --jq '.permission'); then"
)


def _permission_block():
    """The API read through the line that writes `permission`."""
    steps = [s for s in action_steps("comment-ops") if "perm=$(" in (s.get("run") or "")]
    assert len(steps) == 1, f"expected one step reading the permission, got {len(steps)}"
    lines = steps[0]["run"].replace("\r\n", "\n").replace("\r", "\n").splitlines()
    starts = [i for i, ln in enumerate(lines) if "perm=$(" in ln]
    assert len(starts) == 1, f"expected one permission read, got {len(starts)}"
    ends = [i for i in range(starts[0], len(lines)) if lines[i].strip() == _END]
    # A slice that missed the write would assert nothing.
    assert ends, "the permission read is not followed by the line writing `permission`"
    return "\n".join(lines[starts[0] : ends[0] + 1])


def test_the_permission_call_runs_on_the_workflow_token():
    """Mutation: prefix the call with `GH_TOKEN="$APP_TOKEN"`."""
    assert _permission_block().splitlines()[0].strip() == _CALL


def test_permission_read_is_not_a_pipeline():
    """`gh | grep -q` lets grep exit first, so gh takes SIGPIPE and pipefail's 141 reads as a
    failed call, refusing an authorized commenter."""
    code = "\n".join(
        ln for ln in _permission_block().splitlines() if not ln.strip().startswith("#")
    )
    assert "grep" not in code and not _PIPE.search(code), f"permission read pipes:\n{code}"


_TWO_LINES = "printf 'admin\\nx\\n'"
_NOT_A_USER = shlex.quote(
    '{"message":"someone is not a user",'
    '"documentation_url":"https://docs.github.com/rest","status":"404"}'
)


def _write(tmp_path, *, emit, rc):
    """What the block writes to GITHUB_OUTPUT with `gh` stubbed to emit this and exit rc."""
    out = tmp_path / "gh_output"
    out.write_text("", encoding="utf-8", newline="\n")
    harness = (
        "set -euo pipefail\n"
        f"gh() {{ {emit} ; return {rc} ; }}\n"
        "GITHUB_REPOSITORY=o/r USER=someone\n"
    ) + _permission_block()
    r = run_step(tmp_path, harness, {"GITHUB_OUTPUT": str(out), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, f"step died: {r.stdout!r} {r.stderr!r}"
    return out.read_text(encoding="utf-8").strip()


@bash_only
def test_the_two_line_stub_really_emits_two_lines(tmp_path):
    """The two-line row proves nothing if its stub prints one line holding a backslash."""
    r = run_step(tmp_path, _TWO_LINES + "\n", {"PATH": "/usr/bin:/bin"})
    assert r.stdout.splitlines() == ["admin", "x"]


@bash_only
@pytest.mark.parametrize(
    ("emit", "rc", "expected"),
    [
        ("printf '%s\\n' admin", 0, "permission=admin"),
        ("printf '%s\\n' read", 0, "permission=read"),
        ("printf '%s\\n' none", 0, "permission=none"),
        # A login that is no user. The step still has the review decision and the plan runs to
        # gather, so it writes an empty value and carries on.
        (f"printf '%s\\n' {_NOT_A_USER}", 1, "permission="),
        # A clean value with a failed exit is still a failed read.
        ("printf '%s\\n' admin", 1, "permission="),
        ("printf ''", 0, "permission="),
        (_TWO_LINES, 0, "permission="),
        # Readable and unexpected: written as read, refused by authorize.
        ("printf '%s\\n' null", 0, "permission=null"),
    ],
    ids=["admin", "read", "none", "not-a-user", "admin-failed-exit", "empty", "two-lines", "null"],
)
def test_permission_write(tmp_path, emit, rc, expected):
    """Mutations: `|| true` in place of the `if !` (admin-failed-exit writes `admin`); drop the
    charset check, or replace it with a line-based `grep -qxE '[a-z]+'` (two-lines writes a
    two-line value)."""
    assert _write(tmp_path, emit=emit, rc=rc) == expected

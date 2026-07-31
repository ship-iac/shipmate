"""Run comment-ops' team-membership decision over what the API can answer.

`is_member` is one of the two inputs `scripts/authorize` weighs before a
`shipmate apply` dispatches, and it is decided by bash in the action rather than
by an importable script. Both ways it can be wrong are silent: a member read as
a non-member is refused an apply they are entitled to, the reverse is a bypass.
"""

import re
import subprocess

import pytest
from _loader import action_steps, usable_bash

_START = "state=$(GH_TOKEN="
_PIPE = re.compile(r"(?<!\|)\|(?!\|)")


def _gather_run():
    steps = [s for s in action_steps("comment-ops") if _START in (s.get("run") or "")]
    assert len(steps) == 1, f"expected one step computing is_member, got {len(steps)}"
    return steps[0]["run"].replace("\r\n", "\n").replace("\r", "\n")


def _membership_block():
    """The API read through the `fi` that writes `is_member`."""
    lines = _gather_run().splitlines()
    starts = [i for i, ln in enumerate(lines) if _START in ln]
    assert len(starts) == 1, f"expected one membership read, got {len(starts)}"
    ends = [i for i in range(starts[0], len(lines)) if lines[i].strip() == "fi"]
    assert ends, "membership read is not closed by an `fi`"
    block = lines[starts[0] : ends[0] + 1]
    # A slice that missed the decision would assert nothing.
    assert any("is_member=true" in ln for ln in block), "extracted block writes no is_member"
    return "\n".join(block)


def test_membership_read_is_not_a_pipeline():
    """`gh | grep -q` lets grep exit first; gh takes SIGPIPE and pipefail's 141
    reads as "not a member", refusing an authorized user their apply."""
    code = "\n".join(
        ln for ln in _membership_block().splitlines() if not ln.strip().startswith("#")
    )
    assert "grep" not in code and not _PIPE.search(code), (
        f"membership decision pipes again:\n{code}"
    )


bash_only = pytest.mark.skipif(usable_bash() is None, reason="no working bash on PATH")


def _decide(tmp_path, *, stdout, rc):
    """What the block writes to GITHUB_OUTPUT with `gh` stubbed to this answer."""
    out = tmp_path / "gh_output"
    out.write_text("", encoding="utf-8", newline="\n")
    harness = (
        "set -euo pipefail\n"
        f"gh() {{ printf '%s\\n' {stdout!r} ; return {rc} ; }}\n"
        "APP_TOKEN=t OWNER=o TEAM=deployers USER=someone\n"
    ) + _membership_block()
    script = tmp_path / "membership.sh"
    script.write_text(harness, encoding="utf-8", newline="\n")
    r = subprocess.run(
        [usable_bash(), str(script)],
        capture_output=True,
        text=True,
        env={"GITHUB_OUTPUT": str(out), "PATH": "/usr/bin:/bin"},
        timeout=30,
    )
    assert r.returncode == 0, f"step died: {r.stdout!r} {r.stderr!r}"
    return out.read_text(encoding="utf-8").strip()


@bash_only
@pytest.mark.parametrize(
    ("stdout", "rc", "expected"),
    [
        ("active", 0, "is_member=true"),
        ("pending", 0, "is_member=false"),  # invited, not accepted
        # 404: not in the team. The step must record a non-member and carry on --
        # it still has the review decision and the plan run to gather.
        ("", 1, "is_member=false"),
        ("null", 0, "is_member=false"),
        ("", 0, "is_member=false"),
        # A substring match would read these two as membership.
        ("inactive", 0, "is_member=false"),
        ("deactivated", 0, "is_member=false"),
    ],
)
def test_membership_decision(tmp_path, stdout, rc, expected):
    assert _decide(tmp_path, stdout=stdout, rc=rc) == expected

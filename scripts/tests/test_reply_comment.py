"""Tests for scripts/reply-comment: the refusal, failure and notice bodies the bash sites post."""

import os
import subprocess
import sys

from _loader import SCRIPTS, load_script

rc = load_script("reply-comment")

_RUN_URL = "https://github.com/org/repo/actions/runs/7777"
_FOOTER = f"[run]({_RUN_URL}). Comment `shipmate help` for the available commands."


def _main(**reply):
    env = {
        **os.environ,
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "org/repo",
        "GITHUB_RUN_ID": "7777",
        **{f"SHIPMATE_REPLY_{k.upper()}": v for k, v in reply.items()},
    }
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "reply-comment")],
        env=env,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )


def test_a_refusal_is_header_verdict_and_footer():
    """Mutations: drop the blank line between the verdict and the footer; pass `hint=False`
    for every outcome."""
    assert rc.body("unlock", "dev-eu", "refused", "no", _RUN_URL) == (
        f"### shipmate unlock dev-eu\n\n🔴 refused: no\n\n{_FOOTER}"
    )


def test_a_failure_says_failed_not_refused():
    """Mutation: map `failed` to the `refused` verdict."""
    assert rc.body("doctor", "", "failed", "no token", _RUN_URL) == (
        f"### shipmate doctor\n\n🔴 failed: no token\n\n{_FOOTER}"
    )


def test_a_notice_carries_the_white_circle_and_no_hint():
    """Mutations: give `notice` the 🔴 circle; pass `hint=True` for every outcome."""
    assert rc.body("apply", "dev-eu", "notice", "dev-eu: ungated", _RUN_URL) == (
        f"### shipmate apply dev-eu\n\n⚪ dev-eu: ungated\n\n[run]({_RUN_URL})"
    )


def test_an_empty_verb_drops_the_env_from_the_header():
    """Mutation: keep the env in `header` when the verb is empty."""
    assert rc.body("", "dev-eu", "refused", "x", _RUN_URL) == (
        f"### shipmate\n\n🔴 refused: x\n\n{_FOOTER}"
    )


def test_main_prints_the_body_from_the_environment_with_the_env_escaped():
    """The whole stdout of a run, through the environment variables the action sets.

    Mutation: drop `_md_escape` from the header's env.
    """
    result = _main(verb="apply", env="a<b", outcome="refused", text="`x` <y>")
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"### shipmate apply a&lt;b\n\n🔴 refused: `x` <y>\n\n{_FOOTER}\n"


def test_an_unknown_outcome_exits_non_zero_and_prints_no_body():
    """A caller capturing stdout must get nothing to post.

    Mutation: default an unknown outcome to `refused`.
    """
    result = _main(verb="apply", outcome="refuse", text="x")
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "::error::SHIPMATE_REPLY_OUTCOME must be refused, failed or notice (got: refuse)\n"
    )

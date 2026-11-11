"""actions/setup/release.sh: one download function and one refusal writer for the setup steps.

Each case sources the real `release.sh` into a small bash body, as a setup step does, with a
bash `curl` stub on PATH that logs its argv, writes `$STUB_BODY` to its `-o` path, prints
`$STUB_CODE $STUB_EFFECTIVE` (the effective URL defaulting to the requested one) and exits
`$STUB_RC`. Nothing reaches the network.

Threat model: an accidental regression in a SHA-pinned, reviewed engine file -- a dropped curl
option, a lost remedy or exit-2 branch, a refusal overwritten or never written, a curl call
outside `fetch`. A hostile edit is out of scope.
"""

import os
import re

import pytest
from _loader import (
    ACTIONS,
    action_steps,
    bash_only,
    run_step,
    setup_curl_argv,
    write_stub,
)

_LIB = (ACTIONS / "setup").as_posix()
_URL = "https://github.com/o/r/releases/download/v1.0.0/asset.tar.gz"
_ERROR = "::error title=Release step failed::Tool 1.0.0: "
_CURL_STUB = (
    'printf \'%s\\x1f\' "$@" >> "$STUB_LOG"; echo >> "$STUB_LOG"\n'
    'prev=""; for a in "$@"; do if [ "$prev" = -o ]; then printf %s "$STUB_BODY" > "$a"; fi; '
    'prev="$a"; done\n'
    'printf \'%s %s\' "$STUB_CODE" "${STUB_EFFECTIVE:-${!#}}"\n'
    'exit "$STUB_RC"'
)


def _run(tmp_path, snippet, *, code="200", rc=0, effective=None, path=None):
    """Run `snippet` after sourcing release.sh and `release_init`. Return (result, the
    $GITHUB_OUTPUT text, curl's argv per call)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_stub(bin_dir, "curl", _CURL_STUB)
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    log = tmp_path / "curl.log"
    body = (
        "set -euo pipefail\n"
        'source "$GITHUB_ACTION_PATH/release.sh"\n'
        'release_init "Release step failed" "Tool 1.0.0: "\n'
        f"{snippet}\n"
    )
    env = {
        **os.environ,
        "PATH": path or f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "GITHUB_OUTPUT": str(out),
        "GITHUB_ACTION_PATH": _LIB,
        "STUB_LOG": log.as_posix(),
        "STUB_BODY": "release bytes",
        "STUB_CODE": code,
        "STUB_RC": str(rc),
        **({} if effective is None else {"STUB_EFFECTIVE": effective}),
    }
    r = run_step(tmp_path, body, env)
    calls = (
        [ln.split("\x1f")[:-1] for ln in log.read_text(encoding="utf-8").splitlines()]
        if log.exists()
        else []
    )
    return r, out.read_text(encoding="utf-8"), calls


def _fetch(dest):
    return f'fetch "{_URL}" "{dest}" "check the version" "re-run the failed job"'


@bash_only
def test_a_200_writes_the_body_and_runs_curl_with_the_one_option_string(tmp_path):
    """Mutations: drop `--max-time 600` from `fetch` (argv); drop `-o "$dest"` (body)."""
    dest = (tmp_path / "asset").as_posix()
    r, out, calls = _run(tmp_path, _fetch(dest))
    assert (r.returncode, r.stdout, out) == (0, "", ""), r.stderr
    assert calls == [setup_curl_argv(_URL, dest)]
    assert (tmp_path / "asset").read_text(encoding="utf-8") == "release bytes"


@bash_only
def test_a_404_refuses_with_the_callers_404_remedy(tmp_path):
    """Mutation: pass the other remedy on a 404."""
    r, out, _ = _run(tmp_path, _fetch((tmp_path / "a").as_posix()), code="404")
    reason = f"Tool 1.0.0: {_URL} answered HTTP 404 from {_URL} (curl exit 0); check the version."
    assert (r.returncode, r.stdout, out) == (
        1,
        f"::error title=Release step failed::{reason}\n",
        f"refusal={reason}\n",
    )


@bash_only
def test_a_503_names_the_url_that_answered_without_its_query(tmp_path):
    """A redirect lands on another host; the refusal names it, signed query stripped.

    Mutations: print the requested URL as the answering host; drop the `%%\\?*` strip.
    """
    effective = "https://objects.example.invalid/asset?X-Amz-Signature=secret"
    r, out, _ = _run(tmp_path, _fetch((tmp_path / "a").as_posix()), code="503", effective=effective)
    reason = (
        f"Tool 1.0.0: {_URL} answered HTTP 503 from https://objects.example.invalid/asset "
        "(curl exit 0); re-run the failed job."
    )
    assert (r.returncode, r.stdout, out) == (
        1,
        f"::error title=Release step failed::{reason}\n",
        f"refusal={reason}\n",
    )


@bash_only
def test_empty_remedies_end_the_refusal_at_curls_exit(tmp_path):
    """The verify steps pass no remedy. Mutation: append `; ${remedy}` unconditionally."""
    snippet = f'fetch "{_URL}" "{(tmp_path / "a").as_posix()}" "" ""'
    r, out, _ = _run(tmp_path, snippet, code="000", rc=7)
    reason = f"Tool 1.0.0: {_URL} answered HTTP 000 from {_URL} (curl exit 7)."
    assert (r.returncode, out) == (1, f"refusal={reason}\n")


@bash_only
def test_curl_exit_2_refuses_with_the_7_71_floor(tmp_path):
    """curl exits 2 on an option it does not know, which a re-run repeats.

    Mutation: drop the exit-2 branch (the generic HTTP line appears).
    """
    r, out, calls = _run(tmp_path, _fetch((tmp_path / "a").as_posix()), code="", rc=2)
    reason = (
        "Tool 1.0.0: curl exited 2 on an option it does not know; shipmate needs curl 7.71 or "
        "later (CONTRACT.md §Runner prerequisites)."
    )
    assert (r.returncode, r.stdout, out, len(calls)) == (
        1,
        f"::error title=Release step failed::{reason}\n",
        f"refusal={reason}\n",
        1,
    )


@pytest.mark.parametrize(
    ("missing", "extras"),
    [("curl", ""), ("sha256sum", ""), ("gpgconf", "gpg gpgconf")],
)
@bash_only
def test_require_refuses_naming_the_missing_tool(tmp_path, missing, extras):
    """PATH holds only never-run stubs for the other tools, so the check reads PATH alone.

    Mutations: drop `curl` or `sha256sum` from `require`'s base list; ignore its arguments.
    """
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name in {"curl", "openssl", "base64", "sha256sum", "gpg", "gpgconf"} - {missing}:
        write_stub(stubs, name, "exit 99")
    r, out, _ = _run(tmp_path, f"require {extras}", path=str(stubs))
    reason = f"Tool 1.0.0: {missing} is not installed on the runner."
    assert (r.returncode, r.stdout, out) == (
        1,
        f"::error title=Release step failed::{reason}\n",
        f"refusal={reason}\n",
    )


_GENERIC = 'Tool 1.0.0: a command exited 3 under "Release step failed"; the step log names it.'


@bash_only
def test_a_failure_inside_a_function_writes_the_generic_refusal(tmp_path):
    """`set -e` ends the step inside a function with no `fail`.

    Mutation: drop `trap _release_exit EXIT` (no refusal is written).
    """
    r, out, _ = _run(tmp_path, "f() { return 3; }\nf\necho unreachable")
    assert (r.returncode, r.stdout, out) == (
        3,
        f"::error title=Release step failed::{_GENERIC}\n",
        f"refusal={_GENERIC}\n",
    )


@bash_only
def test_fail_leaves_exactly_its_own_refusal(tmp_path):
    """Mutation: drop the handler's existing-`refusal=` guard (a second, generic line)."""
    r, out, _ = _run(tmp_path, "fail 'the specific reason.'")
    assert (r.returncode, r.stdout, out) == (
        1,
        "::error title=Release step failed::Tool 1.0.0: the specific reason.\n",
        "refusal=Tool 1.0.0: the specific reason.\n",
    )


@bash_only
def test_a_fail_in_a_subshell_still_leaves_exactly_one_refusal(tmp_path):
    """The subshell's variables die with it; its write to $GITHUB_OUTPUT does not.

    Mutation: replace the handler's file check with a variable `fail` sets (a second line).
    """
    r, out, _ = _run(tmp_path, 'x="$(fail \'from a subshell.\')"\necho "$x"')
    assert (r.returncode, out) == (1, "refusal=Tool 1.0.0: from a subshell.\n")


@bash_only
def test_a_passing_step_writes_no_refusal(tmp_path):
    """Mutation: write the generic refusal on every exit."""
    r, out, _ = _run(tmp_path, "true")
    assert (r.returncode, r.stdout, out) == (0, "", "")


_CURL_WORD = re.compile(r"\bcurl\b")


@pytest.mark.parametrize("action", ["setup", "build-matrix"])
def test_no_step_names_curl(action):
    """Every download goes through `fetch`, so the option string exists once.

    Mutation: restore one inline `curl` call in a setup step.
    """
    steps = action_steps(action)
    assert [s.get("name") for s in steps if _CURL_WORD.search(s.get("run") or "")] == []

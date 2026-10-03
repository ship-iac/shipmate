"""actions/setup downloads the pinned Terramate release with one curl and fails closed.

The `Install Terramate` body runs here against stub `curl`, `tar`, `terramate` and `uname`
binaries. The stubs record their argv, so curl's whole command line is compared to a
hand-written constant: a dropped `-L` or `--retry-all-errors`, or an added `--fail`, reds it.
"""

import os
import stat

import pytest
from _loader import bash_only, run_step, step_by

_ACTION = "setup"
_STEP = "Install Terramate"
_VERSION = "0.17.1"
_URL = (
    "https://github.com/terramate-io/terramate/releases/download/"
    "v0.17.1/terramate_0.17.1_linux_x86_64.tar.gz"
)


def _annotation(code, rc):
    return (
        f"::error title=Terramate install failed::Terramate {_VERSION} did not install: "
        f"{_URL} answered HTTP {code} (curl exit {rc}); re-run the failed job."
    )


def _stub(bin_dir, name, body):
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _install(tmp_path, curl_output, curl_rc):
    """Run the step's body with a curl that prints `curl_output` and exits `curl_rc`.

    The tar stub writes a `terramate` stub where the real extract would, and that stub records
    its argv when run. Return (result, runner_temp, the logs dir holding each stub's argv).
    """
    bin_dir = tmp_path / "bin"
    logs = tmp_path / "logs"
    runner_temp = tmp_path / "runner-temp"
    for d in (bin_dir, logs, runner_temp):
        d.mkdir()
    log = logs.as_posix()
    _stub(
        bin_dir,
        "curl",
        f"printf '%s\\n' \"$@\" > '{log}/curl'\nprintf '{curl_output}'\nexit {curl_rc}",
    )
    _stub(
        bin_dir,
        "tar",
        f"printf '%s\\n' \"$@\" > '{log}/tar'\n"
        f'printf \'#!/usr/bin/env bash\\nprintf "%%s\\\\n" "$@" > {log}/terramate\\n\''
        ' > "$4/terramate"\nchmod +x "$4/terramate"',
    )
    _stub(
        bin_dir,
        "uname",
        'if [ "$1" = -s ]; then echo Linux; elif [ "$1" = -m ]; then echo x86_64; fi',
    )
    github_path = tmp_path / "github_path"
    github_path.write_text("", encoding="utf-8")
    r = run_step(
        tmp_path,
        step_by(_ACTION, name=_STEP)["run"],
        {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "RUNNER_TEMP": runner_temp.as_posix(),
            "GITHUB_PATH": str(github_path),
            "SHIPMATE_TERRAMATE_VERSION": _VERSION,
        },
    )
    return r, runner_temp.as_posix(), logs


def _argv(logs, name):
    path = logs / name
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else None


def test_the_install_step_is_fail_closed():
    """No `continue-on-error`, no `if:`, no third-party `uses:`.

    Mutation: add `continue-on-error: true` to the step.
    """
    assert set(step_by(_ACTION, name=_STEP)) == {"name", "shell", "env", "run"}


@bash_only
def test_a_200_extracts_checks_the_binary_and_adds_the_directory_to_path(tmp_path):
    """Mutations: drop `--retry-all-errors` or `-L`, or add `--fail` (curl argv); drop the
    `terramate --version` line (terramate argv).
    """
    r, runner_temp, logs = _install(tmp_path, "200", 0)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    tarball = f"{runner_temp}/terramate/terramate.tar.gz"
    assert _argv(logs, "curl") == [
        "-sSL",
        "--retry",
        "3",
        "--retry-all-errors",
        "-o",
        tarball,
        "-w",
        "%{http_code}",
        _URL,
    ]
    assert _argv(logs, "tar") == ["-xzf", tarball, "-C", f"{runner_temp}/terramate", "terramate"]
    assert _argv(logs, "terramate") == ["--version"]
    assert (tmp_path / "github_path").read_text(encoding="utf-8") == f"{runner_temp}/terramate\n"


@pytest.mark.parametrize(
    ("curl_output", "curl_rc", "code"),
    [
        # Mutation: drop the `!= 200` status check.
        ("404", 0, "404"),
        # Mutation: drop the `${code:-000}` default.
        ("", 7, "000"),
        # A transfer cut mid-body. Mutation: replace `|| rc=$?` with `|| true`.
        ("200", 18, "200"),
    ],
)
@bash_only
def test_a_failed_download_fails_the_step_with_one_annotation(tmp_path, curl_output, curl_rc, code):
    r, _, logs = _install(tmp_path, curl_output, curl_rc)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == _annotation(code, curl_rc) + "\n"
    assert _argv(logs, "tar") is None
    assert (tmp_path / "github_path").read_text(encoding="utf-8") == ""

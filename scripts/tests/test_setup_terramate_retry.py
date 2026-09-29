"""actions/setup retries the Terramate download and names a failed install.

`terramate-action`'s `install.sh` runs one `curl -w "%{http_code}" -o <file> -L <url>` with no
retry option and prints the HTTP status only to its own stderr. The `Retry transient download
errors` step writes a `.curlrc` that curl reads through the `CURL_HOME` the install step sets,
and `Report a failed Terramate install` rebuilds the release URL, probes it once and prints one
annotation. The mechanism test runs a real curl against a local server, so it proves curl honours
the file, not only that the file exists.
"""

import http.server
import os
import stat
import subprocess
import threading

import pytest
from _loader import bash_only, run_step, step_by, usable_bash

_ACTION = "setup"
_RETRY = "Retry transient download errors"
_INSTALL = "Install Terramate"
_REPORT = "Report a failed Terramate install"

_INSTALL_ENV = {"CURL_HOME": "${{ runner.temp }}/shipmate-curl"}
_REPORT_IF = "${{ failure() && steps.terramate.outcome == 'failure' }}"
_REPORT_ENV = {"SHIPMATE_TERRAMATE_VERSION": "${{ steps.versions.outputs.terramate }}"}

_ANNOTATION = (
    "::error title=Terramate install failed::Terramate 0.17.1 did not install; the step log "
    "above names the cause. https://github.com/terramate-io/terramate/releases/download/"
    "v0.17.1/terramate_0.17.1_linux_x86_64.tar.gz answers HTTP 503 now; re-run the failed job."
)


def _require_curl():
    probe = subprocess.run(
        [usable_bash(), "-c", "command -v curl"], capture_output=True, text=True, timeout=30
    )
    if probe.returncode != 0:
        pytest.skip("no curl on this host's bash PATH")


def _write_curlrc(tmp_path, home_curlrc=None):
    """Run the real retry step with `RUNNER_TEMP` and `HOME` under `tmp_path`; return
    `RUNNER_TEMP`."""
    runner_temp = tmp_path / "runner-temp"
    home = tmp_path / "home"
    runner_temp.mkdir()
    home.mkdir()
    if home_curlrc is not None:
        (home / ".curlrc").write_text(home_curlrc, encoding="utf-8", newline="\n")
    r = run_step(
        tmp_path,
        step_by(_ACTION, name=_RETRY)["run"],
        {**os.environ, "RUNNER_TEMP": str(runner_temp), "HOME": str(home)},
    )
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    return runner_temp


_OK_BODY = b"terramate tarball"
_ERROR_BODY = b"internal error"


class _Flaky(http.server.BaseHTTPRequestHandler):
    """Answers 500, 500, then 200, counting requests on the server."""

    def do_GET(self):
        self.server.requests += 1
        ok = self.server.requests >= 3
        code, body = (200, _OK_BODY) if ok else (500, _ERROR_BODY)
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@bash_only
def test_curl_retries_through_the_install_steps_curl_home(tmp_path):
    """Three requests reach a server answering 500, 500, 200, and curl prints 200, run with
    the flags `install.sh` uses and the `CURL_HOME` the install step's `env:` names.

    The output file holds only the final 200 body, not a failed attempt's.

    Mutations: delete the `retry = 3` line; change the install step's `CURL_HOME`; make the 200
    response send the 500 body.
    """
    _require_curl()
    runner_temp = _write_curlrc(tmp_path)
    curl_home = step_by(_ACTION, name=_INSTALL)["env"]["CURL_HOME"].replace(
        "${{ runner.temp }}", runner_temp.as_posix()
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Flaky)
    server.requests = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/terramate.tar.gz"
        r = run_step(
            tmp_path,
            f"curl -w '%{{http_code}}' -o '{(tmp_path / 'out').as_posix()}' -L '{url}'\n",
            # HOME and XDG_CONFIG_HOME are curl's fallbacks when CURL_HOME holds no config.
            {
                **{k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"},
                "CURL_HOME": curl_home,
                "HOME": str(tmp_path / "home"),
            },
            timeout=60,
        )
    finally:
        server.shutdown()
        server.server_close()
    assert (server.requests, r.stdout) == (3, "200"), f"stderr={r.stderr!r}"
    assert (tmp_path / "out").read_bytes() == _OK_BODY


@bash_only
def test_the_generated_file_keeps_the_runners_own_curlrc_first(tmp_path):
    """curl reads only the first config it finds, so a runner's own `proxy` or `cacert` must
    survive in the generated file. Mutation: drop the copy of `$HOME/.curlrc`.
    """
    runner_temp = _write_curlrc(tmp_path, home_curlrc="user-agent = shipmate-probe\n")
    got = (runner_temp / "shipmate-curl" / ".curlrc").read_text(encoding="utf-8")
    assert got == "user-agent = shipmate-probe\nretry = 3\nretry-all-errors\n"


@bash_only
@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes are not meaningful on Windows")
def test_the_generated_file_is_private(tmp_path):
    """The copied `$HOME/.curlrc` can hold `proxy-user` credentials. Mutation: delete the
    `umask 077` line.
    """
    runner_temp = _write_curlrc(tmp_path, home_curlrc="proxy-user = u:p\n")
    mode = (runner_temp / "shipmate-curl" / ".curlrc").stat().st_mode
    assert stat.S_IMODE(mode) & 0o077 == 0


def test_the_install_is_wired_to_the_retry_file_and_the_report():
    """Mutations: add `continue-on-error: true` to the install step; change `failure()` to
    `always()` in the report step's `if:`.
    """
    install = step_by(_ACTION, name=_INSTALL)
    report = step_by(_ACTION, name=_REPORT)
    assert (install.get("id"), install.get("env"), "continue-on-error" in install) == (
        "terramate",
        _INSTALL_ENV,
        False,
    )
    assert (report.get("if"), report.get("env")) == (_REPORT_IF, _REPORT_ENV)


def _stub(bin_dir, name, body):
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@bash_only
def test_a_failed_install_names_the_url_and_its_status_now(tmp_path):
    """Mutations: drop the URL from the annotation; drop the status from it."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir, "curl", "printf 503")
    _stub(
        bin_dir,
        "uname",
        'if [ "$1" = -s ]; then echo Linux; elif [ "$1" = -m ]; then echo x86_64; fi',
    )
    r = run_step(
        tmp_path,
        step_by(_ACTION, name=_REPORT)["run"],
        {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "SHIPMATE_TERRAMATE_VERSION": "0.17.1",
        },
    )
    assert r.returncode != 0
    assert r.stdout.strip() == _ANNOTATION, f"stderr={r.stderr!r}"

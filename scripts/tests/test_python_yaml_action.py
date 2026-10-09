"""actions/python-yaml installs python3-yaml when PyYAML is missing, never fails, and runs before
every step that parses YAML.

The step body runs here as GitHub runs a composite `shell: bash` step, `bash --noprofile --norc
-eo pipefail <file>`, against stub `python3`, `sudo` and `apt-get` binaries that record their
argv. `sudo` drops its `-n` and runs the rest, so the real `env` reaches the `apt-get` stub, and
a successful `install` makes the `python3` stub's import succeed from then on.
"""

import os
import stat
import subprocess
import sys

import pytest
from _loader import ACTIONS, ENGINE, action_steps, bash_only, load_script, usable_bash

ec = load_script("env-config")

_USES = "$/actions/python-yaml"
_WARNING = (
    "::warning::could not install python3-yaml on this runner; the steps that read YAML report it."
)
_IMPORT = ["-I", "-c", "import yaml"]
_APT = ["-n", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-qq", "-o"]
_APT += ["Acquire::Retries=3"]
_UPDATE = [*_APT, "update"]
_INSTALL = [*_APT, "install", "-y", "--no-install-recommends", "python3-yaml"]
#: The install step's whole `if:` in comment-ops: the routes that run `doctor` or `gate-config`.
_COMMENT_OPS_IF = (
    "${{ steps.parse.outputs.route == 'doctor' || steps.parse.outputs.route == 'apply'"
    " || steps.parse.outputs.route == 'unlock' }}"
)


def _stub(bin_dir, name, body):
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run(tmp_path, *, has_yaml=False, sudo_rc=None, install_rc=0):
    """Run the step body; return (result, calls), `calls[name]` being each call's argv in order.

    `has_yaml` makes the import succeed from the start. `sudo_rc`, when set, is the exit of
    every `sudo` call, which then runs nothing. `apt-get update` succeeds; `install_rc` is the
    `install` exit, and a zero one installs PyYAML for the `python3` stub.
    """
    bin_dir, logs = tmp_path / "bin", tmp_path / "logs"
    bin_dir.mkdir()
    logs.mkdir()
    log, marker = logs.as_posix(), (tmp_path / "yaml-installed").as_posix()
    if has_yaml:
        (tmp_path / "yaml-installed").write_text("", encoding="utf-8")

    def record(name):
        return "{ printf '%s\\n' \"$@\"; echo --; } >> '" + f"{log}/{name}'"

    _stub(bin_dir, "python3", record("python3") + f"\n[ -e '{marker}' ]")
    sudo_tail = f"exit {sudo_rc}" if sudo_rc is not None else 'shift\nexec "$@"'
    _stub(bin_dir, "sudo", record("sudo") + "\n" + sudo_tail)
    _stub(
        bin_dir,
        "apt-get",
        record("apt-get") + "\n"
        'if [ "$4" = update ]; then exit 0; fi\n'
        f"[ {install_rc} = 0 ] || exit {install_rc}\n"
        f": > '{marker}'",
    )
    (step,) = action_steps("python-yaml")
    script = tmp_path / "step.sh"
    script.write_text(step["run"], encoding="utf-8", newline="\n")
    bash = usable_bash()
    assert bash is not None, "callers are bash_only-gated"
    r = subprocess.run(
        [bash, "--noprofile", "--norc", "-eo", "pipefail", str(script)],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{bin_dir.as_posix()}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    calls = {}
    for name in ("python3", "sudo"):
        path = logs / name
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        calls[name] = []
        argv = []
        for line in lines:
            if line == "--":
                calls[name].append(argv)
                argv = []
            else:
                argv.append(line)
    return r, calls


@bash_only
def test_an_importable_pyyaml_installs_nothing_and_prints_nothing(tmp_path):
    """Mutation: delete the first `&& exit 0` (sudo is then called)."""
    r, calls = _run(tmp_path, has_yaml=True)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    assert calls == {"python3": [_IMPORT], "sudo": []}


@bash_only
def test_a_missing_pyyaml_is_installed_with_update_then_install(tmp_path):
    """Mutations: drop `apt update &&` (sudo argv); drop `-I` from the first import check
    (python3 argv)."""
    r, calls = _run(tmp_path)
    assert (r.returncode, r.stdout) == (0, ""), r.stderr
    assert calls == {"python3": [_IMPORT, _IMPORT], "sudo": [_UPDATE, _INSTALL]}


@bash_only
def test_a_failed_install_warns_and_exits_zero(tmp_path):
    """Under `-e` a failing group ends the step, which would skip the script whose refusal names
    the cause. Mutation: delete `|| true` (exit 1, no warning)."""
    r, calls = _run(tmp_path, install_rc=100)
    assert (r.returncode, r.stdout) == (0, _WARNING + "\n"), r.stderr
    assert calls["sudo"] == [_UPDATE, _INSTALL]


@bash_only
def test_a_runner_without_sudo_warns_and_attempts_no_install(tmp_path):
    """Mutation: replace `&&` with `;` between update and install (install is then attempted)."""
    r, calls = _run(tmp_path, sudo_rc=1)
    assert (r.returncode, r.stdout) == (0, _WARNING + "\n"), r.stderr
    assert calls["sudo"] == [_UPDATE]


def test_the_troubleshooting_entry_quotes_the_warning_and_the_refusal(monkeypatch):
    """Each quote is read from its source: the warning from the parsed step, the refusal from
    `runner_refusal` with PyYAML absent. Mutation: edit one word in the action's warning."""
    (step,) = action_steps("python-yaml")
    (echo,) = [ln.strip() for ln in step["run"].splitlines() if ln.strip().startswith("echo ")]
    warning = echo.removeprefix('echo "').removesuffix('"')
    monkeypatch.setitem(sys.modules, "yaml", None)
    refusal = ec.runner_refusal()
    assert warning.startswith("::warning::") and refusal.startswith("::error::"), (warning, refusal)

    text = (ENGINE / "docs" / "troubleshooting.md").read_text(encoding="utf-8")
    heading = "### `could not install python3-yaml`, or `the engine needs PyYAML`\n"
    entry = text[text.index(heading) :].split("\n### ", 1)[0]
    quotes = [ln.removeprefix("> ") for ln in entry.splitlines() if ln.startswith("> ")]
    assert quotes == [warning.removeprefix("::warning::"), refusal.removeprefix("::error::")]


@pytest.mark.parametrize("action", ["build-matrix", "apply-detect", "deploy-detect"])
def test_the_detect_actions_install_first(action):
    """Mutation: delete the step from one action."""
    assert action_steps(action)[0] == {"uses": _USES}


def test_summary_installs_directly_before_doctor_and_its_gate_step_stays_unconditional():
    """A failing step before `Create/refresh gate` would skip it, and the install never fails.
    Mutation: move the step after `Doctor`."""
    steps = action_steps("summary")
    names = [s.get("name") for s in steps]
    doctor = names.index("Doctor: settings-drift warnings (annotations only, never blocks)")
    assert steps[doctor - 1] == {"uses": _USES}
    gate = steps[names.index("Create/refresh gate")]
    assert "if" not in gate


def test_comment_ops_installs_for_its_parsing_routes_before_any_parsing_step():
    """Mutations: drop `unlock` from the `if:`; move the step after `Resolve gate configuration`."""
    steps = action_steps("comment-ops")
    (at,) = [i for i, s in enumerate(steps) if s.get("uses") == _USES]
    assert steps[at] == {"uses": _USES, "if": _COMMENT_OPS_IF}
    parsing = [
        i
        for i, s in enumerate(steps)
        if any(f"scripts/{name}" in (s.get("run") or "") for name in ("doctor", "gate-config"))
    ]
    assert len(parsing) == 3, parsing
    assert at < min(parsing)


def test_exactly_the_actions_that_parse_yaml_install_it():
    """`pr-facts` and `drift-issues` never read the config. Mutation: add the step to
    `actions/pr-facts`."""
    callers = {
        path.parent.name
        for path in ACTIONS.glob("*/action.yml")
        if any(s.get("uses") == _USES for s in action_steps(path))
    }
    assert callers == {"build-matrix", "apply-detect", "deploy-detect", "summary", "comment-ops"}

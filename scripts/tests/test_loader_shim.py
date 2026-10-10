"""Tests for `_loader.write_python3_shim`, the `python3` the as-wired step tests run under."""

import os
import shutil

from _loader import SCRIPTS, bash_only, run_step, write_python3_shim

_SCRIPT = """\
import sys

import _shipmate

print(_shipmate.run(["gh", "api", *sys.argv[1:]]), end="")
"""


@bash_only
def test_a_script_run_through_the_shim_reaches_the_stub_gh_with_its_own_arguments(tmp_path):
    """The script imports its sibling with no PYTHONPATH from another cwd, sees only the two
    words it was given, and its `gh` reaches the stub.

    Mutations: leave the `gh` argv unchanged (red on Windows only, where the spawn resolves the
    real `gh.EXE`; on Linux the PATH stub answers); drop the `sys.path[0]` line; drop the
    `sys.argv` reset.
    """
    bin_dir, scripts, cwd = tmp_path / "bin", tmp_path / "scripts", tmp_path / "cwd"
    for d in (bin_dir, scripts, cwd):
        d.mkdir()
    shutil.copy(SCRIPTS / "_shipmate.py", scripts / "_shipmate.py")
    (scripts / "probe").write_text(_SCRIPT, encoding="utf-8")
    write_python3_shim(bin_dir)
    (bin_dir / "gh").write_text(
        "#!/bin/bash\nprintf '%s\\n' \"$@\"\n", encoding="utf-8", newline="\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    result = run_step(tmp_path, f'python3 "{scripts / "probe"}" "one word" two\n', env, cwd=cwd)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "api\none word\ntwo\n"

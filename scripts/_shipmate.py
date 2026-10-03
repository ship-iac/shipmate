"""Load extension-less sibling scripts as fresh modules.

``spec_from_file_location`` infers the loader from the suffix and returns None for a
suffix-less file, so the ``SourceFileLoader`` is passed explicitly. Nothing is cached in
``sys.modules``: every call returns a fresh module, so a test that monkeypatches one sibling's
``bm._run`` cannot leak the patch into every other holder of ``build_matrix``.

Also holds the subprocess runner, secret scrubber and repository-slug check that ``onboard``
and ``register-app`` share, and the UTF-8 switch for their console output. It also reads the
per-cell ``cell.json`` summaries and builds this run's page link.
"""

import glob
import importlib.util
import io
import json
import os
import pathlib
import re
import subprocess
import sys
from importlib.machinery import SourceFileLoader

_D = pathlib.Path(__file__).resolve().parent

REDACTED = "***"

#: A repository slug is interpolated into API paths, URLs and `gh --repo` arguments. Both halves
#: start alphanumeric, as GitHub logins and repository names do: that forecloses '.' and '..',
#: and a value beginning with '-' reads to a CLI as a flag.
REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9._-]*")


def scrub(text, secrets):
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


def run(args, secrets=(), stdin=None):
    r"""Run a `gh` or `git` invocation, returning stdout; raise on a nonzero exit.

    `stdin` is sent as UTF-8 bytes rather than through `text=True`, which wraps
    the child's stdin in a `TextIOWrapper` and rewrites every \n to
    `os.linesep`. The App private key goes through here on its way to
    `gh secret set`, and a CRLF-mangled PEM is not the key that was read.

    `secrets` are the live credential values this call carries, scrubbed from
    the argv and the child's stderr in the failure message: the manifest `code`
    sits in the conversions URL gh quotes, and the private key fails exactly
    when it has been minted but not yet stored. The stderr goes in the message
    so that `onboard`'s `_gh_json_or_none` can swallow a 404 without printing it.
    Nothing else prints, so a caller that discards the exception discards the
    noise too.
    """
    # args is a list run with shell=False, so no value in it is shell-parsed. Values
    # bound into a path are validated where they enter the script.
    p = subprocess.run(  # noqa: S603
        args,
        capture_output=True,
        input=None if stdin is None else stdin.encode("utf-8"),
    )
    if p.returncode != 0:
        # gh's stderr is the only diagnosis a hand-run script gets.
        stderr = p.stderr.decode("utf-8", "replace").strip()
        raise SystemExit(
            f"command failed ({p.returncode}): {scrub(' '.join(args), secrets)}"
            + (f"\n{scrub(stderr, secrets)}" if stderr else "")
        )
    return p.stdout.decode("utf-8", "replace")


def utf8_output():
    """Write stdout and stderr as UTF-8: a cp1252 Windows console prints `—` and `§` as `?`.

    A stream that is not a `TextIOWrapper` is left alone, including `None` under `pythonw`.
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")


def cell_summaries(cells_dir, keys, skew):
    """Yield ``(path, cell)`` for every ``cell.json`` under ``cells_dir``, sorted by path.

    A cell missing any of ``keys`` raises ``SystemExit`` naming it; ``skew`` ends that message
    with the producer and reader that must share one engine SHA.
    """
    for p in sorted(glob.glob(os.path.join(cells_dir, "**", "cell.json"), recursive=True)):
        with open(p, encoding="utf-8") as fh:
            cell = json.load(fh)
        missing = [k for k in keys if k not in cell]
        if missing:
            raise SystemExit(f"::error::cell summary {p} missing keys {missing} {skew}")
        yield p, cell


def current_run_url():
    """This workflow run's page, from the runner's default environment variables."""
    return (
        f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}"
        f"/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    )


def _load(fname):
    loader = SourceFileLoader(fname.replace("-", "_"), str(_D / fname))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    # spec_from_loader is typed Optional; with a real loader it never returns None.
    mod = importlib.util.module_from_spec(spec)  # ty: ignore[invalid-argument-type]
    loader.exec_module(mod)
    return mod

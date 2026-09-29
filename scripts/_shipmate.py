"""Load extension-less sibling scripts as fresh modules.

``spec_from_file_location`` infers the loader from the suffix and returns None for a
suffix-less file, so the ``SourceFileLoader`` is passed explicitly. Nothing is cached in
``sys.modules``: every call returns a fresh module, so a test that monkeypatches one sibling's
``bm._run`` cannot leak the patch into every other holder of ``build_matrix``.

Also holds the secret scrubber and repository-slug check that ``onboard`` and ``register-app``
share, and the UTF-8 switch for their console output. It also reads the per-cell
``cell.json`` summaries and builds this run's page link.
"""

import glob
import importlib.util
import io
import json
import os
import pathlib
import re
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

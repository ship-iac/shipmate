"""Only the App may dispatch an apply; a human with write access must not bypass authorize."""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2] / ".github/workflows"


def test_apply_paths_require_a_bot_actor():
    for name in ("apply.yml", "apply-all.yml"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "endsWith(github.actor, '[bot]')" in text, name

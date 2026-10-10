"""Tests for `_loader.github_outputs`, the one reader of a test's `GITHUB_OUTPUT` file."""

import pytest
from _loader import github_outputs


def test_a_line_without_an_equals_sign_raises(tmp_path):
    """A line the `key=value` form cannot hold raises rather than vanishing, so an assertion on
    the remaining keys cannot pass over it.

    Mutation: filter out lines without `=` in `github_outputs` (red).
    """
    out = tmp_path / "out.txt"
    out.write_bytes(b"authorized=false\nreason=a=b\nsecond line of the reason\n")
    with pytest.raises(ValueError):
        github_outputs(out)

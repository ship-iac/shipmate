"""actions/setup restores and saves each tool's verified release files in the Actions cache.

Threat model: an accidental regression in a SHA-pinned, reviewed engine file -- a widened
restore, a key that loses its version, a save under another path than its restore, a save that
can fail the job or that runs after a failed verify. A hostile edit to the action is out of
scope. Each of the four steps is compared whole, its `uses:` cut to the action path, against a
hand-written constant.
"""

import pytest
from _loader import step_by

_ACTION = "setup"

_TM_KEY = (
    "shipmate-tools-${{ runner.os }}-${{ runner.arch }}-terramate-"
    "${{ steps.versions.outputs.terramate }}"
)
_TOFU_KEY = (
    "shipmate-tools-${{ runner.os }}-${{ runner.arch }}-tofu-${{ steps.versions.outputs.tofu }}"
)
_TM_WITH = {"path": "${{ runner.temp }}/terramate-release", "key": _TM_KEY}
_TOFU_WITH = {"path": "${{ runner.temp }}/tofu-release", "key": _TOFU_KEY}

_EXPECTED = {
    "Restore the Terramate release": {
        "name": "Restore the Terramate release",
        "id": "terramate-cache",
        "uses": "actions/cache/restore",
        "with": _TM_WITH,
    },
    "Restore the OpenTofu release": {
        "name": "Restore the OpenTofu release",
        "id": "tofu-cache",
        "if": "${{ inputs.tofu != 'false' }}",
        "uses": "actions/cache/restore",
        "with": _TOFU_WITH,
    },
    "Save the Terramate release": {
        "name": "Save the Terramate release",
        "if": (
            "${{ inputs.cache-save != 'false' && "
            "steps.terramate-cache.outputs.cache-hit != 'true' }}"
        ),
        "continue-on-error": True,
        "uses": "actions/cache/save",
        "with": _TM_WITH,
    },
    "Save the OpenTofu release": {
        "name": "Save the OpenTofu release",
        "if": (
            "${{ inputs.tofu != 'false' && inputs.cache-save != 'false' && "
            "steps.tofu-cache.outputs.cache-hit != 'true' }}"
        ),
        "continue-on-error": True,
        "uses": "actions/cache/save",
        "with": _TOFU_WITH,
    },
}


@pytest.mark.parametrize("name", list(_EXPECTED))
def test_each_cache_step_is_pinned_whole(name):
    """Exact-match restore, the key carries the version, each save writes its restore's path and
    key only after a miss and never fails the job.

    Mutations: add `restore-keys: shipmate-tools-` to a restore; drop
    `-${{ steps.versions.outputs.tofu }}` from a key; change one save's `path:` so it no longer
    equals its restore's; drop `continue-on-error` from a save; drop the `cache-hit` clause from
    a save `if:`; prefix `always() &&` to a save `if:`.
    """
    step = dict(step_by(_ACTION, name=name))
    step["uses"] = step["uses"].split("@")[0]
    assert step == _EXPECTED[name]

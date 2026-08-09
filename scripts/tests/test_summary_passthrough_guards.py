"""The plan workflow -> summary action -> gate-state handshake.

Successor to the passthrough half of the deleted test_plan_matrix_marker.py.
Three files have to agree on five names; nothing at runtime notices when they
stop agreeing, and the failure is a gate decided from defaults.
"""

import ast
import json
import re

import pytest
import yaml
from _loader import ACTIONS, SCRIPTS

EXPECTED_GATE_ENV = {
    "SHIPMATE_DETECT_RESULT": "${{ inputs.detect-result }}",
    "SHIPMATE_PLAN_RESULT": "${{ inputs.plan-result }}",
    "SHIPMATE_PLANNED_CELLS": "${{ inputs.planned-cells }}",
    "SHIPMATE_CELL_COUNT": "${{ steps.build.outputs.count }}",
    "SHIPMATE_PENDING": "${{ steps.build.outputs.pending }}",
}


def _summary_action_gate_step():
    doc = yaml.safe_load((ACTIONS / "summary/action.yml").read_text(encoding="utf-8"))
    gate = [s for s in doc["runs"]["steps"] if s.get("id") == "gate"]
    assert len(gate) == 1, f"expected exactly one gate step, got {len(gate)}"
    return gate[0]


def test_the_action_hands_gate_state_exactly_these_env_vars():
    """The whole mapping, not a membership check: an env var added here that
    gate-state does not read, or dropped from here while gate-state still reads
    it, both end as a gate decided from a default."""
    assert _summary_action_gate_step()["env"] == EXPECTED_GATE_ENV


def _is_os_environ(node):
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _gate_state_env_reads():
    """The `SHIPMATE_*` names gate-state actually reads, from its parse tree.

    Both read forms: the two required values are subscripts, so `main()` dies
    loudly when the action stops setting them; the rest carry a default. A
    regex over the raw source cannot tell either form from the same words in a
    comment -- commenting out the `SHIPMATE_PENDING` read and hardcoding
    `pending=False` left the regex version green. An `ast` walk sees only code.
    """
    tree = ast.parse((SCRIPTS / "gate-state").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        key = None
        if isinstance(node, ast.Subscript) and _is_os_environ(node.value):
            key = node.slice
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and _is_os_environ(node.func.value)
            and node.args
        ):
            key = node.args[0]
        if isinstance(key, ast.Constant) and str(key.value).startswith("SHIPMATE_"):
            names.add(key.value)
    return names


def test_gate_state_reads_every_env_var_the_action_supplies():
    """The whole set of names gate-state reads, not a membership scan.

    Both directions: a name the action supplies that gate-state stopped reading
    (the gate then decided from that read's default), and a name gate-state
    reads that the action never sets.
    """
    assert _gate_state_env_reads() == set(EXPECTED_GATE_ENV)


def test_the_planned_count_does_not_default_to_a_number():
    """`os.environ.get("SHIPMATE_PLANNED_CELLS", "0")` would decide a quiet green
    gate from a count nobody reported. The default has to be a value _as_int
    rejects."""
    source = (SCRIPTS / "gate-state").read_text(encoding="utf-8")
    default = re.search(r'os\.environ\.get\("SHIPMATE_PLANNED_CELLS",\s*("[^"]*")\)', source)
    assert default, f"SHIPMATE_PLANNED_CELLS is not read with an explicit default:\n{source}"
    with pytest.raises(ValueError, match="invalid literal"):
        int(json.loads(default.group(1)), 10)

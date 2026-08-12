"""Guards how apply-env-level.yml's wave jobs bind a GitHub Environment.

Invariants:
- all eight wave jobs bind the shared-mode expression and nothing else: an env
  listed in vars.SHIPMATE_SHARED_ENVS binds the logical name, anything else
  falls through to <env>-apply. The fall-through is the fail-safe direction --
  the reviewer gate, the OIDC environment claim split and any environment secret
  all live on the apply environment -- and where that environment does not exist
  the apply-match fingerprint refuses the cell, on every layout that injects a
  variable (CONTRACT.md §Env model states the condition and the one layout that
  gets no refusal);
- snapshot binds no environment at all and complete binds shipmate-engine: a job
  that gains an env-derived binding is the regression;
- every wave's concurrency block still keys on the logical matrix.environment,
  so serialization does not fork by mode.

The realistic failure is accidental regression -- a reverted expression, one
missed wave, an inverted ternary -- not a hostile edit to this SHA-pinned file,
so one whole-value comparison per wave covers it.

Whole parsed values against hand-written constants, never substrings. The YAML
folded scalar (`>-`) collapses the expression's two source lines into one
space-joined string, which is why the constant below is a single line.
"""

import yaml
from _loader import WORKFLOWS

APPLY_ENV = (
    "${{ contains(format(',{0},', vars.SHIPMATE_SHARED_ENVS), "
    "format(',{0},', matrix.environment)) "
    "&& matrix.environment "
    "|| format('{0}-apply', matrix.environment) }}"
)
CONCURRENCY = {
    "group": "apply-${{ matrix.environment }}-${{ matrix.stack }}",
    "cancel-in-progress": False,
}
WAVES = [f"wave{i}" for i in range(8)]


def _jobs():
    spec = yaml.safe_load((WORKFLOWS / "apply-env-level.yml").read_text(encoding="utf-8"))
    assert isinstance(spec, dict), "apply-env-level.yml did not parse to a mapping"
    return spec["jobs"]


def _wave_jobs():
    jobs = _jobs()
    missing = [w for w in WAVES if w not in jobs]
    assert not missing, f"apply-env-level.yml lost wave jobs: {missing}"
    return {w: jobs[w] for w in WAVES}


def test_every_wave_binds_the_shared_or_apply_environment():
    for wave, job in _wave_jobs().items():
        assert job.get("environment") == APPLY_ENV, (
            f"{wave}: environment must resolve to the logical env only when it is "
            "listed in vars.SHIPMATE_SHARED_ENVS, and to <env>-apply otherwise"
        )


def test_snapshot_binds_no_environment_and_complete_binds_the_engine_environment():
    jobs = _jobs()
    # Absence asserted as absence: an explicit `environment:` with a null value
    # is a binding the caller can be made to resolve, and `.get()` would read it
    # as no key at all.
    assert "environment" not in jobs["snapshot"], (
        "snapshot must bind no environment -- it needs no secret, and an "
        "env-derived binding would subject the check snapshot to protection rules"
    )
    assert jobs["complete"].get("environment") == "shipmate-engine"


def test_every_wave_serializes_on_the_logical_environment():
    for wave, job in _wave_jobs().items():
        assert job.get("concurrency") == CONCURRENCY, (
            f"{wave}: concurrency must key on the logical matrix.environment -- a "
            "mode-dependent group would let a shared-mode and a split-mode apply "
            "run against the same stack at once"
        )

from _loader import ENGINE as _ENGINE


def _sources():
    """Every engine file that could issue a check-runs listing: helper scripts,
    composite actions, and reusable workflows. Scanned rather than enumerated
    (same pattern as `test_internal_pins._self_refs`), so a new site cannot be
    added without this guard seeing it. scripts/ is globbed non-recursively so
    this test dir is not itself scanned."""
    return (
        sorted((_ENGINE / "scripts").glob("*"))
        + sorted((_ENGINE / "actions").glob("*/action.yml"))
        + sorted((_ENGINE / ".github" / "workflows").glob("*.yml"))
    )


def test_every_check_runs_query_uses_filter_all():
    # A commit's check-runs *listing* (`.../check-runs?...`) must use filter=all
    # so the gate (gate-refresh) and the detects judge the identical run set per
    # name. A POST-create (`/check-runs` with no `?`) and a PATCH-by-id
    # (`/check-runs/<id>`) are not listings and correctly do not match.
    offenders = []
    for path in _sources():
        if not path.is_file():
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "/check-runs?" in line and "filter=all" not in line:
                offenders.append(f"{path.relative_to(_ENGINE).as_posix()}:{n}: {line.strip()}")
    assert offenders == [], "check-runs listing missing filter=all:\n" + "\n".join(offenders)

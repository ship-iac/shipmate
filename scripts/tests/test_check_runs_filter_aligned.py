import pathlib

_ENGINE = pathlib.Path(__file__).resolve().parents[2]


def _sources():
    """Every engine file that could issue a check-runs listing: helper scripts,
    composite actions, and reusable workflows. The tree is *scanned*, not read
    from a hand-maintained registry, so a new site cannot be added without this
    guard seeing it (the failure mode of the old hardcoded `_SITES` list:
    coupling row 9 could silently drift the moment someone forgot to register a
    file). Mirrors `test_internal_pins._self_refs`'s scan-don't-enumerate pattern.
    scripts/ is globbed non-recursively so this test dir is not itself scanned."""
    return (
        sorted((_ENGINE / "scripts").glob("*"))
        + sorted((_ENGINE / "actions").glob("*/action.yml"))
        + sorted((_ENGINE / ".github" / "workflows").glob("*.yml"))
    )


def test_every_check_runs_query_uses_filter_all():
    # A commit's check-runs *listing* (`.../check-runs?...`) must use filter=all so
    # the gate (checkmate-refresh) and the detects judge the identical run set per
    # name — coupling row 9 (apply "done" predicate). A POST-create (`/check-runs`
    # with no `?`) and a PATCH-by-id (`/check-runs/<id>`) are not listings and
    # correctly do not match the `/check-runs?` needle.
    offenders = []
    for path in _sources():
        if not path.is_file():
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "/check-runs?" in line and "filter=all" not in line:
                offenders.append(f"{path.relative_to(_ENGINE).as_posix()}:{n}: {line.strip()}")
    assert offenders == [], "check-runs listing missing filter=all:\n" + "\n".join(offenders)

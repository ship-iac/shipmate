import pathlib

_ENGINE = pathlib.Path(__file__).resolve().parents[2]

# Every shipmate call that lists a commit's check-runs must use filter=all so the
# gate (checkmate-refresh) and the detects judge the identical run set per name.
# Coupling row 9 (apply "done" predicate). Add a site here when a new one appears.
_SITES = [
    _ENGINE / "scripts" / "apply-detect",
    _ENGINE / "scripts" / "deploy-detect",
    _ENGINE / "actions" / "checkmate-refresh" / "action.yml",
    _ENGINE / "actions" / "apply-cell" / "action.yml",
]


def test_every_check_runs_query_uses_filter_all():
    offenders = []
    for path in _SITES:
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "/check-runs?" in line and "filter=all" not in line:
                offenders.append(f"{path.name}: {line.strip()}")
    assert not offenders, "check-runs query missing filter=all:\n" + "\n".join(offenders)

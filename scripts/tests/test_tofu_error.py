"""scripts/tofu-error: annotate a failed plan or init with OpenTofu's first error diagnostic.

Fixture provenance: `plan_error_two_ansi.txt` is OpenTofu v1.12.4's stderr from
`tofu plan` over two outputs reading undeclared variables, on Windows, written
straight to a file (no TTY). The escape bytes are intact: OpenTofu colours its
diagnostics into a file too, as `lock-info` records for a runner.

`init_error_cache_mismatch_ansi.txt` and `init_error_backend_ansi.txt` are the combined output
of `terramate run ... -- tofu init -input=false -reconfigure` with OpenTofu v1.13.1 on Windows,
redirected to a file (no TTY): the first over a corrupted package in `TF_PLUGIN_CACHE_DIR`, the
second over an S3 backend with invalid credentials. Two substitutions, no other byte changed:
the local scratch directory prefix, in both its `/` and `\\` forms, became
`/home/runner/work/_temp`, so terramate's Windows tofu path is now the mixed-separator
`/home/runner/work/_temp\\tofu113\\tofu.exe` and the cache path keeps `windows_amd64`; and the
AWS `RequestID` became `00000000-0000-0000-0000-000000000000`.
"""

import io
import pathlib

import pytest
from _loader import load_script, run_lines, step_by

te = load_script("tofu-error")
FIX = pathlib.Path(__file__).parent / "fixtures"

FIRST = (
    "::error::Error: Reference to undeclared input variable%0A"
    "%0A"
    'on main.tf line 2, in output "x":%0A'
    "2:   value = var.first_missing%0A"
    "%0A"
    'An input variable with the name "first_missing" has not been declared. This%0A'
    'variable can be declared with a variable "first_missing" {} block.\n'
)


def _run(monkeypatch, capsys, text, argv=()):
    data = text if isinstance(text, bytes) else text.encode()
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(data), encoding="utf-8"))
    te.main(argv)
    return capsys.readouterr().out


def test_annotates_the_first_diagnostic_whole(monkeypatch, capsys):
    """One annotation, the first block only, no colour, newlines escaped. Mutations: drop the
    ANSI strip (`li._strip` -> `str.strip`); print `error` without `command_data`; take the last
    `Error:` line instead of the first; drop the `╵` stop."""
    raw = (FIX / "plan_error_two_ansi.txt").read_text(encoding="utf-8")
    assert _run(monkeypatch, capsys, raw) == FIRST


def test_prints_nothing_without_an_error_line(monkeypatch, capsys):
    """A failure outside OpenTofu keeps only the runner's own annotation. Mutation: print an
    `::error::` when `first_error` is empty."""
    assert _run(monkeypatch, capsys, "terramate: stack not found\n") == ""


def test_escapes_percent_and_cuts_at_cap(monkeypatch, capsys):
    """Mutations: drop the `%` escape (`command_data` -> identity); drop the `[:CAP]` cut."""
    out = _run(monkeypatch, capsys, "╷\n│ Error: 100% " + "x" * 5000 + "\n╵\n")
    assert out == "::error::Error: 100%25 " + "x" * (te.CAP - len("Error: 100% ")) + "\n"


def test_skips_an_error_line_that_opens_no_box(monkeypatch, capsys):
    """A warning's detail and a log line starting `Error: ` are not the diagnostic. Mutation:
    drop the `lines[i - 1] == "╷"` condition."""
    text = (
        "Error: from a log line\n"
        "╷\n│ Warning: deprecated\n│ Error: quoted in the detail\n╵\n"
        "╷\n│ Error: the real one\n╵\n"
    )
    assert _run(monkeypatch, capsys, text) == "::error::Error: the real one\n"


def test_annotates_despite_a_byte_that_is_not_utf8(monkeypatch, capsys):
    """Provider output can carry any byte. Mutation: decode stdin strictly."""
    out = _run(monkeypatch, capsys, "╷\n│ Error: bad ".encode() + b"\xff byte\n")
    assert out == "::error::Error: bad � byte\n"


#: Hand-written: the line both plan steps run after a failed plan.
ANNOTATE = (
    '[ "$status" -eq 0 ] || python3 "$GITHUB_ACTION_PATH/../../scripts/tofu-error"'
    ' < "$RUNNER_TEMP/plan-run.txt" || true'
)


@pytest.mark.parametrize(
    ("action", "step"), [("plan-cell", "Plan"), ("drift-cell", "Plan + classify drift")]
)
def test_each_plan_step_annotates_after_the_group_and_never_masks_the_status(action, step):
    """The annotation runs once, right after `::endgroup::`, and neither an extractor failure nor
    `tee`'s own status can change the step's exit status. Mutations: drop `|| true`; move the
    line above `echo "::endgroup::"`; delete it from drift-cell; read `status=$?`."""
    lines = run_lines(step_by(action, name=step))
    plan = next(i for i, ln in enumerate(lines) if ln.startswith("terramate run"))
    assert lines[plan + 1] == "status=${PIPESTATUS[0]}"
    end = lines.index('echo "::endgroup::"')
    assert lines[end + 1] == ANNOTATE
    assert lines.count(ANNOTATE) == 1


#: Hand-written: the whole first diagnostic of the backend fixture.
BACKEND = (
    "::error::Error: validating provider credentials: retrieving caller identity from STS:"
    " operation error STS: GetCallerIdentity, https response error StatusCode: 403, RequestID:"
    " 00000000-0000-0000-0000-000000000000, api error InvalidClientTokenId: The security token"
    " included in the request is invalid.\n"
)

KEY = "tofu-providers-Linux-X64-abc123"
CACHE_ENTRY = ("cache-entry", KEY)


def _fixture(name):
    return (FIX / name).read_bytes()


def test_annotates_a_failed_init_with_its_own_first_error(monkeypatch, capsys):
    """Mutation: print the cache annotation in plain mode too."""
    assert _run(monkeypatch, capsys, _fixture("init_error_backend_ansi.txt")) == BACKEND


def test_names_the_cache_entry_for_a_provider_install_failure(monkeypatch, capsys):
    """Mutation: drop `--repo {repo}` from the remedy."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    out = _run(monkeypatch, capsys, _fixture("init_error_cache_mismatch_ansi.txt"), CACHE_ENTRY)
    assert out == (
        "::error::OpenTofu's init failed after restoring provider cache entry "
        "tofu-providers-Linux-X64-abc123. If the log above says a cached package does not "
        "match the content of the downloaded package, delete the entry with gh cache delete "
        "tofu-providers-Linux-X64-abc123 --repo o/r and re-run.\n"
    )


REMEDY_NO_REPO = (
    "::error::OpenTofu's init failed after restoring provider cache entry "
    "tofu-providers-Linux-X64-abc123. If the log above says a cached package does not "
    "match the content of the downloaded package, delete the entry with gh cache delete "
    "tofu-providers-Linux-X64-abc123 --repo  and re-run.\n"
)


def test_the_remedy_prints_without_github_repository(monkeypatch, capsys):
    """Mutation: read `os.environ["GITHUB_REPOSITORY"]` (KeyError, no remedy)."""
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    out = _run(monkeypatch, capsys, _fixture("init_error_cache_mismatch_ansi.txt"), CACHE_ENTRY)
    assert out == REMEDY_NO_REPO


@pytest.mark.parametrize(
    "text",
    [
        (
            "╷\n│ Error: Invalid provider configuration\n╵\n"
            "╷\n│ Error: Failed to install provider\n│\n│ detail\n╵\n"
        ),
        "╷\n│ Error: Failed to install provider from shared cache\n╵\n",
    ],
    ids=["second-box", "longer-title"],
)
def test_names_the_cache_entry_for_any_install_failure_title(monkeypatch, capsys, text):
    """Mutations: check the first diagnostic only (reds `second-box`); compare the title with `==`
    (reds `longer-title`)."""
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert _run(monkeypatch, capsys, text, CACHE_ENTRY) == REMEDY_NO_REPO


@pytest.mark.parametrize(
    "text",
    [
        _fixture("init_error_backend_ansi.txt"),
        _fixture("plan_error_two_ansi.txt"),
        b"",
        "╷\n│ Error: Failed to query available provider packages\n│\n"
        "│ Failed to install provider from the registry.\n╵\n".encode(),
    ],
    ids=["backend", "plan", "empty", "phrase-in-detail"],
)
def test_names_no_cache_entry_for_any_other_failure(monkeypatch, capsys, text):
    """A backend or credential failure gets OpenTofu's diagnostic and no cache remedy. Mutations:
    drop the title check (reds `backend` and `plan`); match `Failed to install provider` anywhere
    in the block (reds `phrase-in-detail` only: no fixture carries the phrase outside the title)."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    assert _run(monkeypatch, capsys, text, CACHE_ENTRY) == ""


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (
            ("cache-entry",),
            "::error::usage: tofu-error [cache-entry <key>] (got ['cache-entry'])",
        ),
        (
            ("cache-entry", KEY, "x"),
            (
                "::error::usage: tofu-error [cache-entry <key>] "
                "(got ['cache-entry', 'tofu-providers-Linux-X64-abc123', 'x'])"
            ),
        ),
        (
            ("cache", KEY),
            (
                "::error::usage: tofu-error [cache-entry <key>] "
                "(got ['cache', 'tofu-providers-Linux-X64-abc123'])"
            ),
        ),
    ],
    ids=["one", "three", "cache"],
)
def test_refuses_any_other_arguments(monkeypatch, capsys, argv, code):
    """Mutations: drop the `argv[0] != "cache-entry"` clause (reds `cache`); change the usage
    text (reds all three)."""
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, capsys, b"", argv)
    assert exc.value.code == code


#: Hand-written: the line `cell-init`'s init step runs after a failed init.
ANNOTATE_INIT = (
    '[ "$status" -eq 0 ] || python3 "$GITHUB_ACTION_PATH/../../scripts/tofu-error"'
    ' < "$RUNNER_TEMP/init.txt" || true'
)


def test_the_init_step_annotates_before_its_exit_and_never_masks_the_status():
    """The annotation runs once, before the step exits on tofu's own status, and neither an
    extractor failure nor `tee`'s own status can change it. Mutations: drop `|| true`; read
    `status=$?`; read `plan-run.txt`; move the line below `exit "$status"`."""
    lines = run_lines(step_by("cell-init", id="init"))
    init = next(i for i, ln in enumerate(lines) if ln.startswith("terramate run"))
    assert lines[init + 1] == "status=${PIPESTATUS[0]}"
    assert lines.count(ANNOTATE_INIT) == 1
    assert lines.index(ANNOTATE_INIT) < lines.index('exit "$status"')

"""Load extension-less sibling scripts as fresh modules.

``spec_from_file_location`` infers the loader from the suffix and returns None for a
suffix-less file, so the ``SourceFileLoader`` is passed explicitly. Nothing is cached in
``sys.modules``: every call returns a fresh module, so a test that monkeypatches one sibling's
``bm._run`` cannot leak the patch into every other holder of ``build-matrix``.

Also holds the subprocess runner, its ``::error::`` wrapper and ``gh api`` reader for the CI
scripts, the workflow-command data and property escapers and the annotated scripts' refusal
printer, the secret scrubber and repository-slug check that ``onboard`` and ``register-app``
share, and the UTF-8 switch for their console output. It also holds the pull-request number
check that ``pr-facts``, ``reply-comment`` and ``upsert-comment`` share, the head-SHA check and
the one check-runs listing every reader of a head's checks uses, the ruleset, environment and
``CODEOWNERS`` readers and the names ``doctor`` and ``onboard`` share, reads the per-cell
``cell.json`` summaries, builds this run's page link, joins a capped list of names and parses a
list of cell names. It retries a ``gh`` read that failed transiently. It parses YAML: the
strings-only config loader and the YAML 1.2 workflow loader, both importing PyYAML only when
first called.
"""

import functools
import glob
import importlib.util
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.parse
from importlib.machinery import SourceFileLoader
from typing import TypeGuard

_D = pathlib.Path(__file__).resolve().parent

REDACTED = "***"
#: GitHub keeps at most 10 annotations per level per step.
ANNOTATION_CAP = 10

#: A repository slug is interpolated into API paths, URLs and `gh --repo` arguments. Both halves
#: start alphanumeric, as GitHub logins and repository names do: that forecloses '.' and '..',
#: and a value beginning with '-' reads to a CLI as a flag.
REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9._-]*")
ENGINE_ENV = "shipmate-engine"
#: The commit-status context of the merge gate. `scripts/gate-status` is its one writer.
GATE_CONTEXT = "shipmate / gate"
# Named `APP_KEY_NAME`, not `..._SECRET`: ruff's S105 hardcoded-password rule fires on a
# "SECRET" token in the binding name, and this is a secret's *name*.
APP_KEY_NAME = "SHIPMATE_APP_PRIVATE_KEY"
#: The repository variable onboard and register-app write the App id to. Workflows hand it to
#: the run-time scripts under the same literal name (`test_detect_app_scoping.py`).
APP_ID_VAR = "SHIPMATE_APP_ID"
_PR_NUMBER = re.compile(r"[1-9][0-9]{0,9}")
_HEAD_SHA = re.compile(r"[0-9a-f]{40}")
_RUN_ID = re.compile(r"[0-9]{1,20}")
#: The default projection of `check_runs`: each check-run object whole.
CHECK_RUNS_JQ = ".check_runs[]"


def gate_check(rules, gate):
    """(the `gate` entry, its rule's parameters) from a `rules/branches` response, or
    (None, None) when no `required_status_checks` rule requires `gate`."""
    for rule in rules:
        if rule.get("type") != "required_status_checks":
            continue
        params = rule.get("parameters") or {}
        for chk in params.get("required_status_checks") or []:
            if chk.get("context") == gate:
                return chk, params
    return None, None


def review_count(rules):
    """The highest `required_approving_review_count` over the `pull_request` rules, 0 if none."""
    pull = [r.get("parameters") or {} for r in rules if r.get("type") == "pull_request"]
    return max((p.get("required_approving_review_count") or 0 for p in pull), default=0)


def code_owner_review(rules):
    """True when any `pull_request` rule in a `rules/branches` response requires code-owner
    review."""
    return any(
        (r.get("parameters") or {}).get("require_code_owner_review")
        for r in rules
        if r.get("type") == "pull_request"
    )


#: GitHub's own lookup order for the file: the first one present is the one it reads.
CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")


#: Where a `CODEOWNERS` comment starts: a `#` not escaped by `\`.
_CODEOWNERS_COMMENT = re.compile(r"(?<!\\)#")


def codeowners_rules(text):
    """True when `CODEOWNERS` text holds a rule line: a pattern and at least one owner once
    any `#` comment is cut off. A pattern with no owner un-owns its paths (GitHub), so it owns
    nothing. Which paths a rule matches is GitHub's matching, not read here."""
    return any(len(_CODEOWNERS_COMMENT.split(ln, 1)[0].split()) > 1 for ln in text.splitlines())


def first_codeowners(read):
    """`(path, owns)` for the first of `CODEOWNERS_PATHS` that `read(path)` returns text for,
    `owns` being `codeowners_rules` of it; `(None, False)` when `read` returns None for all.
    `read` raises for a failure that is not absence."""
    for path in CODEOWNERS_PATHS:
        text = read(path)
        if text is not None:
            return path, codeowners_rules(text)
    return None, False


def approval_rules(env):
    """The sorted, distinct protection-rule types in an environment payload that stop a job
    from starting. GitHub synthesizes a `branch_policy` rule for any deployment branch policy;
    that is the policy itself, not a review, and it stalls nothing. A rule with no type is
    listed as `?`: it still stalls a job, but it confirms no reviewer or wait timer, so a
    caller asking whether an environment is reviewed must ignore it."""
    types = {r.get("type") or "?" for r in env.get("protection_rules") or []}
    return sorted(types - {"branch_policy"})


def scrub(text, secrets):
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


def run(args, secrets=(), stdin=None):
    r"""Run a `gh`, `git` or `terramate` invocation, returning stdout; raise on a nonzero exit.

    `stdin` is sent as UTF-8 bytes rather than through `text=True`, which wraps
    the child's stdin in a `TextIOWrapper` and rewrites every \n to
    `os.linesep`. The App private key goes through here on its way to
    `gh secret set`, and a CRLF-mangled PEM is not the key that was read.

    `secrets` are the live credential values this call carries, scrubbed from
    the argv and the child's stderr in the failure message: the manifest `code`
    sits in the conversions URL gh quotes, and the private key fails exactly
    when it has been minted but not yet stored. The stderr goes in the message
    so that `onboard`'s `_gh_json_or_none` can swallow a 404 without printing it.
    Nothing else prints, so a caller that discards the exception discards the
    noise too.
    """
    # args is a code-controlled gh/git/terramate argv run with shell=False, so no value in
    # it is shell-parsed. Values bound into a path are validated where they enter the script.
    p = subprocess.run(  # noqa: S603
        args,
        capture_output=True,
        input=None if stdin is None else stdin.encode("utf-8"),
    )
    if p.returncode != 0:
        # gh's stderr is the only diagnosis a hand-run script gets.
        stderr = p.stderr.decode("utf-8", "replace").strip()
        raise SystemExit(
            f"command failed ({p.returncode}): {scrub(' '.join(args), secrets)}"
            + (f"\n{scrub(stderr, secrets)}" if stderr else "")
        )
    return p.stdout.decode("utf-8", "replace")


class StackCycle(SystemExit):
    """`waves.stack_levels`' refusal of a Terramate stack ordering cycle: a `SystemExit`, typed
    so `deploy-detect` can append the cells it leaves pending. Homed here so every module that
    loads `waves` raises and catches one class."""


def _run(args):
    """stdout of `args` through `run`, its failure re-raised as an ::error:: annotation.
    `run` stays unprefixed for the hand-run scripts that share it.

    Homed here because every CI script already imports this module, so reaching it takes no
    `_load` chain.
    """
    try:
        return run(args)
    except SystemExit as e:
        raise SystemExit(f"::error::{e}") from None


#: gh's text for a failure a later attempt can outlast; never a 4xx, whose answer a retry keeps.
#: Captured raw from gh: `connection refused`. Quoted by a consumer: `Something went wrong`,
#: `TLS handshake timeout`, `unexpected end of JSON input`. The rest: 5xx and Go transport errors.
TRANSIENT = re.compile(
    r"HTTP 5\d\d|Something went wrong|connection refused|connection reset|i/o timeout"
    r"|TLS handshake timeout|unexpected EOF|unexpected end of JSON input"
    # Go's net/http transport errors as gh prints them: `Get "<url>": EOF` and the rest.
    r"|: EOF$|Client\.Timeout exceeded|context deadline exceeded|GOAWAY",
    re.MULTILINE,
)
#: The sleeps before the second and third attempts.
RETRY_DELAYS = (2, 5)
_sleep = time.sleep


def is_transient(exc):
    """True when `exc`, a `SystemExit`, carries a `TRANSIENT` failure under its first line: that
    line is the argv, which can carry a consumer's text."""
    if not isinstance(exc.code, str):
        return False
    return TRANSIENT.search("\n".join(exc.code.splitlines()[1:])) is not None


def warn_retry(exc):
    """Print `exc`'s first two lines (the command and gh's first stderr line) as one `::warning::`
    on stderr, never stdout: `gate-status checks` writes data there."""
    head = "\n".join(str(exc.code).removeprefix("::error::").splitlines()[:2])
    sys.stderr.write(f"::warning::{command_data(head)}\n")
    sys.stderr.flush()


def retried(call, before_retry=None):
    """`call()`, called again after each `RETRY_DELAYS` sleep while it raises a transient
    `SystemExit` (`is_transient`), each retry warned (`warn_retry`). `before_retry`, when given,
    runs after each sleep and may raise to stop. Any other `SystemExit` re-raises at once; the
    third transient one re-raises unchanged. A write can land and still fail, so a retried write
    can overwrite what another run wrote since; a caller that must not passes `before_retry`."""
    for delay in RETRY_DELAYS:
        try:
            return call()
        except SystemExit as e:
            if not is_transient(e):
                raise
            warn_retry(e)
        _sleep(delay)
        if before_retry:
            before_retry()
    return call()


def command_data(s):
    """`s` escaped as workflow-command data (`%`, CR, LF): a raw newline in an author-controlled
    name would start a second workflow command."""
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def command_property(s):
    """`s` escaped as a workflow-command property value, such as `file=`: properties escape `:`
    and `,` too, unlike data, because `,` separates properties and `::` ends them."""
    return command_data(s).replace(":", "%3A").replace(",", "%2C")


#: A refusal output and a fenced reason are cut here: a comment carrying one stays far under its
#: 65,536-character cap, and an env var holding one far under Linux's 128 KiB per-string limit.
REASON_CAP = 4000


def is_refusal(code) -> TypeGuard[str]:
    """True when a `SystemExit` code is an `::error::` refusal: a `str` starting `::error::`."""
    return isinstance(code, str) and code.startswith("::error::")


def run_annotated(main, *args, refusal_output=False):
    """Call `main(*args)`; print an `::error::` exit as one escaped workflow command per error.

    The runner parses workflow commands per line, so a raw multi-line refusal annotates only its
    first line. The code splits into lines with `splitlines`, so CR, LF and CRLF each end a
    line on both surfaces: the annotation and the `refusal` output. A new
    error starts at each line starting `::error::` (joined refusals keep one annotation each),
    every other line stays with the error above it, trailing empty lines are dropped, and each
    error prints through `command_data`, which also keeps a literal `%0A` or `%25` in a stack
    path verbatim. Exits 1 after printing. Any other `SystemExit` passes through unchanged.
    Producers stay raw: this is the one print site of an annotated script's refusal.

    With `refusal_output`, a refusal also writes the step output `refusal`: the first error on
    one line, each of its lines stripped and joined by a space, unescaped and cut at `REASON_CAP`
    characters, so a failed command keeps the stderr cause under its first line. It is written
    after the annotations, so a failing write cannot cost them. This is the one writer of that
    output.
    """
    try:
        main(*args)
    except SystemExit as exc:
        code = exc.code
        if not is_refusal(code):
            raise
        errors = []
        for line in code.splitlines():
            if line.startswith("::error::"):
                errors.append([line.removeprefix("::error::")])
            else:
                errors[-1].append(line)
        for lines in errors:
            message = "\n".join(lines).rstrip("\n")
            sys.stderr.write(f"::error::{command_data(message)}\n")
        sys.stderr.flush()
        if refusal_output:
            # A `GITHUB_OUTPUT` line written before the step fails still reaches the job outputs.
            write_outputs(refusal=" ".join(filter(None, map(str.strip, errors[0])))[:REASON_CAP])
        raise SystemExit(1) from None


def gh_json(path, run=None):
    """`gh api <path>` -> parsed JSON, through `run` so a nonzero exit raises ::error::
    with the tool's stderr in the message, from a single definition, so no caller repeats the
    `json.loads(_run(...))` pair.

    Homed beside `_run` for the same reason.
    """
    return json.loads((run or _run)(["gh", "api", path]))


def write_outputs(**pairs):
    """Append each `name=value` pair to the calling step's `GITHUB_OUTPUT`, a bool as
    `true`/`false`. A value holding CR or LF would end its line early."""
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
        for name, value in pairs.items():
            if isinstance(value, bool):
                value = "true" if value else "false"
            fh.write(f"{name}={value}\n")


def event_payload(path):
    """The event payload at `path`, or {} when `path` is empty or the file cannot be read as a
    JSON object."""
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def event_action():
    """The `action` of the event payload at `GITHUB_EVENT_PATH`, or "" when it cannot be read
    or names none, as a `workflow_dispatch` payload does."""
    action = event_payload(os.environ.get("GITHUB_EVENT_PATH", "")).get("action")
    return action if isinstance(action, str) else ""


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
    with the producer and reader that must share one engine SHA. Recursive because
    ``actions/download-artifact`` drops the per-artifact subdirectory when one artifact matches.
    """
    for p in sorted(glob.glob(os.path.join(cells_dir, "**", "cell.json"), recursive=True)):
        with open(p, encoding="utf-8") as fh:
            cell = json.load(fh)
        missing = [k for k in keys if k not in cell]
        if missing:
            raise SystemExit(f"::error::cell summary {p} missing keys {missing} {skew}")
        yield p, cell


def is_pr_number(value):
    """Whether `value` is a pull-request number as GitHub numbers them.

    Bounded and anchored, with no leading zero, because callers bind it into an API path.
    """
    return _PR_NUMBER.fullmatch(value) is not None


def is_run_id(value):
    """Whether `value` is a workflow run id: 1 to 20 digits, bounded because it is bound into a
    link."""
    return _RUN_ID.fullmatch(value) is not None


def is_head_sha(value):
    """Whether `value` is a str of exactly 40 lowercase hex characters."""
    return isinstance(value, str) and _HEAD_SHA.fullmatch(value) is not None


def validate_head_sha(value, source="SHIPMATE_HEAD_SHA", *, annotate=True):
    """Refuse a head SHA that is not 40 lowercase hex characters, so a malformed value
    cannot retarget the gh api URLs built from it (e.g. `/`, `?`, `..`). `source` names
    where the value came from in the refusal, prefixed `::error::` unless `annotate` is
    false. Safe to echo — not a secret."""
    if not is_head_sha(value):
        message = f"{source} must be a 40-char lowercase hex SHA (got: {value!r})"
        raise SystemExit(f"::error::{message}" if annotate else message)


def check_runs(repo, sha, jq=CHECK_RUNS_JQ, run=None, name=None):
    """Every check-run on `sha`, or only those named `name`, as `gh api --jq` output: one `jq`
    projection per line.

    `sha` is refused before any call unless it is a head SHA. `filter=all`, because a
    re-created check name keeps its older runs and `apply-gate` judges the newest per name, so
    every reader must see the same set. `--paginate --jq` prints one JSON object per line
    across every page, so a head with more than 100 checks drops none. `run` defaults to
    `_run`. A transient failure is retried (`retried`)."""
    validate_head_sha(sha, "head SHA")
    query = "filter=all&per_page=100"
    if name is not None:
        query += f"&check_name={urllib.parse.quote(name, safe='')}"
    args = ["gh", "api", "--paginate", f"repos/{repo}/commits/{sha}/check-runs?{query}", "--jq", jq]
    return retried(lambda: (run or _run)(args))


def named(items, cap):
    """`items` joined with `, `: the first `cap`, the rest as `, and N more`."""
    listed = ", ".join(items[:cap])
    if len(items) > cap:
        listed += f", and {len(items) - cap} more"
    return listed


def cell_names(raw):
    """`raw` as a JSON list of `{stack, environment}` objects with string values, each reduced to
    those two keys, or None when it is anything else (empty included)."""
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(value, list) or not all(
        isinstance(c, dict)
        and isinstance(c.get("stack"), str)
        and isinstance(c.get("environment"), str)
        for c in value
    ):
        return None
    return [{"stack": c["stack"], "environment": c["environment"]} for c in value]


def current_run_url(run_id=None):
    """The page of run `run_id` of this repository, or of this workflow run when it is None, from
    the runner's default environment variables. `run_id` must already be checked (`is_run_id`)."""
    run_id = os.environ["GITHUB_RUN_ID"] if run_id is None else run_id
    return (
        f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{run_id}"
    )


_CORE_RESOLVERS = (
    ("tag:yaml.org,2002:bool", r"^(?:true|True|TRUE|false|False|FALSE)$", "tTfF"),
    ("tag:yaml.org,2002:int", r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$", "-+0123456789"),
    (
        "tag:yaml.org,2002:float",
        (
            r"^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?"
            r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$"
        ),
        "-+0123456789.",
    ),
    ("tag:yaml.org,2002:null", r"^(?:~|null|Null|NULL|)$", ("~", "n", "N", "")),
)


def _core_int(loader, node):
    # SafeLoader's own int constructor reads `08` as YAML 1.1 octal and raises.
    value = loader.construct_scalar(node)
    if value.startswith(("0o", "0x")):
        return int(value[2:], 8 if value[1] == "o" else 16)
    return int(value, 10)


def _untagged(loader, _suffix, node):
    if node.id == "mapping":
        return loader.construct_mapping(node, deep=True)
    if node.id == "sequence":
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)


def _refuse(cls, message, mark):
    raise cls(None, None, message, mark)


def _check_keys(node, key_of):
    """Refuse a non-scalar key before `key_of` constructs it, then a duplicate of `key_of`.
    A node that is no mapping (`!!map [1]`) is left to the constructor's own refusal."""
    from yaml.constructor import ConstructorError

    if node.id != "mapping":
        return
    seen = set()
    for key, _ in node.value:
        if key.id != "scalar":
            _refuse(ConstructorError, "a key must be a plain value", key.start_mark)
        value = key_of(key)
        if value in seen:
            _refuse(ConstructorError, f"duplicate key {value!r}", key.start_mark)
        seen.add(value)


def _construct_object(self, node, deep=False):
    """`WorkflowLoader.construct_object`: SafeLoader's, refusing as a `ConstructorError` at
    `node` the plain exceptions its scalar constructors raise on an explicit tag with a bad
    value (`!!int abc`), which a caller catching `YAMLError` would miss. The value is cut to
    80 characters, because the refusal reaches a pull request comment whole."""
    from yaml import SafeLoader
    from yaml.constructor import ConstructorError

    try:
        return SafeLoader.construct_object(self, node, deep)
    except (ValueError, LookupError, AttributeError, TypeError):
        if node.id != "scalar":
            raise
        tag = node.tag.replace("tag:yaml.org,2002:", "!!")
        value = node.value if len(node.value) <= 80 else node.value[:80] + "..."
        _refuse(ConstructorError, f"{value!r} is not a valid {tag}", node.start_mark)


@functools.cache
def _loaders():
    import yaml
    from yaml.composer import ComposerError

    class ConfigLoader(yaml.BaseLoader):
        # An AliasEvent carries an `anchor` too, so the alias check comes first or every
        # alias is reported as an anchor.
        def compose_node(self, parent, index):
            event = self.peek_event()
            if isinstance(event, yaml.AliasEvent):
                _refuse(ComposerError, "an alias is not allowed", event.start_mark)
            if getattr(event, "anchor", None) is not None:
                _refuse(ComposerError, "an anchor is not allowed", event.start_mark)
            if getattr(event, "tag", None) is not None:
                _refuse(
                    ComposerError,
                    f"a tag ({event.tag}) is not allowed",
                    event.start_mark,
                )
            return super().compose_node(parent, index)

        def construct_mapping(self, node, deep=False):
            _check_keys(node, lambda key: key.value)
            return super().construct_mapping(node, deep)

    class WorkflowLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            _check_keys(node, lambda key: self.construct_object(key, deep=True))
            return super().construct_mapping(node, deep)

    WorkflowLoader.yaml_implicit_resolvers = {}
    for tag, pattern, first in _CORE_RESOLVERS:
        WorkflowLoader.add_implicit_resolver(tag, re.compile(pattern), list(first))
    WorkflowLoader.add_constructor("tag:yaml.org,2002:int", _core_int)
    WorkflowLoader.add_multi_constructor("!", _untagged)
    WorkflowLoader.construct_object = _construct_object
    return ConfigLoader, WorkflowLoader


def _parse(loader_class, text):
    """The single document in `text`. Composing and constructing recurse once per nesting
    level, so a document nested past Python's recursion limit is refused as a `YAMLError`
    at the point the reader reached, like any other document that does not parse."""
    from yaml.composer import ComposerError

    loader = loader_class(text)
    try:
        return loader.get_single_data()
    except RecursionError:
        _refuse(ComposerError, "the document nests too deeply", loader.get_mark())
    finally:
        loader.dispose()


def load_config_text(text):
    """Parse the shipmate config: every scalar is a string, an empty document is `{}`.

    Built on `yaml.BaseLoader`, which has no implicit types and no merge keys, so `<<` is an
    ordinary key and the duplicate-key check sees every key a mapping holds. Aliases, anchors
    and explicit tags are refused while composing; a duplicate or non-scalar key while
    constructing. A leading U+FEFF and CRLF line ends are accepted. Raises `yaml.YAMLError`,
    and `ModuleNotFoundError` when PyYAML is absent.
    """
    doc = _parse(_loaders()[0], text)
    return {} if doc is None else doc


def load_workflow_text(text):
    """Parse a GitHub workflow or action file by the YAML 1.2 core schema, as GitHub does.

    `true`/`false` in three casings are booleans; decimal, `0o` and `0x` integers, floats and
    `null`/`~`/empty are typed, and `08` is the integer 8; every other scalar, `on`, `yes`,
    `no` and `1_000` included, is a string. Aliases resolve, an unknown local tag (`!foo`) reads
    as untagged, `<<` is an ordinary key, and a duplicate or non-scalar key is refused. Raises
    `yaml.YAMLError`, and `ModuleNotFoundError` when PyYAML is absent.
    """
    return _parse(_loaders()[1], text)


def yaml_error_text(exc):
    """`exc` on one line: `{problem} (line {L}, column {C})`, 1-based, from a marked error;
    otherwise `str(exc)` with its newlines replaced by spaces."""
    mark = getattr(exc, "problem_mark", None)
    if mark is None:
        return str(exc).replace("\n", " ")
    return f"{exc.problem} (line {mark.line + 1}, column {mark.column + 1})"


def _load(fname):
    loader = SourceFileLoader(fname.replace("-", "_"), str(_D / fname))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    # spec_from_loader is typed Optional; with a real loader it never returns None.
    mod = importlib.util.module_from_spec(spec)  # ty: ignore[invalid-argument-type]
    loader.exec_module(mod)
    return mod

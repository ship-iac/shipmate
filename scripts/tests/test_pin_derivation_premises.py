"""The premises the internal-pin guard's dependency derivation rests on.

Separate module from ``test_internal_pins.py`` on purpose. That module reads git
history and is red by design on a pin-bump PR, so PR CI ``--ignore``s it and it
runs only on push to main (``.github/workflows/ci.yml``,
``.github/workflows/internal-pins.yml``). The check here reads nothing but the
working tree, so it is safe to gate a PR -- and a guard that can only go red
after the offending change has already landed on main is not blocking anything.

What it covers: the derivation walks two edge kinds. ``SCRIPT_REF`` finds the
action.yml to script edge; ``LOAD_REF`` finds the script to script edge, as a
literal ``_load("<name>")`` call. A helper cross-loaded by any other spelling is
invisible to the closure, so a change to it can ship without a pin bump while
the guard reports pins current -- the same class of gap that once let a
``scripts/apply-comment``-only change ship green.

The asserts below deliberately key off the Python token stream rather than the
regex under test, because comparing a regex against itself asserts nothing. They
also read code only: ``tokenize`` drops comments, and a docstring arrives as one
opaque STRING token, so prose mentioning ``_load(`` cannot make this red.
"""

import io
import tokenize

import pinrefs

# Cross-loading helpers as of 2026-07-29. Named explicitly so the walk cannot go
# vacuously green: if scripts/ is reorganised (helpers moved into a subdirectory,
# pinrefs.ROOT mis-derived), the loop would silently iterate nothing and every
# assert below would pass without checking a single cross-load.
KNOWN_CROSS_LOADERS = {
    "apply-all-detect",
    "apply-comment",
    "apply-detect",
    "deploy-detect",
    "doctor",
    "env-order",
    "summary-comment",
}

# Module-loading machinery a helper script may not reach for. The sanctioned
# shim is SourceFileLoader inside a local `def _load`, which LOAD_REF can see;
# every one of these loads a sibling by a route neither derivation regex reads.
# `importlib.util.spec_from_loader` / `module_from_spec` are absent on purpose --
# those are what the sanctioned shim itself is built from.
FORBIDDEN_LOADERS = (
    "spec_from_file_location",
    "runpy",
    "run_path",
    "import_module",
    "__import__",
    "exec",
)


def _helper_scripts():
    """The extension-less helper scripts directly under ``scripts/``.

    Extension-less is the filter, not "every file": these run as GHA steps and
    carry no suffix, whereas anything else there (``.gitkeep``, a fixture, an
    asset) is not a helper and need not even be UTF-8 text.
    """
    d = pinrefs.ROOT / "scripts"
    return sorted(
        p for p in d.iterdir() if p.is_file() and not p.suffix and not p.name.startswith(".")
    )


def _code_tokens(text):
    """Token stream with comments dropped, so only code is examined.

    Docstrings survive as single STRING tokens, which is what makes prose
    containing ``_load(`` invisible here: it never appears as a NAME followed by
    an OP.
    """
    skip = {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
    }
    return [t for t in tokenize.generate_tokens(io.StringIO(text).readline) if t.type not in skip]


def _is_name(tok, value):
    return tok.type == tokenize.NAME and tok.string == value


def _is_op(tok, value):
    return tok.type == tokenize.OP and tok.string == value


def test_the_helper_script_set_is_not_empty_and_holds_the_known_cross_loaders():
    """Guards the walk itself, so the checks below cannot pass vacuously."""
    names = {p.name for p in _helper_scripts()}
    assert names, (
        f"no extension-less helper scripts found under {pinrefs.ROOT / 'scripts'} -- "
        "the premise checks below would pass without examining anything"
    )
    missing = KNOWN_CROSS_LOADERS - names
    assert not missing, (
        f"cross-loading helper(s) no longer enumerated by the walk: {sorted(missing)} -- "
        "if they moved, the premise checks below stopped covering them"
    )


def test_every_cross_load_in_a_script_is_visible_to_load_ref():
    """Every script-to-script edge is one ``LOAD_REF`` actually sees.

    Three independent ways an edge can hide, each asserted separately so the
    failure names which one happened:

    1. the helper reached under another name (``_L = _load``, a ``partial``, a
       dispatch table) -- caught by requiring every ``_load`` code token to be
       the definition or a direct call;
    2. a call whose argument is not a plain string literal (a variable, an
       f-string) -- caught by comparing call sites against LOAD_REF's matches;
    3. a loader built outside ``_load`` at all -- caught by tying the
       construction count to the number of definitions, and by refusing the
       other import APIs outright.
    """
    for script in _helper_scripts():
        rel = f"scripts/{script.name}"
        text = script.read_text(encoding="utf-8")
        toks = _code_tokens(text)

        mentions = [i for i, t in enumerate(toks) if _is_name(t, "_load")]
        called = [i for i in mentions if i + 1 < len(toks) and _is_op(toks[i + 1], "(")]
        assert len(mentions) == len(called), (
            f"{rel}: {len(mentions)} _load mention(s) in code but only {len(called)} are "
            "direct calls -- the helper is being passed around under another name, and a "
            "cross-load through that name is invisible to the derivation"
        )

        # A string literal containing `_load(` is indistinguishable from a real
        # call to a regex, so LOAD_REF would read it as an edge while the token
        # stream (where it is one opaque STRING) would not. Rather than leave the
        # two silently disagreeing, forbid it and say so.
        in_literal = [t for t in toks if t.type == tokenize.STRING and "_load(" in t.string]
        assert not in_literal, (
            f"{rel}: a string literal contains `_load(` (line {in_literal[0].start[0]}) -- "
            "LOAD_REF cannot tell that from a real call and would derive a phantom "
            "dependency from it"
        )

        defs = [i for i in called if i > 0 and _is_name(toks[i - 1], "def")]
        calls = len(called) - len(defs)
        # Compared against the derivation's own view, comments included or not,
        # so this asserts the two agree rather than re-deriving one from the other.
        matched = len(pinrefs.LOAD_REF.findall(pinrefs.strip_comments(text)))
        assert calls == matched, (
            f"{rel}: {calls} _load( call site(s) in code but LOAD_REF matched {matched} -- "
            "the two disagree, so either a call's argument is not a plain string literal "
            "(invisible to the derivation) or LOAD_REF is matching something that is not a "
            "cross-load (a phantom dependency)"
        )

        built = [
            i
            for i, t in enumerate(toks)
            if _is_name(t, "SourceFileLoader") and i + 1 < len(toks) and _is_op(toks[i + 1], "(")
        ]
        assert len(built) == len(defs), (
            f"{rel}: {len(built)} SourceFileLoader( construction(s) but {len(defs)} "
            "_load definition(s) -- a loader built outside _load can cross-load a helper "
            "without going through the pattern the derivation matches"
        )

        for name in FORBIDDEN_LOADERS:
            hits = [t for t in toks if _is_name(t, name)]
            assert not hits, (
                f"{rel}: uses {name} (line {hits[0].start[0]}) -- loading a sibling this way "
                "is invisible to both derivation regexes; cross-load via the local _load shim"
            )

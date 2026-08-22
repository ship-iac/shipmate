"""Every action manifest's inputs/outputs carry only keys GitHub defines.

`yaml.safe_load` is not a proxy for GitHub's manifest parser, and the gap has a
specific shape. Inside a YAML *flow* mapping a comma is a separator, so a
description written unquoted with a comma in it splits:

    plan_run_id: { description: Echoed plan run id (unset in unlock mode, which
                  consumes no plan run), value: "${{ … }}" }

PyYAML accepts that and yields an extra key whose name is the tail of the
sentence. GitHub refuses the whole file --
`Unexpected value 'which consumes no plan run)'` -- and every job using the
action dies at manifest load, before any step runs. That shipped in v0.16.0 and
broke the apply and deploy paths for anyone who re-pinned; plan runs were
unaffected, which is why no other guard saw it.

This is a **stand-in**, not the property its old filename claimed
(`test_action_manifests_parse_as_github_parses.py`): it still parses with
PyYAML, so it covers one known divergence from GitHub's parser and not the
class. actionlint cannot replace it -- it parses any file argument as a
*workflow*, so every composite `action.yml` fails identically on the missing
`on:`/`jobs:` and the good and shipped-broken manifests are indistinguishable.
The real replacement is a manifest-load smoke run, and it deletes this file.

Checking the key set is what catches it: a comma-split description always leaves
a key that is not one of GitHub's. The keys come from GitHub's metadata syntax
for composite actions -- `deprecationMessage` is valid on an input, `value` on an
output; neither `default` nor `required` is meaningful on an output, but this
guard is about detecting stray parse artifacts, not about policing which of the
legal keys each section uses.
"""

import glob

import yaml

_KNOWN = frozenset({"description", "required", "default", "value", "deprecationMessage"})


def test_every_action_manifest_declares_only_known_input_and_output_keys():
    manifests = sorted(glob.glob("actions/*/action.yml"))
    # Non-vacuity: a changed layout or a wrong cwd must fail here rather than
    # pass over an empty glob.
    assert len(manifests) > 15, f"expected the full action set, found {manifests}"

    stray = []
    for path in manifests:
        doc = yaml.safe_load(open(path, encoding="utf-8")) or {}
        for section in ("inputs", "outputs"):
            for name, spec in (doc.get(section) or {}).items():
                if not isinstance(spec, dict):
                    stray.append(f"{path}: {section}.{name} is not a mapping ({spec!r})")
                    continue
                unknown = sorted(set(spec) - _KNOWN)
                if unknown:
                    stray.append(f"{path}: {section}.{name} has unknown key(s) {unknown}")
    assert not stray, (
        "action manifest key(s) GitHub does not define -- an unquoted description "
        "containing a comma inside a { } flow mapping splits into a bogus key and "
        "GitHub refuses to load the file:\n" + "\n".join(stray)
    )

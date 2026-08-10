"""`app/manifest.json`'s two registration-time fields.

Both are one-time-use and so are never exercised by CI: a missing
`redirect_url` makes GitHub reject the manifest POST outright, and a name
already taken across GitHub makes it reject the registration.
"""

import json

from _loader import ENGINE

MANIFEST = json.loads((ENGINE / "app" / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_declares_the_loopback_redirect_url():
    assert MANIFEST["redirect_url"] == "http://127.0.0.1:8723/callback"


def test_manifest_name_is_a_placeholder():
    # App names are unique across all of GitHub, so a verbatim name is rejected
    # for every org but the first one to register it.
    assert MANIFEST["name"] == "shipmate-<your-org>"

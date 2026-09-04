"""`app/manifest.json`'s registration-time fields.

`redirect_url` and `name` are one-time-use, so CI never exercises them. A missing `redirect_url`
makes GitHub reject the manifest POST outright. A name already taken across GitHub makes it
reject the registration.
"""

import json

from _loader import ENGINE

MANIFEST = json.loads((ENGINE / "app" / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_declares_the_loopback_redirect_url():
    assert MANIFEST["redirect_url"] == "http://127.0.0.1:8723/callback"


def test_manifest_registers_a_private_app():
    # docs/github-app.md §Reference states it, and a public App is installable by any org.
    assert MANIFEST["public"] is False


def test_manifest_name_is_a_placeholder():
    # App names are unique across all of GitHub, so a verbatim name is rejected for every
    # organization but the first one to register it.
    assert MANIFEST["name"] == "shipmate-<your-org>"

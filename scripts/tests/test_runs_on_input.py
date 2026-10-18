"""`deploy.yml`'s `runs_on` input, whole.

A consumer file that passes no `with:` to `deploy` still loads, because the input is optional with
a default. The other workflows' `runs_on` declarations are pinned with their whole input maps.
"""

from _loader import workflow_yaml


def test_deploy_runs_on_default():
    """Mutations: `deploy.yml` `runs_on` to `required: true`; its default to `ubuntu-latest`."""
    assert workflow_yaml("deploy.yml")["on"]["workflow_call"]["inputs"]["runs_on"] == {
        "required": False,
        "default": "ubuntu-26.04-arm",
        "type": "string",
    }

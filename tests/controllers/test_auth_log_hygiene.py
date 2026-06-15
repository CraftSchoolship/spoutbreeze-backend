"""Regression test for log hygiene in the auth flow.

The original issue: `auth_controller.py` `print()`d the full identity-provider
`user_info` payload (email, given/family name — PII) to stdout on every login,
which ended up in pod logs and any aggregator they piped to. The session flow
now logs only safe identifiers; this guards against a `print()` creeping back.
"""

from pathlib import Path

import app.controllers.auth_controller as auth_controller

REPO_SRC = Path(auth_controller.__file__).parent.parent  # app/


def test_no_bare_print_in_app_code():
    """A `print(` anywhere under app/ is the symptom that previously
    leaked the full user_info payload to stdout. Stay vigilant."""
    offenders = []
    for path in REPO_SRC.rglob("*.py"):
        for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
            stripped = raw.lstrip()
            if stripped.startswith("print(") and "# noqa" not in raw:
                offenders.append(f"{path.relative_to(REPO_SRC.parent)}:{lineno}: {raw.strip()}")
    assert not offenders, "Unexpected print() calls:\n" + "\n".join(offenders)

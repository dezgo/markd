"""Every cron entry point is actually installed by setup.sh.

purge_stale_signups.py existed, worked, and was documented in deploy/README.md
as a "run crontab -e and paste this" step. Nobody pasted it, so it never ran
once, and 1267 accounts from the June-July 2026 signup flood were still in the
database two months later.

The convention this relies on: a script meant to run from cron opens its
docstring with "Cron script:".
"""

import ast
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent
SETUP = WEB / "setup.sh"


def _cron_scripts():
    found = []
    for path in sorted(WEB.glob("*.py")):
        doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
        if doc.startswith("Cron script:"):
            found.append(path.name)
    return found


def test_the_convention_still_finds_things():
    """If this ever returns nothing, the test below is passing vacuously."""
    assert len(_cron_scripts()) >= 3


@pytest.mark.parametrize("script", _cron_scripts())
def test_setup_installs_it(script):
    setup = SETUP.read_text(encoding="utf-8")
    assert script in setup, (
        f"{script} declares itself a cron script but setup.sh never installs it")


@pytest.mark.parametrize("script", _cron_scripts())
def test_the_crontab_line_is_rewritten_not_appended(script):
    """setup.sh is re-run on every deploy. Without the matching grep -vF, each
    run would add another copy of the same job."""
    setup = SETUP.read_text(encoding="utf-8")
    crontab_line = next(l for l in setup.splitlines() if "| crontab -" in l)
    assert f'grep -vF "{script}"' in crontab_line, (
        f"{script} is not stripped before being re-added — re-running setup.sh "
        f"would install it twice")


def test_the_purge_log_directory_is_created():
    setup = SETUP.read_text(encoding="utf-8")
    assert "purge.log" in setup
    assert "LOG_DIR" in setup

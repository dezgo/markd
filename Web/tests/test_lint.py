"""Static check for names that do not exist.

Earned its place: splitting app.py into modules moved make_token into mail.py
and left `import secrets` behind, so every emailed link — signup, password
reset, resend — raised NameError in production. Nothing else in the suite
touches make_token, so nothing caught it.

Undefined names only. This is not a style gate.
"""

import subprocess
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent


def test_no_undefined_names():
    files = [str(p) for p in sorted(WEB.glob("*.py"))]
    files += [str(p) for p in sorted((WEB / "routes").glob("*.py"))]

    result = subprocess.run([sys.executable, "-m", "pyflakes", *files],
                            capture_output=True, text=True)
    bad = [line for line in result.stdout.splitlines() if "undefined name" in line]
    assert not bad, "undefined names:\n" + "\n".join(bad)

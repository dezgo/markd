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


def test_only_config_reads_the_environment_at_import_time():
    """config.py is what loads .env, so a module-level os.environ read anywhere
    else races it.

    app.py imports antispam before config, so antispam's Turnstile keys were
    read before .env had been loaded. Under systemd that is invisible, because
    EnvironmentFile has already populated the process environment — everywhere
    else the keys came back empty and the CAPTCHA silently stayed off.
    """
    import ast

    offenders = []
    files = sorted(WEB.glob("*.py")) + sorted((WEB / "routes").glob("*.py"))
    for path in files:
        if path.name == "config.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:          # module level, including defs called at import
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Attribute) and sub.attr == "environ"
                        and isinstance(sub.value, ast.Name) and sub.value.id == "os"):
                    offenders.append(f"{path.name}:{sub.lineno}")

    assert not offenders, (
        "read these through config.py instead: " + ", ".join(offenders))


def test_antispam_keys_come_from_config():
    """The mirror must not drift from the source."""
    import antispam
    import config
    assert antispam.TURNSTILE_SITE_KEY == config.TURNSTILE_SITE_KEY
    assert antispam.TURNSTILE_SECRET_KEY == config.TURNSTILE_SECRET_KEY

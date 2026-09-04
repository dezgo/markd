"""Take a copy of the sqlite database before something irreversible.

deploy.sh already does this around a restart. Maintenance scripts that delete
rows need the same protection, and a purge that removes a thousand accounts is
a worse thing to get wrong than a deploy, because nothing about it is obvious
afterwards.
"""

import os
import shutil
import sqlite3
from datetime import datetime

KEEP = 10


def backup(database_url: str, label: str) -> str:
    """Copy the database into backups/ and return the path, or "" if it is not
    a local sqlite file — an external database is the operator's to protect."""
    if not database_url.startswith("sqlite:///"):
        return ""
    path = database_url[len("sqlite:///"):]
    if not path or not os.path.exists(path):
        return ""

    folder = os.path.join(os.path.dirname(path) or ".", "backups")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(
        folder, f"markd-{label}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db")

    # .backup is safe against a live writer — gunicorn is still serving.
    # copy2 is the fallback for a sqlite3 too old to have it.
    try:
        src, dst = sqlite3.connect(path), sqlite3.connect(dest)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
    except (sqlite3.Error, AttributeError):
        shutil.copy2(path, dest)

    _prune(folder, label)
    return dest


def _prune(folder: str, label: str) -> None:
    """Keep the most recent KEEP backups of this label; a purge run should not
    fill the disk that the app needs to keep working."""
    ours = sorted(
        (f for f in os.listdir(folder) if f.startswith(f"markd-{label}-")),
        reverse=True)
    for stale in ours[KEEP:]:
        try:
            os.remove(os.path.join(folder, stale))
        except OSError:
            pass

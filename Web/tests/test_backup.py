"""The copy taken before anything irreversible.

A purge that removes a thousand accounts is worse to get wrong than a deploy,
because nothing about the result is obvious afterwards.
"""

import os
import sqlite3

import pytest

from dbbackup import KEEP, backup


@pytest.fixture
def db_file(tmp_path):
    path = tmp_path / "markd.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    con.executemany("INSERT INTO t (v) VALUES (?)", [(f"row{i}",) for i in range(50)])
    con.commit()
    con.close()
    return path


def _url(path):
    return "sqlite:///" + str(path).replace("\\", "/")


def test_it_copies_the_data_not_just_the_file(db_file):
    dest = backup(_url(db_file), "prepurge")
    con = sqlite3.connect(dest)
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 50
    con.close()


def test_the_backup_is_independent_of_the_original(db_file):
    """The point of the exercise: deleting after the copy must not touch it."""
    dest = backup(_url(db_file), "prepurge")
    con = sqlite3.connect(db_file)
    con.execute("DELETE FROM t")
    con.commit()
    con.close()

    con = sqlite3.connect(dest)
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 50
    con.close()


def test_it_lands_in_a_backups_folder_beside_the_database(db_file):
    dest = backup(_url(db_file), "prepurge")
    assert os.path.basename(os.path.dirname(dest)) == "backups"
    # samefile, not string equality: the sqlite URL uses forward slashes even
    # on Windows, so the returned path is separator-mixed.
    assert os.path.samefile(os.path.dirname(os.path.dirname(dest)), db_file.parent)


def test_the_label_is_in_the_name(db_file):
    assert "prepurge" in os.path.basename(backup(_url(db_file), "prepurge"))
    assert "predormant" in os.path.basename(backup(_url(db_file), "predormant"))


def test_a_non_sqlite_url_is_declined_rather_than_guessed_at(db_file):
    assert backup("postgresql://localhost/markd", "prepurge") == ""


def test_a_missing_file_is_declined(tmp_path):
    assert backup(_url(tmp_path / "nope.db"), "prepurge") == ""


def test_old_backups_are_pruned(db_file, monkeypatch):
    """A nightly purge must not fill the disk the app needs to keep running."""
    import dbbackup
    stamps = iter([f"2026-09-{d:02d}-000000" for d in range(1, 20)])

    class _Now:
        @staticmethod
        def now():
            class _S:
                @staticmethod
                def strftime(_fmt):
                    return next(stamps)
            return _S()

    monkeypatch.setattr(dbbackup, "datetime", _Now)
    for _ in range(KEEP + 5):
        backup(_url(db_file), "prepurge")

    folder = db_file.parent / "backups"
    kept = [f for f in os.listdir(folder) if f.startswith("markd-prepurge-")]
    assert len(kept) == KEEP


def test_pruning_only_touches_its_own_label(db_file, monkeypatch):
    """deploy.sh's backups and a purge's must not evict each other."""
    import dbbackup
    counter = iter(range(100))

    class _Now:
        @staticmethod
        def now():
            class _S:
                @staticmethod
                def strftime(_fmt):
                    return f"2026-09-04-{next(counter):06d}"
            return _S()

    monkeypatch.setattr(dbbackup, "datetime", _Now)
    keeper = backup(_url(db_file), "predormant")
    for _ in range(KEEP + 3):
        backup(_url(db_file), "prepurge")
    assert os.path.exists(keeper), "a different label's backup was pruned"

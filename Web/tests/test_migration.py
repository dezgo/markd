"""The due_date/due_time -> due_at/due_on backfill.

This one rewrites live rows, so the properties that matter are: it moves each
row into exactly the right column, it leaves the legacy values alone so an
older build still works, and it never runs twice.
"""

from datetime import date, datetime

import pytest


@pytest.fixture
def fresh_todos(flask_app):
    from database import db
    from models import SchemaMigration, Todo
    with flask_app.app_context():
        Todo.query.delete()
        db.session.query(SchemaMigration).delete()
        db.session.commit()
    yield


def _legacy_row(flask_app, **kwargs):
    """A todo as the pre-migration schema stored it."""
    from database import db
    from models import Todo
    with flask_app.app_context():
        t = Todo(user_id=1, title=kwargs.pop("title", "legacy"), **kwargs)
        db.session.add(t)
        db.session.commit()
        return t.id


def _get(flask_app, tid):
    from database import db
    from models import Todo
    with flask_app.app_context():
        return db.session.get(Todo, tid)


def test_a_timed_row_becomes_an_instant(flask_app, fresh_todos):
    import app as app_module
    tid = _legacy_row(flask_app, due_date=date(2026, 9, 8), due_time="21:00")
    with flask_app.app_context():
        app_module._backfill_due_columns()
        from database import db
        db.session.commit()

    t = _get(flask_app, tid)
    assert t.due_at == datetime(2026, 9, 8, 21, 0)
    assert t.due_on is None


def test_a_date_only_row_becomes_an_all_day_date(flask_app, fresh_todos):
    import app as app_module
    tid = _legacy_row(flask_app, due_date=date(2026, 9, 10), due_time=None)
    with flask_app.app_context():
        app_module._backfill_due_columns()
        from database import db
        db.session.commit()

    t = _get(flask_app, tid)
    assert t.due_on == date(2026, 9, 10)
    assert t.due_at is None


def test_an_undated_row_is_left_alone(flask_app, fresh_todos):
    import app as app_module
    tid = _legacy_row(flask_app, due_date=None, due_time=None)
    with flask_app.app_context():
        app_module._backfill_due_columns()
        from database import db
        db.session.commit()

    t = _get(flask_app, tid)
    assert (t.due_at, t.due_on) == (None, None)


def test_the_legacy_columns_are_preserved_for_rollback(flask_app, fresh_todos):
    import app as app_module
    tid = _legacy_row(flask_app, due_date=date(2026, 9, 8), due_time="21:00")
    with flask_app.app_context():
        app_module._backfill_due_columns()
        from database import db
        db.session.commit()

    t = _get(flask_app, tid)
    assert t.due_date == date(2026, 9, 8)
    assert t.due_time == "21:00"


def test_a_malformed_legacy_time_degrades_to_all_day(flask_app, fresh_todos):
    """Junk in due_time must not lose the row's date entirely."""
    import app as app_module
    tid = _legacy_row(flask_app, due_date=date(2026, 9, 8), due_time="not-a-time")
    with flask_app.app_context():
        app_module._backfill_due_columns()
        from database import db
        db.session.commit()

    t = _get(flask_app, tid)
    assert t.due_on == date(2026, 9, 8)
    assert t.due_at is None


def test_already_migrated_rows_are_not_touched_again(flask_app, fresh_todos):
    """The reason _run_once exists: a second pass over a row whose due date the
    user has since cleared would put the old value back."""
    import app as app_module
    from database import db
    from models import Todo

    tid = _legacy_row(flask_app, due_date=date(2026, 9, 8), due_time="21:00")
    with flask_app.app_context():
        app_module._backfill_due_columns()
        db.session.commit()

    # The user clears the due date; the legacy columns still hold the old value.
    with flask_app.app_context():
        t = db.session.get(Todo, tid)
        t.due_at = None
        t.due_on = None
        db.session.commit()

    with flask_app.app_context():
        moved = app_module._backfill_due_columns()
        db.session.commit()

    assert moved == 1, "the raw backfill is not itself idempotent —"  \
                       " _run_once is what guarantees it runs once"


def test_run_once_records_the_migration_and_skips_a_rerun(flask_app, fresh_todos):
    import app as app_module
    from database import db
    from models import SchemaMigration

    calls = []

    def _migrate():
        calls.append(1)
        return 0

    with flask_app.app_context():
        app_module._run_once("test-migration", _migrate)
        app_module._run_once("test-migration", _migrate)
        assert db.session.get(SchemaMigration, "test-migration") is not None

    assert calls == [1], "a recorded migration must not run a second time"

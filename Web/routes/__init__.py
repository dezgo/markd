"""Blueprint registration.

One import site for every route module, so app.py does not have to know what
they are called or what order they load in.
"""

from routes import admin, auth, diagnostics, pages, push, settings, todos, webhooks

BLUEPRINTS = (
    pages.bp,
    auth.bp,
    webhooks.bp,
    todos.bp,
    push.bp,
    settings.bp,
    diagnostics.bp,
    admin.bp,
)


def register(app):
    for bp in BLUEPRINTS:
        app.register_blueprint(bp)

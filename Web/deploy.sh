#!/usr/bin/env bash
# Deploy the currently checked-out tree: install deps, restart, verify.
#
# Assumes the caller has already put the repo on the commit to deploy (CI does
# fetch + checkout main + ff-only merge). Run setup.sh interactively for
# first-install / one-time infra changes.
set -euo pipefail

DIR=/var/www/markd          # repo root
APP_DIR=$DIR/Web            # web app subdir
APP=markd
SOCK=/run/markd/markd.sock

cd "$DIR"

# Guard against deploying a drifted checkout. This is the failure that hid for
# 2.5 months: prod was left on restructure/monorepo, so `git pull` was a no-op
# and every deploy still reported success.
BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$BRANCH" != "main" ]; then
  echo "ERROR: refusing to deploy from branch '$BRANCH' (expected main)" >&2
  exit 1
fi

echo "==> Deploying $(git rev-parse --short HEAD) $(git log -1 --format=%s)"

echo "==> Installing dependencies"
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

# The app runs schema migrations at startup, so take a snapshot first. This is
# the only copy standing between a bad migration and lost todos.
echo "==> Backing up the database"
DB="$APP_DIR/markd.db"
if [ -f "$DB" ]; then
  mkdir -p "$APP_DIR/backups"
  BACKUP="$APP_DIR/backups/markd-$(date +%Y%m%d-%H%M%S).db"
  # .backup is safe against a live writer; cp is the fallback if sqlite3 is absent.
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$DB" ".backup '$BACKUP'"
  else
    cp "$DB" "$BACKUP"
  fi
  echo "    $BACKUP"
  # Keep the ten most recent.
  ls -1t "$APP_DIR"/backups/markd-*.db 2>/dev/null | tail -n +11 | xargs -r rm -f
else
  echo "    no sqlite file at $DB — skipping (external database?)"
fi

echo "==> Restarting service"
sudo systemctl restart "$APP"

# The app must come back up AND serve the version in the tree we just deployed.
# Without this, a restart that silently kept serving stale code looks like success.
#
# Poll rather than using curl --retry: the unit has RuntimeDirectory=markd, so
# systemd deletes /run/markd on stop and the socket briefly does not exist.
# That is ENOENT, not ECONNREFUSED, so --retry-connrefused fails instantly.
echo "==> Verifying"
EXPECTED=$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' "$APP_DIR/app.py")

RAW=""
for _ in $(seq 1 30); do
  if RAW=$(curl -fsS --unix-socket "$SOCK" http://localhost/version 2>/dev/null); then
    break
  fi
  sleep 1
done

if [ -z "$RAW" ]; then
  echo "ERROR: app did not answer on $SOCK within 30s" >&2
  systemctl status "$APP" --no-pager >&2 || true
  journalctl -u "$APP" -n 40 --no-pager >&2 || true
  exit 1
fi

LIVE=$(printf '%s' "$RAW" | sed -n 's/.*"version":"\([^"]*\)".*/\1/p')

if [ "$LIVE" != "$EXPECTED" ]; then
  echo "ERROR: expected version '$EXPECTED', app is serving '$LIVE'" >&2
  journalctl -u "$APP" -n 40 --no-pager >&2 || true
  exit 1
fi

echo "==> Done — serving $LIVE"

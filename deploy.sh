#!/usr/bin/env bash
# Slim CI deploy: pull, install deps, restart service.
# Run setup.sh interactively for first-install / one-time infra changes.
set -euo pipefail

DIR=/var/www/markd
APP=markd

echo "==> Pulling latest code"
git -C "$DIR" pull --ff-only

echo "==> Installing dependencies"
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"

echo "==> Restarting service"
sudo systemctl restart "$APP"

echo "Done."

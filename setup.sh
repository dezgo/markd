#!/usr/bin/env bash
set -euo pipefail

APP=markd
DIR=/var/www/markd
REPO=git@github.com:dezgo/markd.git
LOG_DIR=/var/log/markd
DOMAIN=markd.appfoundry.cc

# ── Code ─────────────────────────────────────────────────────────────────────
if [ -d "$DIR/.git" ]; then
    echo "==> Pulling latest code"
    git -C "$DIR" pull
else
    echo "==> Cloning repo"
    sudo mkdir -p "$DIR"
    sudo chown derek:derek "$DIR"
    git clone "$REPO" "$DIR"
fi

# ── Python venv ───────────────────────────────────────────────────────────────
if [ ! -d "$DIR/.venv" ]; then
    echo "==> Creating venv"
    python3 -m venv "$DIR/.venv"
fi

echo "==> Installing dependencies"
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"

# ── .env ──────────────────────────────────────────────────────────────────────
if [ ! -f "$DIR/.env" ]; then
    echo "==> Copying .env.example — fill in real values before starting the service"
    cp "$DIR/.env.example" "$DIR/.env"
fi

# ── Log dir ───────────────────────────────────────────────────────────────────
echo "==> Log directory"
sudo mkdir -p "$LOG_DIR"
sudo chown derek:www-data "$LOG_DIR"

# ── Systemd service ───────────────────────────────────────────────────────────
echo "==> Installing systemd service"
sudo cp "$DIR/deploy/$APP.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "$APP"
sudo systemctl restart "$APP"

# ── Nginx ─────────────────────────────────────────────────────────────────────
# Only copy the config on first install — Certbot modifies it in place for SSL
# and we don't want to overwrite those changes on every deploy.
if [ ! -f /etc/nginx/sites-available/"$APP" ]; then
    echo "==> Installing Nginx config (first install)"
    sudo cp "$DIR/deploy/nginx-$APP.conf" /etc/nginx/sites-available/"$APP"
else
    echo "==> Nginx config already exists — skipping (Certbot owns it)"
fi

if [ ! -L /etc/nginx/sites-enabled/"$APP" ]; then
    sudo ln -s /etc/nginx/sites-available/"$APP" /etc/nginx/sites-enabled/"$APP"
fi

sudo nginx -t
sudo systemctl reload nginx

# ── Sudoers ───────────────────────────────────────────────────────────────────
echo "==> Installing sudoers rules"
sudo cp "$DIR/deploy/sudoers-derek-ops" /etc/sudoers.d/derek-ops
sudo chmod 440 /etc/sudoers.d/derek-ops

echo ""
echo "Done."

if grep -q "change-me" "$DIR/.env" 2>/dev/null; then
    echo ""
    echo ".env still has placeholder values. Edit it and restart the service:"
    echo "  nano $DIR/.env"
    echo "  sudo systemctl restart $APP"
fi

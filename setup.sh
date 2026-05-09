#!/usr/bin/env bash
set -euo pipefail

APP=markd
DIR=/var/www/markd
REPO=git@github-personal:dezgo/markd.git
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
echo "==> Installing Nginx config"
sudo cp "$DIR/deploy/nginx-$APP.conf" /etc/nginx/sites-available/"$APP"
if [ ! -L /etc/nginx/sites-enabled/"$APP" ]; then
    sudo ln -s /etc/nginx/sites-available/"$APP" /etc/nginx/sites-enabled/"$APP"
fi

CERT=/etc/letsencrypt/live/$DOMAIN/fullchain.pem
if [ -f "$CERT" ]; then
    sudo nginx -t
    sudo systemctl reload nginx
else
    echo "==> Skipping nginx reload — no SSL cert yet (see below)"
fi

# ── Sudoers ───────────────────────────────────────────────────────────────────
echo "==> Installing sudoers rules"
sudo cp "$DIR/deploy/sudoers-derek-ops" /etc/sudoers.d/derek-ops
sudo chmod 440 /etc/sudoers.d/derek-ops

echo ""
echo "Done."

if [ ! -f "$CERT" ]; then
    echo ""
    echo "SSL certificate not found. Run to finish:"
    echo "  sudo certbot --nginx -d $DOMAIN"
    echo "  sudo systemctl reload nginx"
fi

if grep -q "change-me" "$DIR/.env" 2>/dev/null; then
    echo ""
    echo ".env still has placeholder values. Edit it and restart the service:"
    echo "  nano $DIR/.env"
    echo "  sudo systemctl restart $APP"
fi

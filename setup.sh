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

# ── VAPID keys ────────────────────────────────────────────────────────────────
if ! grep -q "^VAPID_PRIVATE_KEY=.\+" "$DIR/.env" 2>/dev/null; then
    echo "==> Generating VAPID keys"
    VAPID_KEYS=$("$DIR/.venv/bin/python3" - <<'PYEOF'
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
import base64
key = ec.generate_private_key(ec.SECP256R1())
priv_bytes = key.private_numbers().private_value.to_bytes(32, 'big')
priv = base64.urlsafe_b64encode(priv_bytes).decode().rstrip('=')
pub = base64.urlsafe_b64encode(key.public_key().public_bytes(
    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
)).decode().rstrip('=')
print(priv, pub)
PYEOF
)
    VAPID_PRIV=$(echo "$VAPID_KEYS" | cut -d' ' -f1)
    VAPID_PUB=$(echo "$VAPID_KEYS"  | cut -d' ' -f2)
    sed -i "s|^VAPID_PRIVATE_KEY=.*|VAPID_PRIVATE_KEY=$VAPID_PRIV|" "$DIR/.env"
    sed -i "s|^VAPID_PUBLIC_KEY=.*|VAPID_PUBLIC_KEY=$VAPID_PUB|"   "$DIR/.env"
    echo "    VAPID keys written to .env"
else
    echo "==> VAPID keys already present — skipping"
fi

# ── Notification cron job ─────────────────────────────────────────────────────
CRON_CMD="* * * * * $DIR/.venv/bin/python3 $DIR/send_notifications.py >> $LOG_DIR/notifications.log 2>&1"
if ! crontab -l 2>/dev/null | grep -qF "send_notifications.py"; then
    echo "==> Installing notification cron job"
    (crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -
else
    echo "==> Cron job already installed"
fi

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

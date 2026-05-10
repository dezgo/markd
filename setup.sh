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
    cat > /tmp/_gen_vapid.py << 'PYEOF'
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
import base64

# Older cryptography versions require the backend argument
try:
    from cryptography.hazmat.backends import default_backend
    key = ec.generate_private_key(ec.SECP256R1(), default_backend())
except TypeError:
    key = ec.generate_private_key(ec.SECP256R1())

# Private key: raw 32-byte big-endian scalar
priv_bytes = key.private_numbers().private_value.to_bytes(32, 'big')
priv = base64.urlsafe_b64encode(priv_bytes).decode().rstrip('=')

# Public key: uncompressed EC point (65 bytes)
# X962/UncompressedPoint may not exist in older cryptography; fall back to DER
try:
    pub_bytes = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint
    )
except (ValueError, AttributeError):
    der = key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo
    )
    pub_bytes = der[-65:]

pub = base64.urlsafe_b64encode(pub_bytes).decode().rstrip('=')
print(priv, pub)
PYEOF
    VAPID_KEYS=$("$DIR/.venv/bin/python3" /tmp/_gen_vapid.py)
    rm -f /tmp/_gen_vapid.py
    VAPID_PRIV=$(echo "$VAPID_KEYS" | cut -d' ' -f1)
    VAPID_PUB=$(echo "$VAPID_KEYS"  | cut -d' ' -f2)
    if grep -q "^VAPID_PRIVATE_KEY=" "$DIR/.env"; then
        sed -i "s|^VAPID_PRIVATE_KEY=.*|VAPID_PRIVATE_KEY=$VAPID_PRIV|" "$DIR/.env"
        sed -i "s|^VAPID_PUBLIC_KEY=.*|VAPID_PUBLIC_KEY=$VAPID_PUB|"   "$DIR/.env"
    else
        echo "VAPID_PRIVATE_KEY=$VAPID_PRIV" >> "$DIR/.env"
        echo "VAPID_PUBLIC_KEY=$VAPID_PUB"   >> "$DIR/.env"
        echo "VAPID_CONTACT=mailto:derekgg@gmail.com" >> "$DIR/.env"
    fi
    echo "    VAPID keys written to .env"
else
    echo "==> VAPID keys already present — skipping"
fi

# ── Notification cron job ─────────────────────────────────────────────────────
# Source .env directly so env vars are set before Python starts —
# dotenv.load_dotenv() has been observed to silently fail under cron.
CRON_CMD="* * * * * /bin/bash -c 'set -a; . $DIR/.env; set +a; $DIR/.venv/bin/python3 $DIR/send_notifications.py' >> $LOG_DIR/notifications.log 2>&1"
# Always rewrite to pick up cron command changes between deploys
(crontab -l 2>/dev/null | grep -vF "send_notifications.py"; echo "$CRON_CMD") | crontab -
echo "==> Cron job installed"

# ── Sudoers ───────────────────────────────────────────────────────────────────
echo "==> Installing sudoers rules"
sudo cp "$DIR/deploy/sudoers-derek-ops" /etc/sudoers.d/derek-ops
sudo chmod 440 /etc/sudoers.d/derek-ops

# ── Restart service (after .env is fully populated) ───────────────────────────
echo "==> Restarting service"
sudo systemctl restart "$APP"

echo ""
echo "Done."

if grep -q "change-me" "$DIR/.env" 2>/dev/null; then
    echo ""
    echo ".env still has placeholder values. Edit it and restart the service:"
    echo "  nano $DIR/.env"
    echo "  sudo systemctl restart $APP"
fi

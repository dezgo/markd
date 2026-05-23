#!/usr/bin/env bash
set -euo pipefail

APP=markd
DIR=/var/www/markd          # repo root (git operations target this)
APP_DIR=$DIR/Web            # web app subdir (venv, deps, .env, deploy/ live here)
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
if [ ! -d "$APP_DIR/.venv" ]; then
    echo "==> Creating venv"
    python3 -m venv "$APP_DIR/.venv"
fi

echo "==> Installing dependencies"
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

# ── .env ──────────────────────────────────────────────────────────────────────
if [ ! -f "$APP_DIR/.env" ]; then
    echo "==> Copying .env.example — fill in real values before starting the service"
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
fi

# Append any newly-introduced env vars that aren't in the existing .env
ensure_env() {
    local key="$1"
    local default="$2"
    if ! grep -q "^${key}=" "$APP_DIR/.env"; then
        echo "${key}=${default}" >> "$APP_DIR/.env"
        echo "    added ${key}= to .env (set a real value)"
    fi
}
ensure_env APP_URL "https://$DOMAIN"
ensure_env INITIAL_ADMIN_EMAIL "you@example.com"
ensure_env RESEND_API_KEY ""
ensure_env EMAIL_FROM "Markd <markd@appfoundry.cc>"

# ── Log dir ───────────────────────────────────────────────────────────────────
echo "==> Log directory"
sudo mkdir -p "$LOG_DIR"
sudo chown derek:www-data "$LOG_DIR"

# ── Systemd service ───────────────────────────────────────────────────────────
echo "==> Installing systemd service"
sudo cp "$APP_DIR/deploy/$APP.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "$APP"

# ── Nginx ─────────────────────────────────────────────────────────────────────
# Only copy the config on first install — Certbot modifies it in place for SSL
# and we don't want to overwrite those changes on every deploy.
if [ ! -f /etc/nginx/sites-available/"$APP" ]; then
    echo "==> Installing Nginx config (first install)"
    sudo cp "$APP_DIR/deploy/nginx-$APP.conf" /etc/nginx/sites-available/"$APP"
else
    echo "==> Nginx config already exists — skipping (Certbot owns it)"
fi

if [ ! -L /etc/nginx/sites-enabled/"$APP" ]; then
    sudo ln -s /etc/nginx/sites-available/"$APP" /etc/nginx/sites-enabled/"$APP"
fi

sudo nginx -t
sudo systemctl reload nginx

# ── VAPID keys ────────────────────────────────────────────────────────────────
if ! grep -q "^VAPID_PRIVATE_KEY=.\+" "$APP_DIR/.env" 2>/dev/null; then
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
    VAPID_KEYS=$("$APP_DIR/.venv/bin/python3" /tmp/_gen_vapid.py)
    rm -f /tmp/_gen_vapid.py
    VAPID_PRIV=$(echo "$VAPID_KEYS" | cut -d' ' -f1)
    VAPID_PUB=$(echo "$VAPID_KEYS"  | cut -d' ' -f2)
    if grep -q "^VAPID_PRIVATE_KEY=" "$APP_DIR/.env"; then
        sed -i "s|^VAPID_PRIVATE_KEY=.*|VAPID_PRIVATE_KEY=$VAPID_PRIV|" "$APP_DIR/.env"
        sed -i "s|^VAPID_PUBLIC_KEY=.*|VAPID_PUBLIC_KEY=$VAPID_PUB|"   "$APP_DIR/.env"
    else
        echo "VAPID_PRIVATE_KEY=$VAPID_PRIV" >> "$APP_DIR/.env"
        echo "VAPID_PUBLIC_KEY=$VAPID_PUB"   >> "$APP_DIR/.env"
        echo "VAPID_CONTACT=mailto:derekgg@gmail.com" >> "$APP_DIR/.env"
    fi
    echo "    VAPID keys written to .env"
else
    echo "==> VAPID keys already present — skipping"
fi

# ── Notification cron jobs ────────────────────────────────────────────────────
# Source .env directly so env vars are set before Python starts —
# dotenv.load_dotenv() has been observed to silently fail under cron.
CRON_DUE="* * * * * /bin/bash -c 'set -a; . $APP_DIR/.env; set +a; $APP_DIR/.venv/bin/python3 $APP_DIR/send_notifications.py' >> $LOG_DIR/notifications.log 2>&1"
CRON_OVERDUE="* * * * * /bin/bash -c 'set -a; . $APP_DIR/.env; set +a; $APP_DIR/.venv/bin/python3 $APP_DIR/send_overdue_check.py' >> $LOG_DIR/overdue.log 2>&1"
# Always rewrite to pick up cron command changes between deploys
(crontab -l 2>/dev/null | grep -vF "send_notifications.py" | grep -vF "send_overdue_check.py"; echo "$CRON_DUE"; echo "$CRON_OVERDUE") | crontab -
echo "==> Cron jobs installed"

# ── Sudoers ───────────────────────────────────────────────────────────────────
echo "==> Installing sudoers rules"
sudo cp "$APP_DIR/deploy/sudoers-derek-ops" /etc/sudoers.d/derek-ops
sudo chmod 440 /etc/sudoers.d/derek-ops

# ── Restart service (after .env is fully populated) ───────────────────────────
echo "==> Restarting service"
sudo systemctl restart "$APP"

echo ""
echo "Done."

if grep -q "change-me" "$APP_DIR/.env" 2>/dev/null; then
    echo ""
    echo ".env still has placeholder values. Edit it and restart the service:"
    echo "  nano $APP_DIR/.env"
    echo "  sudo systemctl restart $APP"
fi

# Deploy

Server config files for reference. Copy to the correct locations on the server.

| File | Server path |
|---|---|
| `markd.service` | `/etc/systemd/system/markd.service` |
| `nginx-markd.conf` | `/etc/nginx/sites-available/markd` |
| `sudoers-derek-ops` | `/etc/sudoers.d/derek-ops` |

## First install

```bash
# On do-personal — run setup.sh from anywhere, it clones the repo itself
bash <(ssh do-personal cat /dev/stdin) << 'EOF'
curl -sL https://raw.githubusercontent.com/dezgo/markd/main/setup.sh | bash
EOF

# Or: clone manually then run
ssh do-personal
git clone git@github.com:dezgo/markd.git /var/www/markd
bash /var/www/markd/setup.sh
```

`setup.sh` handles everything. On a fresh server it will:
1. Clone/pull the repo
2. Create the Python venv and install dependencies
3. Copy `.env.example` → `.env` (edit this before the service will start)
4. Create `/var/log/markd/`
5. Install and enable the systemd service
6. Install the Nginx config
7. Install sudoers rules
8. Print a reminder to run Certbot if no SSL cert exists yet

## Updates

```bash
ssh do-personal
bash /var/www/markd/setup.sh
```

That's it — pulls latest, reinstalls deps, restarts the service.

## SSL (first install only)

After `setup.sh` completes on a fresh server:

```bash
ssh do-personal
sudo certbot --nginx -d markd.appfoundry.cc
```

## API usage

```bash
# Create a todo
curl -X POST https://markd.appfoundry.cc/todos \
  -H "X-API-Key: <your-api-key>" \
  -H "Content-Type: application/json" \
  -d '{"title": "Buy milk", "due_date": "2026-05-12"}'

# List todos
curl https://markd.appfoundry.cc/todos -H "X-API-Key: <your-api-key>"

# Complete a todo
curl -X PATCH https://markd.appfoundry.cc/todos/1 \
  -H "X-API-Key: <your-api-key>" \
  -H "Content-Type: application/json" \
  -d '{"done": true}'

# Delete a todo
curl -X DELETE https://markd.appfoundry.cc/todos/1 -H "X-API-Key: <your-api-key>"
```

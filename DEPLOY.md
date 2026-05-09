# Deploy Markd to do-personal

## 1. Server setup (once)

```bash
ssh do-personal
sudo mkdir -p /var/www/markd
sudo chown $USER:$USER /var/www/markd
cd /var/www/markd

git clone git@github-personal:dezgo/markd.git .
python3 -m venv venv
venv/bin/pip install -r requirements.txt

cp .env.example .env
# Edit .env — set SECRET_KEY, UI_PASSWORD, API_KEY
nano .env

# Systemd service
sudo cp markd.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now markd

# Nginx
sudo cp nginx.conf /etc/nginx/sites-available/markd.appfoundry.cc
sudo ln -s /etc/nginx/sites-available/markd.appfoundry.cc \
           /etc/nginx/sites-enabled/markd.appfoundry.cc
sudo nginx -t && sudo systemctl reload nginx

# TLS (certbot must be installed)
sudo certbot --nginx -d markd.appfoundry.cc
```

## 2. Generate PWA icons

You need `static/icons/icon-192.png` and `static/icons/icon-512.png`.
Use any tool (e.g. https://realfavicongenerator.net) to generate them from a
source image, then commit them to the repo.

## 3. Deploy updates

```bash
ssh do-personal
cd /var/www/markd
git pull
venv/bin/pip install -r requirements.txt   # if requirements changed
sudo systemctl restart markd
```

## API usage (from voice-note dispatcher)

```bash
# Create a todo
curl -X POST https://markd.appfoundry.cc/todos \
  -H "X-API-Key: <your-api-key>" \
  -H "Content-Type: application/json" \
  -d '{"title": "Buy milk", "due_date": "2026-05-12"}'

# List todos
curl https://markd.appfoundry.cc/todos \
  -H "X-API-Key: <your-api-key>"

# Complete a todo
curl -X PATCH https://markd.appfoundry.cc/todos/1 \
  -H "X-API-Key: <your-api-key>" \
  -H "Content-Type: application/json" \
  -d '{"done": true}'

# Delete a todo
curl -X DELETE https://markd.appfoundry.cc/todos/1 \
  -H "X-API-Key: <your-api-key>"
```

# Markd — Web

Flask web app (the original Markd). Serves the PWA + the `/todos` HTTP API.

## Local dev

```bash
cd Web
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in real values
.venv/bin/flask --app app run
```

## Production deploy

GitHub Actions push-to-`main` runs `Web/.venv/bin/pip install -r Web/requirements.txt` and restarts the `markd` systemd service on do-personal. First install / infra changes: run `bash /var/www/markd/Web/setup.sh` on the server.

See `deploy/README.md` for full server setup notes.

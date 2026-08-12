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
curl -sL https://raw.githubusercontent.com/dezgo/markd/main/Web/setup.sh | bash
EOF

# Or: clone manually then run
ssh do-personal
git clone git@github.com:dezgo/markd.git /var/www/markd
bash /var/www/markd/Web/setup.sh
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
bash /var/www/markd/Web/setup.sh
```

That's it — pulls latest, reinstalls deps, restarts the service.

## Note on the Web/ restructure (May 2026)

The repo used to be Python files at the root; now everything web-app related
lives under `Web/`. If you're running `setup.sh` against a server that was
provisioned **before** that restructure, the script will detect and migrate
the stale `/var/www/markd/.env`, `/var/www/markd/markd.db`, and
`/var/www/markd/.venv` into `Web/` automatically. It also patches the live
Nginx static `alias` in place (Certbot owns that file, so we can't just
overwrite it). No manual steps required.

## SSL (first install only)

After `setup.sh` completes on a fresh server:

```bash
ssh do-personal
sudo certbot --nginx -d markd.appfoundry.cc
```

## Email deliverability & the signup gate

Bot signups were posting junk addresses to `/signup`, each of which got a
verification email. The hard bounces wrecked the sending reputation and real
mail started landing in spam. The code side is done; these are the parts that
live outside the repo.

**Do them in this order — 2, 3, 5, then 1.** Stopping the bots and suppressing
bounces is what actually ends the damage; the domain move only helps once
nothing is generating fresh bounces, and it is the one step that briefly takes
sending offline. Steps 4 and 6 can go in whenever.

### 1. Move sending to a mail subdomain

The root domain's reputation is already damaged, so app mail moves to its own
subdomain and starts clean. Markd is the only thing sending through this Resend
account, so the root domain can be given up entirely.

> **The free plan allows one verified domain, and it is currently
> `appfoundry.cc`.** The two cannot both be verified, so `appfoundry.cc` has to
> be deleted before `mail.appfoundry.cc` can be added — and Resend only reveals
> the DKIM key *after* a domain is added, so the records can't be staged in
> advance. **Markd cannot send during the gap.** Usually 10–60 minutes, mostly
> DNS propagation.
>
> Do this step **after** the signup gate is deployed and **last** of the three,
> so no bot traffic is arriving while sending is down. Sends during the gap fail
> soft: the app logs `Email send failed`, and a real user who signs up in the
> window sees the normal "check your email" page, then gets their link from the
> "Send it again" button once sending is back. Nothing is lost, and nothing
> needs re-running.

To shorten the gap, drop the TTL on the `appfoundry.cc` zone to 300s a few hours
beforehand, and go at a quiet time of day.

1. Resend → Domains → delete `appfoundry.cc`.
2. Add Domain → `mail.appfoundry.cc`.
3. Create the records it gives you at the DNS host:

| Type | Name | Value |
|---|---|---|
| MX | `send.mail` | `feedback-smtp.<region>.amazonses.com` (priority 10) |
| TXT | `send.mail` | `v=spf1 include:amazonses.com ~all` |
| TXT | `resend._domainkey.mail` | (long DKIM key from Resend) |
| TXT | `_dmarc.mail` | `v=DMARC1; p=none; rua=mailto:derek@watsonblinds.com.au` |

Copy the exact values from Resend — the MX host is region-specific. Wait for all
rows to show **Verified**, then set in `.env` and restart:

```
EMAIL_FROM=Markd <markd@mail.appfoundry.cc>
```

Confirm with a real signup, and check `/diagnostics` → "Sending from".

Start DMARC at `p=none` so nothing gets rejected while you watch the reports.
Once a couple of weeks of clean sending have gone by, tighten to
`p=quarantine`, then `p=reject`.

**Then lock down the root domain.** Once nothing sends as `@appfoundry.cc`, say
so explicitly — it stops spoofers trading on the damaged reputation, and the
"no mail here" signal helps the subdomain stand on its own:

```
appfoundry.cc          TXT   v=spf1 -all
_dmarc.appfoundry.cc   TXT   v=DMARC1; p=reject; sp=none; rua=mailto:derek@watsonblinds.com.au
```

`sp=none` matters: without it the root's `p=reject` is inherited by
`mail.appfoundry.cc` and would apply there before it has a reputation to stand
on. Only add the `-all` SPF record if you are certain nothing sends from
`@appfoundry.cc` by any route — a mailbox provider, a contact form, a CI
notifier. Receiving is unaffected either way; leave the root MX records alone.

### 2. Turn on Turnstile

Free, and usually invisible to real users. https://dash.cloudflare.com →
Turnstile → Add site (`markd.appfoundry.cc`, Managed widget). Put both keys in
`.env`:

```
TURNSTILE_SITE_KEY=0x4AAA...
TURNSTILE_SECRET_KEY=0x4AAA...
```

Until these are set the CAPTCHA is skipped and the other layers carry the load —
the app logs a warning at boot and `/diagnostics` shows it as OFF.

### 3. Wire up the bounce webhook

Resend → Webhooks → Add endpoint `https://markd.appfoundry.cc/webhooks/resend`,
subscribed to `email.bounced` and `email.complained`. Copy the signing secret:

```
RESEND_WEBHOOK_SECRET=whsec_...
```

Bounced and complained addresses are then never mailed again, and a hard bounce
on an unverified account deletes it. Unsigned or replayed calls get a 401.

### 4. Daily cleanup cron

```bash
sudo crontab -e -u derek
0 4 * * * /var/www/markd/Web/.venv/bin/python /var/www/markd/Web/purge_stale_signups.py >> /var/log/markd/purge.log 2>&1
```

Deletes accounts left unverified for `PURGE_UNVERIFIED_DAYS` (default 7), plus
expired tokens and old rate-limit rows. Check what it would do first:

```bash
/var/www/markd/Web/.venv/bin/python /var/www/markd/Web/purge_stale_signups.py --dry-run
```

### 5. Clear out the existing junk

The bot accounts already in the DB are dead addresses waiting to bounce. See
what's there before deleting anything:

```bash
ssh do-personal
sqlite3 /var/www/markd/Web/markd.db \
  "SELECT date(created_at) d, count(*) FROM users WHERE email_verified=0 GROUP BY d ORDER BY d DESC LIMIT 30;"
```

If that shows the spike, the cron above clears it on its next run — or force it
immediately with `PURGE_UNVERIFIED_DAYS=1`. Also add a Suppression list entry in
Resend for anything that already hard-bounced, so a retry can't re-send.

### 6. Nginx rate limiting

`nginx-markd.conf` now declares a `limit_req_zone` and throttles the auth
routes. Certbot owns the live file, so merge rather than overwrite:

```bash
sudo nano /etc/nginx/sites-available/markd    # copy the limit_req_zone + auth location block
sudo nginx -t && sudo systemctl reload nginx
```

### Checking it worked

`/diagnostics` (logged in) has a **Signup gate & mail reputation** section:
Turnstile and webhook status, unverified account count, suppressed addresses,
and mail sent per hour against the cap. Blocked signups log one line each:

```bash
sudo journalctl -u markd -f | grep "signup blocked"
```

The stage in brackets tells you which layer caught it — `trap`, `attempt-rate`,
`turnstile`, `address`, or `send-rate`.

Reputation itself is visible in Resend's dashboard (bounce and complaint rates)
and, if you add the domain, Google Postmaster Tools. Bounce rate wants to be
under 2%, complaints under 0.1%.

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

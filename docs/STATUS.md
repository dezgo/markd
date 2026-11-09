# Markd

**URL:** https://markd.appfoundry.cc
**Repo:** github.com/dezgo/markd
**Stack:** Flask 3, SQLAlchemy, SQLite, vanilla JS PWA (no build step), Gunicorn, Resend, Web Push
**Hosting:** do-personal (DigitalOcean), Nginx → unix socket; push to `main` auto-deploys

## What It Does

A to-do / reminders app. Tasks with due dates and times, notes, and repeats (every N days/weeks,
chosen weekdays, last or second-last weekday of the month, last day of the month). Push
notifications when a task falls due, plus an overdue nag. They're always bundled, never more than
one push at a time. Multi-user accounts (email + password). There's also a REST API (`X-API-Key`)
for other tools to add tasks.

Used by Derek and a handful of family members.

## Current State

Live and stable at **v75** (2026-11-09).

Recent:
- v75: web addresses in a task's title or notes are tappable links (open in a new tab)
- v74: a task due in the same minute as the overdue nag folds into the nag
- v71–v73: "last day of month" repeat; monthly repeats now actually come back after being ticked
- Signup defences (Turnstile, honeypot, rate limits in the app), bot-account cleanup, nightly purge
  of unverified signups, emailed links that don't act on a plain GET (mail scanners)

Deploy runbook: `Web/deploy/README.md`.

## To Do

- [ ] **Nginx rate limit on login/signup isn't applied on the server.** The repo's
  `Web/deploy/nginx-markd.conf` has it, but the live `/etc/nginx/sites-enabled/markd` has no
  `limit_req` (Certbot owns that file, so it needs a hand merge + `nginx -t` + reload). The app's own
  signup limits still work; login has no brute-force limit besides this. ⚠️ do-personal also runs
  Tina, so do the reload at a quiet time.
- [ ] Done tasks can't be duplicated. Done rows aren't tappable, so there's no way to redo a finished
  task from it. Needs a visible way in, not a hidden tap target.
- [ ] `deploy.sh`'s "only deploy from main" guard has never been tested end to end on prod.
- [ ] Delete the two merged remote branches `restructure/monorepo` and `backend/scaffold`.

Deliberately left as-is:
- Signup says "an account with that email already exists". That reveals which addresses have
  accounts, but hiding it makes the form worse to use, and it's rate-limited.

## History

Session journals for 2026-08-07 and 2026-08-12 are in `docs/logs/` (retired; this file replaced them).

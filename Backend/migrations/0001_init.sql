-- Initial Markd schema. Mirrors the Flask SQLite schema in ../Web/models.py
-- column-for-column so the phase-6 data migration is a straight INSERT.
--
-- Datetime columns store ISO 8601 strings (Flask/SQLAlchemy default).
-- Boolean columns store 0/1 (SQLite has no native boolean).
-- Primary keys are INTEGER AUTOINCREMENT (matches Flask, not sudoku's TEXT ids).

CREATE TABLE users (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  email           TEXT    NOT NULL UNIQUE,
  password_hash   TEXT    NOT NULL,
  email_verified  INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_users_email ON users(email);

CREATE TABLE email_tokens (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER NOT NULL REFERENCES users(id),
  token       TEXT    NOT NULL UNIQUE,
  purpose     TEXT    NOT NULL,           -- 'verify' or 'reset'
  expires_at  TEXT    NOT NULL,
  used_at     TEXT,
  created_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_email_tokens_user_id ON email_tokens(user_id);
CREATE INDEX idx_email_tokens_token   ON email_tokens(token);

CREATE TABLE todos (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id               INTEGER REFERENCES users(id),  -- nullable for legacy rows
  title                 TEXT    NOT NULL,
  done                  INTEGER NOT NULL DEFAULT 0,
  due_date              TEXT,                          -- YYYY-MM-DD (naive local date)
  due_time              TEXT,                          -- HH:MM in UTC
  notes                 TEXT,
  spawned_from_id       INTEGER,                       -- parent recurring todo id
  recurrence_interval   INTEGER,
  recurrence_unit       TEXT,                          -- days|weeks|months|years|monthly-last|monthly-2last
  recurrence_days       TEXT,                          -- CSV of JS weekday numbers (0=Sun..6=Sat)
  notified_at           TEXT,                          -- set when due-now push sent
  created_at            TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at            TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_todos_user_id ON todos(user_id);

CREATE TABLE push_subscriptions (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER REFERENCES users(id),            -- nullable for legacy rows
  endpoint    TEXT    NOT NULL UNIQUE,                 -- push service URL
  p256dh      TEXT    NOT NULL,                        -- base64-url ECDH public key
  auth        TEXT    NOT NULL,                        -- base64-url auth secret
  created_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_push_subscriptions_user_id ON push_subscriptions(user_id);

CREATE TABLE user_settings (
  user_id                  INTEGER PRIMARY KEY REFERENCES users(id),
  overdue_check_enabled    INTEGER NOT NULL DEFAULT 1,
  overdue_check_time       TEXT    NOT NULL DEFAULT '07:00',
  timezone                 TEXT    NOT NULL DEFAULT 'UTC',
  last_overdue_check_date  TEXT,
  theme                    TEXT    NOT NULL DEFAULT 'indigo',
  created_at               TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at               TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

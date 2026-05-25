-- OTP/magic-link auth. Markd is dropping email+password (which Flask uses) in
-- favor of the same pattern sudoku uses: email → 6-digit code → bearer token.
--
-- The users.password_hash column stays in place for now so the phase-6 data
-- migration from Flask sqlite is a straight INSERT. A later migration drops
-- password_hash + email_verified after cutover.

CREATE TABLE auth_codes (
  email       TEXT    PRIMARY KEY,
  code_hash   TEXT    NOT NULL,        -- sha256(code) as lowercase hex
  expires_at  INTEGER NOT NULL,        -- unix-ms
  attempts    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE auth_tokens (
  token_hash    TEXT    PRIMARY KEY,   -- sha256(bearer token) as lowercase hex
  user_id       INTEGER NOT NULL REFERENCES users(id),
  created_at    INTEGER NOT NULL,      -- unix-ms
  last_used_at  INTEGER NOT NULL       -- unix-ms
);
CREATE INDEX idx_auth_tokens_user ON auth_tokens(user_id);

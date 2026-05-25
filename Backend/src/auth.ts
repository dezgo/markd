import type { Env } from "./types";
import { jsonError, jsonOk, readJson } from "./http";
import { generateBearerToken, generateOtpCode, sha256 } from "./crypto";
import { sendOtpEmail } from "./email";

const OTP_TTL_MS = 15 * 60 * 1000;
const MAX_ATTEMPTS = 5;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export async function authStart(req: Request, env: Env): Promise<Response> {
  const body = await readJson<{ email?: string }>(req);
  const email = (body?.email ?? "").trim().toLowerCase();
  if (!EMAIL_RE.test(email)) return jsonError(400, "invalid_email");

  const code = generateOtpCode();
  const codeHash = await sha256(code);
  const expiresAt = Date.now() + OTP_TTL_MS;

  // INSERT OR REPLACE: a fresh request always overwrites any pending code
  // (and resets attempts). Matches what users expect from "resend code".
  await env.DB.prepare(
    "INSERT OR REPLACE INTO auth_codes (email, code_hash, expires_at, attempts) VALUES (?, ?, ?, 0)",
  )
    .bind(email, codeHash, expiresAt)
    .run();

  try {
    await sendOtpEmail(env, email, code);
  } catch (err) {
    console.error("sendOtpEmail failed:", err);
    return jsonError(500, "email_send_failed");
  }

  return new Response(null, { status: 204 });
}

interface VerifyResponse {
  token: string;
  user: { id: number; email: string };
  is_new: boolean;
}

export async function authVerify(req: Request, env: Env): Promise<Response> {
  const body = await readJson<{ email?: string; code?: string }>(req);
  const email = (body?.email ?? "").trim().toLowerCase();
  const code = (body?.code ?? "").trim();
  if (!EMAIL_RE.test(email)) return jsonError(400, "invalid_email");
  if (!/^\d{6}$/.test(code)) return jsonError(400, "invalid_code");

  const row = await env.DB.prepare(
    "SELECT code_hash, expires_at, attempts FROM auth_codes WHERE email = ?",
  )
    .bind(email)
    .first<{ code_hash: string; expires_at: number; attempts: number }>();

  if (!row) return jsonError(401, "invalid_code");
  if (row.expires_at < Date.now()) {
    await env.DB.prepare("DELETE FROM auth_codes WHERE email = ?").bind(email).run();
    return jsonError(401, "code_expired");
  }
  if (row.attempts >= MAX_ATTEMPTS) {
    await env.DB.prepare("DELETE FROM auth_codes WHERE email = ?").bind(email).run();
    return jsonError(429, "too_many_attempts");
  }

  const providedHash = await sha256(code);
  if (providedHash !== row.code_hash) {
    await env.DB.prepare(
      "UPDATE auth_codes SET attempts = attempts + 1 WHERE email = ?",
    )
      .bind(email)
      .run();
    return jsonError(401, "invalid_code");
  }

  // Code valid — consume it.
  await env.DB.prepare("DELETE FROM auth_codes WHERE email = ?").bind(email).run();

  // Get or create user. password_hash is NOT NULL in the schema (held over from
  // the Flask era); insert '' as a sentinel for OTP-only accounts. Will be
  // dropped in a post-cutover migration.
  let user = await env.DB.prepare(
    "SELECT id, email FROM users WHERE email = ?",
  )
    .bind(email)
    .first<{ id: number; email: string }>();

  const isNew = !user;
  if (!user) {
    const insert = await env.DB.prepare(
      "INSERT INTO users (email, password_hash, email_verified) VALUES (?, '', 1) RETURNING id, email",
    )
      .bind(email)
      .first<{ id: number; email: string }>();
    if (!insert) return jsonError(500, "user_create_failed");
    user = insert;
  }

  const token = generateBearerToken();
  const tokenHash = await sha256(token);
  const now = Date.now();
  await env.DB.prepare(
    "INSERT INTO auth_tokens (token_hash, user_id, created_at, last_used_at) VALUES (?, ?, ?, ?)",
  )
    .bind(tokenHash, user.id, now, now)
    .run();

  const response: VerifyResponse = { token, user, is_new: isNew };
  return jsonOk(response);
}

export interface AuthedRequest {
  user_id: number;
}

export async function requireAuth(req: Request, env: Env): Promise<AuthedRequest | Response> {
  const header = req.headers.get("authorization") ?? "";
  const match = header.match(/^Bearer\s+(.+)$/i);
  if (!match) return jsonError(401, "unauthenticated");

  const token = match[1]!.trim();
  const tokenHash = await sha256(token);
  const row = await env.DB.prepare(
    "SELECT user_id FROM auth_tokens WHERE token_hash = ?",
  )
    .bind(tokenHash)
    .first<{ user_id: number }>();
  if (!row) return jsonError(401, "unauthenticated");

  // Best-effort update of last_used_at — fire and forget.
  env.DB.prepare("UPDATE auth_tokens SET last_used_at = ? WHERE token_hash = ?")
    .bind(Date.now(), tokenHash)
    .run()
    .catch(() => {});

  return { user_id: row.user_id };
}

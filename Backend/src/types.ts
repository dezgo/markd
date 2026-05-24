export interface Env {
  DB: D1Database;
  EMAIL_FROM: string;
  APP_URL: string;
  RESEND_API_KEY?: string;
  VAPID_PRIVATE_KEY?: string;
  VAPID_PUBLIC_KEY?: string;
  VAPID_CONTACT?: string;
}

import type { Env } from "./types";

export async function sendOtpEmail(env: Env, to: string, code: string): Promise<void> {
  if (!env.RESEND_API_KEY) {
    throw new Error("RESEND_API_KEY not configured");
  }
  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.RESEND_API_KEY}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      from: env.EMAIL_FROM,
      to: [to],
      subject: `Your Markd sign-in code: ${code}`,
      text:
        `Your sign-in code is: ${code}\n\n` +
        `It expires in 15 minutes.\n\n` +
        `If you didn't request this, you can ignore this email.`,
    }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Resend ${res.status}: ${body}`);
  }
}

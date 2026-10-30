import { env } from "./env.server";

export interface LoginResult {
  accessToken: string;
  expiresAt: number;
  sessionId: string;
}

export class LoginRejected extends Error {}
export class MarketplaceUnavailable extends Error {}

const url = (path: string) => `${env.marketplaceApiUrl}/api/v1${path}`;

/**
 * Credential validation: the marketplace API checks the password against Cognito and records the session itself
 * (see its `/auth/login` description), so the host only keeps the access token and the session id.
 */
export async function login(email: string, password: string): Promise<LoginResult> {
  let res: Response;
  try {
    res = await fetch(url("/auth/login"), { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email, password }) });
  } catch {
    throw new MarketplaceUnavailable("login request failed");
  }
  if (res.status === 400 || res.status === 401 || res.status === 403 || res.status === 404) throw new LoginRejected(String(res.status));
  if (!res.ok) throw new MarketplaceUnavailable(`login failed: ${res.status}`);
  const body = (await res.json().catch(() => null)) as { sessionId?: string; tokens?: { accessToken?: string; expiresIn?: number } } | null;
  const t = body?.tokens;
  // No tokens means Cognito wants another step (e.g. a new password); that is finished in the marketplace, not here.
  if (!body?.sessionId || !t?.accessToken || typeof t.expiresIn !== "number") throw new LoginRejected("incomplete");
  return { accessToken: t.accessToken, expiresAt: Math.floor(Date.now() / 1000) + t.expiresIn, sessionId: body.sessionId };
}

/** Best effort: a failed delete must not stop the local sign-out. */
export async function deleteSession(sessionId: string, accessToken: string): Promise<void> {
  try {
    await fetch(url(`/sessions/${encodeURIComponent(sessionId)}`), { method: "DELETE", headers: { authorization: `Bearer ${accessToken}` } });
  } catch {
    /* ignore */
  }
}

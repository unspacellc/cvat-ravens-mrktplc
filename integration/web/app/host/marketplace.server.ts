import { env } from "./env.server";

export interface MarketplaceUser {
  id: string;
  email: string;
  first_name?: string | null;
  last_name?: string | null;
}

export interface SessionTokens {
  accessToken: string;
  refreshToken: string;
  expiresAt: number;
}

const url = (path: string) => `${env.marketplaceApiUrl}/api/v1${path}`;

/** Credential validation: the marketplace API accepts the user's Cognito access token or answers 401. */
export async function fetchProfile(accessToken: string): Promise<MarketplaceUser | null> {
  const res = await fetch(url("/auth/me"), { headers: { authorization: `Bearer ${accessToken}` } });
  if (res.status === 401 || res.status === 403) return null;
  if (!res.ok) throw new Error(`marketplace /auth/me failed: ${res.status}`);
  return (await res.json()) as MarketplaceUser;
}

/** Credential reception: records the Cognito tokens as a marketplace session. */
export async function createSession(sessionId: string, t: SessionTokens, userId: string): Promise<void> {
  const res = await fetch(url("/sessions"), {
    method: "POST",
    headers: { authorization: `Bearer ${t.accessToken}`, "content-type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId,
      access_token: t.accessToken,
      refresh_token: t.refreshToken,
      expires_at: new Date(t.expiresAt * 1000).toISOString(),
      user_id: userId,
    }),
  });
  if (!res.ok) throw new Error(`marketplace /sessions failed: ${res.status}`);
}

/** Best effort: a failed delete must not stop the local sign-out. */
export async function deleteSession(sessionId: string, accessToken: string): Promise<void> {
  try {
    await fetch(url(`/sessions/${encodeURIComponent(sessionId)}`), { method: "DELETE", headers: { authorization: `Bearer ${accessToken}` } });
  } catch {
    /* ignore */
  }
}

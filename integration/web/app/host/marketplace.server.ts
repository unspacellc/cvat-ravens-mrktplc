import { env } from "./env.server";
import type { StoredTokens } from "./token-store.server";

export interface LoginResult {
  tokens: StoredTokens;
  sessionId: string;
  userId?: string;
}

export class LoginRejected extends Error {}
export class MarketplaceUnavailable extends Error {}

const url = (path: string) => `${env.marketplaceApiUrl}/api/v1${path}`;

/** Credential validation: the marketplace API checks the password against Cognito and returns the tokens. */
export async function login(email: string, password: string): Promise<LoginResult> {
  let res: Response;
  try {
    res = await fetch(url("/auth/login"), { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email, password }) });
  } catch {
    throw new MarketplaceUnavailable("login request failed");
  }
  if (res.status === 400 || res.status === 401 || res.status === 403 || res.status === 404) throw new LoginRejected(String(res.status));
  if (!res.ok) throw new MarketplaceUnavailable(`login failed: ${res.status}`);
  const body = (await res.json().catch(() => null)) as { sessionId?: string; user?: { id?: string }; tokens?: { accessToken?: string; refreshToken?: string | null; expiresIn?: number } } | null;
  const t = body?.tokens;
  // No tokens means Cognito wants another step (e.g. a new password); that is finished in the marketplace, not here.
  if (!body?.sessionId || !t?.accessToken || !t.refreshToken || typeof t.expiresIn !== "number") throw new LoginRejected("incomplete");
  return {
    sessionId: body.sessionId,
    userId: body.user?.id,
    tokens: { accessToken: t.accessToken, refreshToken: t.refreshToken, expiresAt: Math.floor(Date.now() / 1000) + t.expiresIn },
  };
}

/** Exchanges the refresh token for a new access token. Cognito returns a new refresh token only when rotation is on. */
export async function refresh(current: StoredTokens): Promise<StoredTokens | null> {
  const username = usernameFromAccessToken(current.accessToken);
  if (!username) return null;
  let res: Response;
  try {
    res = await fetch(url("/auth/refresh"), { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ refreshToken: current.refreshToken, username }) });
  } catch {
    throw new MarketplaceUnavailable("refresh request failed");
  }
  if (res.status >= 500) throw new MarketplaceUnavailable(`refresh failed: ${res.status}`);
  if (!res.ok) return null;
  const t = ((await res.json().catch(() => null)) as { tokens?: { accessToken?: string; refreshToken?: string | null; expiresIn?: number } } | null)?.tokens;
  if (!t?.accessToken || typeof t.expiresIn !== "number") return null;
  return { accessToken: t.accessToken, refreshToken: t.refreshToken || current.refreshToken, expiresAt: Math.floor(Date.now() / 1000) + t.expiresIn };
}

/** Cognito access tokens carry the pool username. Unverified decode: used only to address the refresh call. */
export function usernameFromAccessToken(token: string): string | null {
  try {
    const u = JSON.parse(Buffer.from(token.split(".")[1], "base64url").toString()).username;
    return typeof u === "string" ? u : null;
  } catch {
    return null;
  }
}

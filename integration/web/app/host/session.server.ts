import { createCookieSessionStorage, redirect } from "react-router";

import { env } from "./env.server";
import { MarketplaceUnavailable, refresh } from "./marketplace.server";
import { tokenStore } from "./token-store.server";

/** The cookie holds only the session id and chosen tenant. Tokens live in the server-side token store. */
interface Data {
  sessionId: string;
  tenantId: string;
}

const storage = createCookieSessionStorage<Partial<Data>>({
  cookie: { name: "__labeling_host", httpOnly: true, sameSite: "lax", path: "/", secrets: [env.sessionSecret], secure: env.secureCookies, maxAge: 60 * 60 * 24 * 30 },
});

export const getSession = (request: Request) => storage.getSession(request.headers.get("cookie"));
export const commitSession = storage.commitSession;
export const destroySession = storage.destroySession;

/** Refresh this long before expiry so a token is never handed to the API moments before it lapses. */
const REFRESH_MARGIN_S = 120;
const now = () => Date.now() / 1000;

/** Parallel requests for one session share a single refresh. */
const refreshes = new Map<string, Promise<string | null>>();

async function freshAccessToken(sessionId: string): Promise<string | null> {
  const t = await tokenStore.get(sessionId);
  if (!t) return null;
  if (t.expiresAt - REFRESH_MARGIN_S > now()) return t.accessToken;
  const pending = refreshes.get(sessionId);
  if (pending) return pending;
  const p = (async () => {
    try {
      const next = await refresh(t);
      if (!next) {
        await tokenStore.delete(sessionId); // refresh token revoked or expired: the sign-in is over
        return null;
      }
      await tokenStore.put(sessionId, next);
      return next.accessToken;
    } catch (e) {
      if (e instanceof MarketplaceUnavailable && t.expiresAt > now()) return t.accessToken; // still valid; try again next request
      return null;
    } finally {
      refreshes.delete(sessionId);
    }
  })();
  refreshes.set(sessionId, p);
  return p;
}

/** The signed-in user's current access token (refreshed when near expiry), or null when there is no valid sign-in. */
export async function resolveAuth(request: Request) {
  const session = await getSession(request);
  const sessionId = session.get("sessionId");
  const token = sessionId ? await freshAccessToken(sessionId) : null;
  return token ? { token, session, tenantId: session.get("tenantId") } : null;
}

/** Returns the bearer token, or redirects to login when absent/expired. */
export async function requireToken(request: Request) {
  const auth = await resolveAuth(request);
  if (!auth) throw redirect("/login", { headers: { "set-cookie": await destroySession(await getSession(request)) } });
  return auth;
}

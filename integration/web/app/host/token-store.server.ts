import { env } from "./env.server";

/** Cognito tokens for one sign-in. `expiresAt` is epoch seconds. The browser never holds these; its cookie carries only the session id. */
export interface StoredTokens {
  accessToken: string;
  refreshToken: string;
  expiresAt: number;
}

export interface TokenStore {
  put(sessionId: string, tokens: StoredTokens, userId?: string): Promise<void>;
  /** Returns the tokens even when the access token has expired (the refresh token may still be good); null when unknown. */
  get(sessionId: string): Promise<StoredTokens | null>;
  delete(sessionId: string): Promise<void>;
}

/** The marketplace API's own session store (`/sessions`), authenticated with the internal service key, same as the marketplace web app. */
export class MarketplaceTokenStore implements TokenStore {
  constructor(private readonly baseUrl: string, private readonly apiKey: string) {}

  private call(path: string, init: RequestInit = {}) {
    return fetch(`${this.baseUrl}/api/v1/sessions${path}`, { ...init, headers: { authorization: `Bearer ${this.apiKey}`, "content-type": "application/json" } });
  }

  async put(sessionId: string, t: StoredTokens, userId?: string) {
    const res = await this.call("", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, access_token: t.accessToken, refresh_token: t.refreshToken, expires_at: new Date(t.expiresAt * 1000).toISOString(), user_id: userId }),
    });
    if (!res.ok) throw new Error(`session store put failed: ${res.status}`);
  }

  async get(sessionId: string) {
    const res = await this.call(`/${encodeURIComponent(sessionId)}`);
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`session store get failed: ${res.status}`);
    const s = (await res.json()) as { access_token?: string; refresh_token?: string; expires_at?: string };
    if (!s.access_token || !s.refresh_token || !s.expires_at) return null;
    return { accessToken: s.access_token, refreshToken: s.refresh_token, expiresAt: Math.floor(new Date(s.expires_at).getTime() / 1000) };
  }

  async delete(sessionId: string) {
    try {
      await this.call(`/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
    } catch {
      /* a failed delete must not stop sign-out */
    }
  }
}

/** Dev only: lost on restart and not shared between instances. */
export class MemoryTokenStore implements TokenStore {
  private readonly items = new Map<string, StoredTokens>();
  async put(id: string, t: StoredTokens) {
    this.items.set(id, t);
  }
  async get(id: string) {
    return this.items.get(id) ?? null;
  }
  async delete(id: string) {
    this.items.delete(id);
  }
}

/** Production without the service key fails closed instead of silently keeping tokens in one process. */
class UnconfiguredTokenStore implements TokenStore {
  private fail(): never {
    throw new Error("MARKETPLACE_API_KEY is required for the session token store");
  }
  put = async () => this.fail();
  get = async () => this.fail();
  delete = async () => {};
}

export function createTokenStore(): TokenStore {
  if (env.marketplaceApiKey) return new MarketplaceTokenStore(env.marketplaceApiUrl, env.marketplaceApiKey);
  return env.secureCookies ? new UnconfiguredTokenStore() : new MemoryTokenStore();
}

export const tokenStore: TokenStore = createTokenStore();

/** Dev-only standalone host configuration. In the marketplace this is replaced by its own env + session handling. */
export const env = {
  apiUrl: process.env.LABELING_API_URL ?? "http://localhost:8000",
  /** Marketplace API that signs users in (`/auth/login`) and holds their sessions (`/sessions`). */
  marketplaceApiUrl: process.env.MARKETPLACE_API_URL ?? "https://api.unspace.cassyork.com",
  /** Internal service key the marketplace API requires (as a Bearer credential) on its `/sessions` store. Server-side secret. */
  marketplaceApiKey: process.env.MARKETPLACE_API_KEY ?? "",
  /** Where "Back to portal" goes (the marketplace web app). Hidden when unset. */
  portalUrl: process.env.MARKETPLACE_PORTAL_URL ?? "",
  sessionSecret: process.env.SESSION_SECRET ?? "dev-only-session-secret",
  secureCookies: process.env.NODE_ENV === "production",
};

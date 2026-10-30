/** Dev-only standalone host configuration. In the marketplace this is replaced by its own env + session handling. */
export const env = {
  apiUrl: process.env.LABELING_API_URL ?? "http://localhost:8000",
  /** Marketplace API that signs users in (`/auth/login`) and holds their sessions (`/sessions`). */
  marketplaceApiUrl: process.env.MARKETPLACE_API_URL ?? "https://api.unspace.cassyork.com",
  sessionSecret: process.env.SESSION_SECRET ?? "dev-only-session-secret",
  secureCookies: process.env.NODE_ENV === "production",
};

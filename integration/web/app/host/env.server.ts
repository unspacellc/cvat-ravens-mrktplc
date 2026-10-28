/** Dev-only standalone host configuration. In the marketplace this is replaced by its own env + session handling. */
export const env = {
  apiUrl: process.env.LABELING_API_URL ?? "http://localhost:8000",
  issuer: process.env.OIDC_ISSUER ?? "http://localhost:8081/default",
  clientId: process.env.OIDC_CLIENT_ID ?? "portal-web",
  clientSecret: process.env.OIDC_CLIENT_SECRET ?? "x",
  sessionSecret: process.env.SESSION_SECRET ?? "dev-only-session-secret",
  publicUrl: process.env.PUBLIC_URL ?? "http://localhost:3000",
  secureCookies: process.env.NODE_ENV === "production",
};

/** Dev-only standalone host configuration. In the marketplace this is replaced by its own env + session handling. */
export const env = {
  apiUrl: process.env.LABELING_API_URL ?? "http://localhost:8000",
  /** Marketplace API that validates Cognito tokens (`/auth/me`) and records sessions (`/sessions`). */
  marketplaceApiUrl: process.env.MARKETPLACE_API_URL ?? "https://api.unspace.cassyork.com",
  cognito: {
    region: process.env.COGNITO_REGION ?? "",
    userPoolId: process.env.COGNITO_USER_POOL_ID ?? "",
    userPoolClientId: process.env.COGNITO_USER_POOL_CLIENT_ID ?? "",
  },
  sessionSecret: process.env.SESSION_SECRET ?? "dev-only-session-secret",
  secureCookies: process.env.NODE_ENV === "production",
};

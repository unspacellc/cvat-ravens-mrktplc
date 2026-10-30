import { createCookieSessionStorage, redirect } from "react-router";

import { env } from "./env.server";

interface Data {
  accessToken: string;
  expiresAt: number;
  tenantId: string;
  marketplaceSessionId: string;
}

const storage = createCookieSessionStorage<Partial<Data>>({
  cookie: { name: "__labeling_host", httpOnly: true, sameSite: "lax", path: "/", secrets: [env.sessionSecret], secure: env.secureCookies, maxAge: 3600 },
});

export const getSession = (request: Request) => storage.getSession(request.headers.get("cookie"));
export const commitSession = storage.commitSession;
export const destroySession = storage.destroySession;

/** Returns the bearer token, or redirects to login when absent/expired. */
export async function requireToken(request: Request) {
  const session = await getSession(request);
  const token = session.get("accessToken");
  if (!token || (session.get("expiresAt") ?? 0) < Date.now() / 1000) {
    throw redirect("/login", { headers: { "set-cookie": await destroySession(session) } });
  }
  return { token, session, tenantId: session.get("tenantId") };
}

import { randomUUID } from "node:crypto";

import { jwtExp } from "~/host/cognito-tokens";
import { createSession, fetchProfile } from "~/host/marketplace.server";
import { commitSession, getSession } from "~/host/session.server";

const fail = (status: number, error: string, code: string) =>
  Response.json({ error, statusCode: status, code, details: {}, requestId: "host" }, { status });

/**
 * Receives the Cognito tokens Amplify obtained in the browser. The marketplace API validates the access token,
 * the tokens are recorded as a marketplace session, and only then does the host cookie session start.
 */
export async function action({ request }: { request: Request }) {
  if (request.method !== "POST" || request.headers.get("x-requested-with") !== "labeling") return fail(403, "missing csrf header", "csrf");
  const body = (await request.json().catch(() => null)) as { accessToken?: string; refreshToken?: string } | null;
  const accessToken = body?.accessToken;
  const exp = accessToken ? jwtExp(accessToken) : null;
  if (!accessToken || !body?.refreshToken || exp === null || exp < Date.now() / 1000) return fail(400, "tokens missing or expired", "bad_tokens");

  let profile;
  try {
    profile = await fetchProfile(accessToken);
  } catch {
    return fail(502, "could not validate sign-in", "marketplace_unavailable");
  }
  if (!profile) return fail(401, "sign-in rejected", "invalid_token");

  const sessionId = randomUUID();
  try {
    await createSession(sessionId, { accessToken, refreshToken: body.refreshToken, expiresAt: exp }, profile.id);
  } catch {
    return fail(502, "could not record session", "marketplace_unavailable");
  }

  const session = await getSession(request);
  session.set("accessToken", accessToken);
  session.set("expiresAt", exp);
  session.set("marketplaceSessionId", sessionId);
  session.unset("tenantId");
  return new Response(null, { status: 204, headers: { "set-cookie": await commitSession(session) } });
}

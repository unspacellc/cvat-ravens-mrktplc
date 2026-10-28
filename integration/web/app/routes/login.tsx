import { createHash, randomBytes } from "node:crypto";

import { redirect } from "react-router";

import { env } from "~/host/env.server";
import { commitSession, getSession } from "~/host/session.server";

/** Authorization-code + PKCE against the configured OIDC issuer (Cognito in production, the mock IdP in dev). */
export async function loader({ request }: { request: Request }) {
  const session = await getSession(request);
  const state = randomBytes(16).toString("hex");
  const verifier = randomBytes(32).toString("base64url");
  session.set("oidcState", state);
  session.set("codeVerifier", verifier);
  const q = new URLSearchParams({
    client_id: env.clientId,
    response_type: "code",
    scope: "openid",
    redirect_uri: `${env.publicUrl}/auth/callback`,
    state,
    nonce: randomBytes(8).toString("hex"),
    code_challenge: createHash("sha256").update(verifier).digest("base64url"),
    code_challenge_method: "S256",
  });
  return redirect(`${env.issuer}/authorize?${q}`, { headers: { "set-cookie": await commitSession(session) } });
}

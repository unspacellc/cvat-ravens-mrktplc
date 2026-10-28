import { redirect } from "react-router";

import { env } from "~/host/env.server";
import { commitSession, getSession } from "~/host/session.server";

export async function loader({ request }: { request: Request }) {
  const url = new URL(request.url);
  const session = await getSession(request);
  const state = session.get("oidcState");
  if (!state || state !== url.searchParams.get("state") || !url.searchParams.get("code")) {
    throw new Response("Invalid sign-in response", { status: 400 });
  }
  const res = await fetch(`${env.issuer}/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code: url.searchParams.get("code")!,
      redirect_uri: `${env.publicUrl}/auth/callback`,
      client_id: env.clientId,
      client_secret: env.clientSecret,
      code_verifier: session.get("codeVerifier") ?? "",
    }),
  });
  if (!res.ok) throw new Response("Sign-in failed", { status: 502 });
  const tok = (await res.json()) as { access_token: string; expires_in?: number };
  session.unset("oidcState");
  session.unset("codeVerifier");
  session.set("accessToken", tok.access_token);
  session.set("expiresAt", Math.floor(Date.now() / 1000) + (tok.expires_in ?? 3600));
  session.unset("tenantId");
  return redirect("/", { headers: { "set-cookie": await commitSession(session) } });
}

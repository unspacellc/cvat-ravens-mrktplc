import { apiFetch } from "~/host/api.server";
import { getSession } from "~/host/session.server";

/**
 * Same-origin proxy so the browser never holds the access token. State-changing calls must carry a custom header
 * (not sendable cross-site without CORS preflight) in addition to the SameSite=Lax session cookie.
 */
async function proxy(request: Request, splat: string | undefined) {
  const session = await getSession(request);
  const token = session.get("accessToken");
  const unauth = (code: string) => Response.json({ error: "session expired", statusCode: 401, code, details: {}, requestId: "host" }, { status: 401 });
  if (!token || (session.get("expiresAt") ?? 0) < Date.now() / 1000) return unauth("invalid_token");
  if (request.method !== "GET" && request.headers.get("x-requested-with") !== "labeling") {
    return Response.json({ error: "missing csrf header", statusCode: 403, code: "csrf", details: {}, requestId: "host" }, { status: 403 });
  }
  const search = new URL(request.url).search;
  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  const upstream = await apiFetch(`/${splat ?? ""}${search}`, token, session.get("tenantId"), {
    method: request.method,
    body: hasBody ? await request.text() : undefined,
    headers: {
      ...(hasBody ? { "content-type": "application/json" } : {}),
      ...(request.headers.get("idempotency-key") ? { "idempotency-key": request.headers.get("idempotency-key")! } : {}),
    },
  });
  return new Response(upstream.status === 204 ? null : await upstream.text(), {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
  });
}

export const loader = ({ request, params }: { request: Request; params: { "*"?: string } }) => proxy(request, params["*"]);
export const action = ({ request, params }: { request: Request; params: { "*"?: string } }) => proxy(request, params["*"]);

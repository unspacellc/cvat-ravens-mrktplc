import { Form, redirect, useOutletContext } from "react-router";

import { apiFetch } from "~/host/api.server";
import { commitSession, requireToken } from "~/host/session.server";

/** The chosen tenant is validated against the user's real memberships here and again by the API on every call. */
export async function action({ request }: { request: Request }) {
  const { token, session } = await requireToken(request);
  const chosen = String((await request.formData()).get("tenant") ?? "");
  const tenants = (await (await apiFetch("/me/tenants", token)).json()) as { items: { tenant_id: string }[] };
  if (!tenants.items.some((t) => t.tenant_id === chosen)) throw new Response("Not a member of that tenant", { status: 403 });
  session.set("tenantId", chosen);
  return redirect("/", { headers: { "set-cookie": await commitSession(session) } });
}

export default function SelectTenant() {
  const { tenants } = useOutletContext<{ tenants?: string[] }>() ?? {};
  return (
    <section>
      <h1 className="mb-3 text-xl font-semibold">Choose a tenant</h1>
      <Form method="post" className="flex flex-col gap-2">
        {(tenants ?? []).map((t) => (
          <button key={t} name="tenant" value={t} className="rounded-md border border-border p-3 text-left">
            {t}
          </button>
        ))}
      </Form>
    </section>
  );
}

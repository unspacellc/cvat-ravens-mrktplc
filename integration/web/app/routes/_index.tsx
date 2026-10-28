import { redirect } from "react-router";

import { apiFetch } from "~/host/api.server";
import { requireToken } from "~/host/session.server";

export async function loader({ request }: { request: Request }) {
  const { token, tenantId } = await requireToken(request);
  const tenants = (await (await apiFetch("/me/tenants", token)).json()) as { items: { tenant_id: string; roles: string[] }[] };
  const t = tenants.items.find((x) => x.tenant_id === tenantId) ?? (tenants.items.length === 1 ? tenants.items[0] : undefined);
  if (!t) throw redirect("/select-tenant");
  // Leads/admins land on the board; people whose only job is labeling or reviewing land on their inbox.
  return redirect(t.roles.includes("admin") || t.roles.includes("creator") ? "/board" : "/my-jobs");
}

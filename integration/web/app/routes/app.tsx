import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { Form, Link, Outlet, redirect, useLoaderData } from "react-router";

import { createApi, LabelingProvider } from "~/features/labeling/api";
import type { Me } from "~/features/labeling/types";
import { apiFetch } from "~/host/api.server";
import { requireToken } from "~/host/session.server";

export async function loader({ request }: { request: Request }) {
  const { token, tenantId } = await requireToken(request);
  const path = new URL(request.url).pathname;
  const tenantsRes = await apiFetch("/me/tenants", token);
  if (!tenantsRes.ok) throw redirect("/login");
  const tenants = ((await tenantsRes.json()) as { items: { tenant_id: string; roles: string[] }[] }).items;
  if (tenants.length === 0) throw new Response("You do not have access to labeling.", { status: 403 });
  const chosen = tenants.find((t) => t.tenant_id === tenantId)?.tenant_id ?? (tenants.length === 1 ? tenants[0].tenant_id : undefined);
  if (!chosen) {
    if (path === "/select-tenant") return { me: null, tenants: tenants.map((t) => t.tenant_id) };
    throw redirect("/select-tenant");
  }
  const meRes = await apiFetch("/me", token, chosen);
  if (!meRes.ok) throw new Response("Could not load your profile.", { status: meRes.status });
  return { me: (await meRes.json()) as Me, tenants: tenants.map((t) => t.tenant_id) };
}

export default function AppLayout() {
  const { me, tenants } = useLoaderData<typeof loader>();
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 5_000 } } }));
  const [api] = useState(() => createApi("/bff/labeling"));
  const can = (...r: string[]) => !!me && me.roles.some((x) => r.includes(x));
  return (
    <QueryClientProvider client={client}>
      <LabelingProvider api={api} loginUrl="/login">
        <header className="border-b border-border">
          <nav aria-label="Primary" className="mx-auto flex max-w-5xl items-center gap-4 p-3 text-sm">
            <strong>Labeling</strong>
            {can("labeler", "reviewer") ? <Link to="/my-jobs">My jobs</Link> : null}
            {can("reviewer", "admin", "creator") ? <Link to="/board">Board</Link> : null}
            <span className="ml-auto text-muted-foreground">{me ? `${me.email ?? me.user_id} · ${me.roles.join(", ")}` : null}</span>
            {tenants.length > 1 ? <Link to="/select-tenant">Switch tenant</Link> : null}
            <Form method="post" action="/logout">
              <button className="underline">Sign out</button>
            </Form>
          </nav>
        </header>
        <main className="mx-auto max-w-5xl p-4">
          <Outlet context={{ me, tenants }} />
        </main>
      </LabelingProvider>
    </QueryClientProvider>
  );
}

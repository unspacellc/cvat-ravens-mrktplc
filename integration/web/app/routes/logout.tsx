import { redirect } from "react-router";

import { destroySession, getSession } from "~/host/session.server";
import { tokenStore } from "~/host/token-store.server";

export async function action({ request }: { request: Request }) {
  const session = await getSession(request);
  const id = session.get("sessionId");
  if (id) await tokenStore.delete(id);
  return redirect("/login", { headers: { "set-cookie": await destroySession(session) } });
}

export const loader = () => redirect("/");

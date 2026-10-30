import { redirect } from "react-router";

import { deleteSession } from "~/host/marketplace.server";
import { destroySession, getSession } from "~/host/session.server";

export async function action({ request }: { request: Request }) {
  const session = await getSession(request);
  const [id, token] = [session.get("marketplaceSessionId"), session.get("accessToken")];
  if (id && token) await deleteSession(id, token);
  return redirect("/login", { headers: { "set-cookie": await destroySession(session) } });
}

export const loader = () => redirect("/");

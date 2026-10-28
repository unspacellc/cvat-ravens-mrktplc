import { redirect } from "react-router";

import { destroySession, getSession } from "~/host/session.server";

export async function action({ request }: { request: Request }) {
  return redirect("/login", { headers: { "set-cookie": await destroySession(await getSession(request)) } });
}

export const loader = () => redirect("/");

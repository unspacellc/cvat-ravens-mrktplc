import { Form, redirect, useActionData, useNavigation } from "react-router";

import { login, LoginRejected } from "~/host/marketplace.server";
import { commitSession, getSession } from "~/host/session.server";

export const meta = () => [{ title: "Login - R.A.V.E.N.S Marketplace - Labelspace" }];

export async function action({ request }: { request: Request }) {
  const form = await request.formData();
  const email = String(form.get("email") ?? "").trim();
  const password = String(form.get("password") ?? "");
  if (!email || !password) return { error: "Enter your email and password.", email };
  try {
    const r = await login(email, password);
    const session = await getSession(request);
    session.set("accessToken", r.accessToken);
    session.set("expiresAt", r.expiresAt);
    session.set("marketplaceSessionId", r.sessionId);
    session.unset("tenantId");
    return redirect("/", { headers: { "set-cookie": await commitSession(session) } });
  } catch (e) {
    return { error: e instanceof LoginRejected ? "Incorrect email or password." : "Sign-in is unavailable right now. Please try again.", email };
  }
}

export default function Login() {
  const data = useActionData<typeof action>();
  const busy = useNavigation().state === "submitting";
  return (
    <div className="flex min-h-screen font-sans">
      <aside className="hidden w-1/2 flex-col justify-center bg-black px-12 lg:flex">
        <p className="text-sm tracking-widest text-white/70">R.A.V.E.N.S</p>
        <p className="mt-6 text-4xl leading-tight text-white">Review, label and approve rail imagery.</p>
      </aside>
      <main className="flex w-full items-center justify-center bg-white p-8 lg:w-1/2">
        <div className="w-full max-w-md space-y-8">
          <header className="space-y-1 text-center">
            <h1 className="text-3xl font-semibold text-gray-900">
              R.A.V.E.N.S Marketplace - <span className="text-red-600">Labelspace</span>
            </h1>
            <p className="text-xs text-gray-500">powered by CVAT community</p>
          </header>
          {data?.error ? (
            <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-600">
              {data.error}
            </div>
          ) : null}
          <Form method="post" className="space-y-4">
            <div className="space-y-2">
              <label htmlFor="email" className="text-sm font-medium text-gray-900">
                Email
              </label>
              <input id="email" name="email" type="email" autoComplete="username" required defaultValue={data?.email}
                className="h-12 w-full rounded-lg border border-gray-200 bg-white px-3 text-gray-900 focus:border-black focus:outline-none" />
            </div>
            <div className="space-y-2">
              <label htmlFor="password" className="text-sm font-medium text-gray-900">
                Password
              </label>
              <input id="password" name="password" type="password" autoComplete="current-password" required
                className="h-12 w-full rounded-lg border border-gray-200 bg-white px-3 text-gray-900 focus:border-black focus:outline-none" />
            </div>
            <button type="submit" disabled={busy} className="h-12 w-full cursor-pointer rounded-lg bg-black font-medium text-white disabled:opacity-60">
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </Form>
        </div>
      </main>
    </div>
  );
}

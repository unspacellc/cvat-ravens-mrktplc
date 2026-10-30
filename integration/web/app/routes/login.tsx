import { Authenticator } from "@aws-amplify/ui-react";
import "@aws-amplify/ui-react/styles.css";
import { Amplify } from "aws-amplify";
import { fetchAuthSession, signOut } from "aws-amplify/auth";
import { cognitoUserPoolsTokenProvider } from "aws-amplify/auth/cognito";
import { sessionStorage as amplifySessionStorage } from "aws-amplify/utils";
import { useEffect, useState } from "react";
import { useLoaderData, useNavigate } from "react-router";

import { readRefreshToken } from "~/host/cognito-tokens";
import { env } from "~/host/env.server";

export const loader = () => ({ clientId: env.cognito.userPoolClientId, userPoolId: env.cognito.userPoolId, configured: !!env.cognito.userPoolId && !!env.cognito.userPoolClientId });

/** Hands the tokens Amplify holds to the host server, which validates and records them (see auth.session.tsx). */
function SyncSession({ clientId }: { clientId: string }) {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    void (async () => {
      const { tokens } = await fetchAuthSession();
      const refreshToken = readRefreshToken(window.sessionStorage, clientId);
      if (!tokens?.accessToken || !refreshToken) return setError("Sign-in did not return a usable session. Please try again.");
      const res = await fetch("/auth/session", {
        method: "POST",
        headers: { "content-type": "application/json", "x-requested-with": "labeling" },
        body: JSON.stringify({ accessToken: tokens.accessToken.toString(), refreshToken }),
      });
      if (res.ok) return navigate("/", { replace: true });
      setError(res.status === 401 ? "Your account was not accepted." : "Sign-in could not be completed. Please try again.");
    })();
  }, [clientId, navigate]);
  return error ? (
    <p role="alert" className="mt-4 text-sm text-red-700">
      {error}
    </p>
  ) : (
    <p role="status" className="mt-4 text-sm text-muted-foreground">
      Finishing sign-in…
    </p>
  );
}

export default function Login() {
  const { clientId, userPoolId, configured } = useLoaderData<typeof loader>();
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (!configured) return;
    Amplify.configure({ Auth: { Cognito: { userPoolId, userPoolClientId: clientId } } });
    // Tokens live only for the browser tab; the host cookie session is what authorizes API calls.
    cognitoUserPoolsTokenProvider.setKeyValueStorage(amplifySessionStorage);
    void (new URLSearchParams(window.location.search).has("signedout") ? signOut() : Promise.resolve()).finally(() => setReady(true));
  }, [clientId, userPoolId, configured]);

  if (!configured) {
    return (
      <main className="mx-auto max-w-md p-8" role="alert">
        <h1 className="text-xl font-semibold">Sign-in is not configured</h1>
        <p className="mt-2 text-muted-foreground">Set COGNITO_USER_POOL_ID and COGNITO_USER_POOL_CLIENT_ID for this host.</p>
      </main>
    );
  }
  return (
    <main className="mx-auto max-w-md p-8">
      <h1 className="mb-4 text-xl font-semibold">Sign in to Labeling</h1>
      {ready ? (
        <Authenticator hideSignUp>
          <SyncSession clientId={clientId} />
        </Authenticator>
      ) : null}
    </main>
  );
}

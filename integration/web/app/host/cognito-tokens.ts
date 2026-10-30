/**
 * Amplify's public API does not expose the Cognito refresh token, but the marketplace session record requires it.
 * Read it from the key-value storage Amplify itself writes to (key layout of `createKeysForAuthStorage`).
 */
const PREFIX = "CognitoIdentityServiceProvider";

export function readRefreshToken(storage: Pick<Storage, "getItem">, clientId: string): string | null {
  const user = storage.getItem(`${PREFIX}.${clientId}.LastAuthUser`);
  return user ? storage.getItem(`${PREFIX}.${clientId}.${user}.refreshToken`) : null;
}

/** `exp` (epoch seconds) of a JWT payload. Display/bookkeeping only; the APIs verify the signature. */
export function jwtExp(token: string): number | null {
  try {
    const exp = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))).exp;
    return typeof exp === "number" ? exp : null;
  } catch {
    return null;
  }
}

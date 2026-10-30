import { jwtExp, readRefreshToken } from "./cognito-tokens";

const store = (items: Record<string, string>) => ({ getItem: (k: string) => items[k] ?? null });
const jwt = (payload: object) => `h.${btoa(JSON.stringify(payload)).replace(/\+/g, "-").replace(/\//g, "_")}.s`;

describe("readRefreshToken", () => {
  it("reads the token for the last authenticated user of this client", () => {
    const s = store({ "CognitoIdentityServiceProvider.c1.LastAuthUser": "u1", "CognitoIdentityServiceProvider.c1.u1.refreshToken": "r" });
    expect(readRefreshToken(s, "c1")).toBe("r");
  });
  it("is null when nobody is signed in or the client differs", () => {
    expect(readRefreshToken(store({}), "c1")).toBeNull();
    expect(readRefreshToken(store({ "CognitoIdentityServiceProvider.c2.LastAuthUser": "u" }), "c1")).toBeNull();
  });
});

describe("jwtExp", () => {
  it("returns exp", () => expect(jwtExp(jwt({ exp: 123 }))).toBe(123));
  it("is null for malformed tokens", () => {
    expect(jwtExp("nope")).toBeNull();
    expect(jwtExp(jwt({}))).toBeNull();
  });
});

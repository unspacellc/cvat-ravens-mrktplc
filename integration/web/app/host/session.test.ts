import { resolveAuth, commitSession, getSession } from "./session.server";
import { tokenStore } from "./token-store.server";

const nowS = () => Math.floor(Date.now() / 1000);
const jwt = (claims: object) => `h.${Buffer.from(JSON.stringify(claims)).toString("base64url")}.s`;

async function requestFor(sessionId?: string) {
  const session = await getSession(new Request("http://h/"));
  if (sessionId) session.set("sessionId", sessionId);
  return new Request("http://h/", { headers: { cookie: (await commitSession(session)).split(";")[0] } });
}

afterEach(() => vi.unstubAllGlobals());

describe("resolveAuth", () => {
  it("returns the stored token when it is not near expiry, without calling the marketplace API", async () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    await tokenStore.put("ok", { accessToken: "A", refreshToken: "R", expiresAt: nowS() + 3600 });
    expect((await resolveAuth(await requestFor("ok")))?.token).toBe("A");
    expect(f).not.toHaveBeenCalled();
  });

  it("is null without a cookie session or a stored session", async () => {
    expect(await resolveAuth(await requestFor())).toBeNull();
    expect(await resolveAuth(await requestFor("missing"))).toBeNull();
  });

  it("refreshes near expiry, stores the result and shares one refresh across parallel requests", async () => {
    const old = jwt({ username: "cog-user" });
    await tokenStore.put("near", { accessToken: old, refreshToken: "R1", expiresAt: nowS() + 30 });
    const f = vi.fn(async (..._a: unknown[]) => Response.json({ tokens: { accessToken: "NEW", expiresIn: 3600 } }));
    vi.stubGlobal("fetch", f);
    const req = await requestFor("near");
    const [a, b] = await Promise.all([resolveAuth(req), resolveAuth(req)]);
    expect([a?.token, b?.token]).toEqual(["NEW", "NEW"]);
    expect(f).toHaveBeenCalledTimes(1);
    expect(JSON.parse((f.mock.calls[0][1] as RequestInit).body as string)).toEqual({ refreshToken: "R1", username: "cog-user" });
    expect(await tokenStore.get("near")).toMatchObject({ accessToken: "NEW", refreshToken: "R1" }); // refresh token kept: no rotation
  });

  it("ends the sign-in when the refresh token is rejected", async () => {
    await tokenStore.put("dead", { accessToken: jwt({ username: "u" }), refreshToken: "R", expiresAt: nowS() - 10 });
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));
    expect(await resolveAuth(await requestFor("dead"))).toBeNull();
    expect(await tokenStore.get("dead")).toBeNull();
  });

  it("keeps a still-valid token and the session when the refresh endpoint is down", async () => {
    await tokenStore.put("flaky", { accessToken: jwt({ username: "u" }), refreshToken: "R", expiresAt: nowS() + 30 });
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 503 })));
    expect((await resolveAuth(await requestFor("flaky")))?.token).toContain("h.");
    expect(await tokenStore.get("flaky")).not.toBeNull();
  });
});

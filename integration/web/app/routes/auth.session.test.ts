import { action } from "./auth.session";

const jwt = (exp: number) => `h.${btoa(JSON.stringify({ exp }))}.s`;
const future = () => Math.floor(Date.now() / 1000) + 3600;
const post = (body: unknown, headers: Record<string, string> = { "x-requested-with": "labeling" }) =>
  action({ request: new Request("http://host/auth/session", { method: "POST", headers, body: JSON.stringify(body) }) });

afterEach(() => vi.unstubAllGlobals());

describe("auth/session action", () => {
  it("rejects requests without the csrf header", async () => {
    expect((await post({}, {})).status).toBe(403);
  });

  it("rejects expired or incomplete tokens without calling the marketplace API", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    expect((await post({ accessToken: jwt(1), refreshToken: "r" })).status).toBe(400);
    expect((await post({ accessToken: jwt(future()) })).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("starts no session when the marketplace API rejects the token", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));
    const res = await post({ accessToken: jwt(future()), refreshToken: "r" });
    expect(res.status).toBe(401);
    expect(res.headers.get("set-cookie")).toBeNull();
  });

  it("validates, records the session, then sets the cookie", async () => {
    const calls: [string, RequestInit | undefined][] = [];
    vi.stubGlobal("fetch", vi.fn(async (u: string, init?: RequestInit) => {
      calls.push([u, init]);
      return u.endsWith("/auth/me") ? Response.json({ id: "u1", email: "a@b.c" }) : new Response("{}", { status: 201 });
    }));
    const token = jwt(future());
    const res = await post({ accessToken: token, refreshToken: "r" });
    expect(res.status).toBe(204);
    expect(res.headers.get("set-cookie")).toContain("__labeling_host=");
    expect(calls.map(([u]) => u.split("/api/v1")[1])).toEqual(["/auth/me", "/sessions"]);
    const sent = JSON.parse(calls[1][1]!.body as string);
    expect(sent).toMatchObject({ access_token: token, refresh_token: "r", user_id: "u1" });
    expect(new Date(sent.expires_at).toString()).not.toBe("Invalid Date");
  });

  it("fails closed when recording the session fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async (u: string) => (u.endsWith("/auth/me") ? Response.json({ id: "u1", email: "a" }) : new Response("", { status: 500 }))));
    const res = await post({ accessToken: jwt(future()), refreshToken: "r" });
    expect(res.status).toBe(502);
    expect(res.headers.get("set-cookie")).toBeNull();
  });
});

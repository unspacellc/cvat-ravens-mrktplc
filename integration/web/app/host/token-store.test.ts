import { MarketplaceTokenStore, MemoryTokenStore } from "./token-store.server";

const T = { accessToken: "a", refreshToken: "r", expiresAt: 1_900_000_000 };
afterEach(() => vi.unstubAllGlobals());

describe("MarketplaceTokenStore", () => {
  it("uses the service key as bearer and the marketplace field names", async () => {
    const f = vi.fn(async (..._a: unknown[]) => new Response("{}", { status: 201 }));
    vi.stubGlobal("fetch", f);
    await new MarketplaceTokenStore("https://m", "KEY").put("s 1", T, "u1");
    const [url, init] = f.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://m/api/v1/sessions");
    expect((init.headers as Record<string, string>).authorization).toBe("Bearer KEY");
    expect(JSON.parse(init.body as string)).toEqual({ session_id: "s 1", access_token: "a", refresh_token: "r", expires_at: "2030-03-17T17:46:40.000Z", user_id: "u1" });
  });

  it("reads tokens back, returns null for unknown sessions and throws on outages", async () => {
    const store = new MarketplaceTokenStore("https://m", "KEY");
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ access_token: "a", refresh_token: "r", expires_at: "2030-03-17T17:46:40Z" })));
    expect(await store.get("s")).toEqual(T);
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 404 })));
    expect(await store.get("s")).toBeNull();
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 500 })));
    await expect(store.get("s")).rejects.toThrow();
  });

  it("never throws from delete", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("down"); }));
    await expect(new MarketplaceTokenStore("https://m", "KEY").delete("s")).resolves.toBeUndefined();
  });
});

describe("MemoryTokenStore", () => {
  it("round-trips and deletes", async () => {
    const s = new MemoryTokenStore();
    await s.put("x", T);
    expect(await s.get("x")).toEqual(T);
    await s.delete("x");
    expect(await s.get("x")).toBeNull();
  });
});

import { render, screen } from "@testing-library/react";
import { createRoutesStub } from "react-router";

import Login, { action } from "./login";

const jsonRes = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const submit = (fields: Record<string, string>) =>
  action({ request: new Request("http://host/login", { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" }, body: new URLSearchParams(fields).toString() }) });

afterEach(() => vi.unstubAllGlobals());

describe("login page", () => {
  it("shows the Labelspace title with CVAT credit", () => {
    const Stub = createRoutesStub([{ path: "/login", Component: Login }]);
    render(<Stub initialEntries={["/login"]} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("R.A.V.E.N.S Marketplace - Labelspace");
    expect(screen.getByText("Labelspace")).toHaveClass("text-red-600");
    expect(screen.getByText("powered by CVAT community")).toHaveClass("text-xs", "text-gray-500");
  });
});

describe("login action", () => {
  it("posts credentials to /auth/login and starts the session from the response", async () => {
    const f = vi.fn(async (..._a: unknown[]) => jsonRes({ sessionId: "s1", tokens: { accessToken: "tok", expiresIn: 3600 } }));
    vi.stubGlobal("fetch", f);
    const res = (await submit({ email: "a@b.c", password: "pw" })) as Response;
    expect(f.mock.calls[0][0]).toMatch(/\/api\/v1\/auth\/login$/);
    expect(JSON.parse((f.mock.calls[0][1] as RequestInit).body as string)).toEqual({ email: "a@b.c", password: "pw" });
    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("/");
    expect(res.headers.get("set-cookie")).toContain("__labeling_host=");
  });

  it.each([401, 400])("shows a generic error and sets no cookie when the API answers %i", async (status) => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonRes({ error: "x" }, status)));
    expect(await submit({ email: "a@b.c", password: "bad" })).toEqual({ error: "Incorrect email or password.", email: "a@b.c" });
  });

  it("treats a response without tokens as a failed sign-in", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonRes({ message: "challenge" })));
    expect(await submit({ email: "a@b.c", password: "pw" })).toMatchObject({ error: "Incorrect email or password." });
  });

  it("reports an outage separately from bad credentials", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonRes({}, 503)));
    expect(await submit({ email: "a@b.c", password: "pw" })).toMatchObject({ error: expect.stringContaining("unavailable") });
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("down"); }));
    expect(await submit({ email: "a@b.c", password: "pw" })).toMatchObject({ error: expect.stringContaining("unavailable") });
  });

  it("does not call the API when a field is empty", async () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    expect(await submit({ email: "", password: "x" })).toMatchObject({ error: expect.any(String) });
    expect(f).not.toHaveBeenCalled();
  });
});

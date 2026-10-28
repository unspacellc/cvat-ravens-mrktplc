import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { MyJobs } from "./MyJobs";
import { apiError, job, json, renderWith } from "./test-utils";

describe("MyJobs", () => {
  it("shows a loading state", () => {
    renderWith(<MyJobs />, () => "pending");
    expect(screen.getByRole("status")).toHaveTextContent(/loading your jobs/i);
  });

  it("shows an empty state", async () => {
    renderWith(<MyJobs />, () => json({ items: [], next_cursor: null }));
    expect(await screen.findByTestId("empty")).toHaveTextContent("No jobs assigned to you");
  });

  it("shows an error state with retry, and recovers", async () => {
    let fail = true;
    renderWith(<MyJobs />, () => (fail ? apiError(500, "boom", "Server error") : json({ items: [job()], next_cursor: null })));
    expect(await screen.findByRole("alert")).toHaveTextContent("Server error");
    expect(screen.getByRole("alert")).toHaveTextContent("req-1");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByText("Crossing batch")).toBeInTheDocument();
  });

  it("sends an expired session to sign-in instead of retrying", async () => {
    renderWith(<MyJobs />, () => apiError(401, "invalid_token"));
    const link = await screen.findByRole("link", { name: /sign in again/i });
    expect(link).toHaveAttribute("href", "/login");
  });

  it("flags stale projections with their age", async () => {
    renderWith(<MyJobs />, () => json({ items: [job({ sync_age_s: 4000 }), job({ id: "j2", sync_age_s: null }), job({ id: "j3", sync_age_s: 20 })], next_cursor: null }));
    const ages = await screen.findAllByTestId("sync-age");
    expect(ages.map((a) => a.textContent)).toEqual(["Stale · Synced 1 h ago", "Not synced yet", "Synced just now"]);
  });

  it("keeps earlier data visible with a banner when a refresh fails", async () => {
    let n = 0;
    const { client } = renderWith(<MyJobs />, () => (n++ === 0 ? json({ items: [job()], next_cursor: null }) : apiError(503, "down", "Service unavailable")));
    await screen.findByText("Crossing batch");
    await client.refetchQueries();
    expect(await screen.findByTestId("stale-banner")).toHaveTextContent("Service unavailable");
    expect(screen.getByText("Crossing batch")).toBeInTheDocument();
  });

  it("opens the editor at the server-provided URL only", async () => {
    const navigate = vi.fn();
    const { calls } = renderWith(<MyJobs />, (url) => (url.includes("editor-launch") ? json({ url: "https://cvat.example/api/auth/social/oidc/portal/login/?next=%2Ftasks%2F1" }) : json({ items: [job()], next_cursor: null })), { navigate });
    await userEvent.click(await screen.findByRole("button", { name: /open editor/i }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("https://cvat.example/api/auth/social/oidc/portal/login/?next=%2Ftasks%2F1"));
    const launch = calls.find((c) => c.url.includes("editor-launch"))!;
    expect(launch.init.method).toBe("POST");
    expect(launch.url).toBe("/bff/labeling/jobs/j1/editor-launch");
  });

  it.each([
    ["assignment_stale", 409, /still updating/i],
    ["not_assigned", 403, /no longer assigned to you/i],
    ["not_found", 404, /no longer available/i],
  ])("explains launch failure %s without navigating", async (code, status, text) => {
    const navigate = vi.fn();
    renderWith(<MyJobs />, (url) => (url.includes("editor-launch") ? apiError(status, code) : json({ items: [job()], next_cursor: null })), { navigate });
    await userEvent.click(await screen.findByRole("button", { name: /open editor/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(text);
    expect(navigate).not.toHaveBeenCalled();
  });
});

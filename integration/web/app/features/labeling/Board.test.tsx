import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Board } from "./Board";
import { apiError, job, json, renderWith } from "./test-utils";

const members = { items: [{ user_id: "u1", roles: ["labeler"], email: "ada@example.com" }, { user_id: "u2", roles: ["labeler"], email: "bo@example.com" }, { user_id: "u9", roles: ["admin"], email: "admin@example.com" }] };

describe("Board", () => {
  it("covers loading, empty and error states", async () => {
    const a = renderWith(<Board roles={["admin"]} />, () => "pending");
    expect(screen.getByRole("status")).toHaveTextContent(/loading board/i);
    a.unmount();
    const b = renderWith(<Board roles={["admin"]} />, () => json({ items: [], next_cursor: null }));
    expect(await screen.findByTestId("empty")).toHaveTextContent("No jobs match");
    b.unmount();
    renderWith(<Board roles={["admin"]} />, () => apiError(403, "forbidden"));
    expect(await screen.findByRole("alert")).toHaveTextContent("not allowed");
  });

  it("only offers assignment to roles that may assign", async () => {
    const handler = () => json({ items: [job()], next_cursor: null });
    const a = renderWith(<Board roles={["reviewer"]} />, handler);
    await screen.findByText("Crossing batch");
    expect(screen.queryByRole("button", { name: /assign/i })).toBeNull();
    a.unmount();
    renderWith(<Board roles={["admin"]} />, handler);
    expect(await screen.findByRole("button", { name: /reassign|assign/i })).toBeInTheDocument();
  });

  it("filters by status server-side and unassigned client-side, and pages", async () => {
    const { calls } = renderWith(<Board roles={["admin"]} />, (url) => {
      if (url.includes("cursor=50")) return json({ items: [job({ id: "j3", batch_name: "Second page", assignee_user_id: null })], next_cursor: null });
      return json({ items: [job({ id: "j1" }), job({ id: "j2", assignee_user_id: null, batch_name: "Loose job" })], next_cursor: 50 });
    });
    await screen.findByText("Loose job");
    await userEvent.click(screen.getByRole("button", { name: /load more/i }));
    await screen.findByText("Second page");
    await userEvent.selectOptions(screen.getByLabelText(/filter by assignment/i), "unassigned");
    expect(screen.queryByText("Crossing batch")).toBeNull();
    expect(screen.getByText("Loose job")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText(/filter by status/i), "submitted");
    expect(calls.some((c) => c.url.includes("state=submitted"))).toBe(true);
  });

  it("assigns with the job's current revision", async () => {
    const { calls } = renderWith(<Board roles={["admin"]} />, (url, init) => {
      if (url.endsWith("/members")) return json(members);
      if (url.endsWith("/assignment")) return json({ id: "j1", revision: 4 });
      return json({ items: [job({ assignee_user_id: null })], next_cursor: null });
    });
    await userEvent.click(await screen.findByRole("button", { name: /assign/i }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByText(/admin@example.com/)).toBeNull(); // admins are not offered as labelers
    await userEvent.selectOptions(await within(dialog).findByLabelText("Assignee"), "u2");
    await userEvent.click(within(dialog).getByRole("button", { name: "Assign" }));
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const post = calls.find((c) => c.url.endsWith("/assignment"))!;
    expect(JSON.parse(post.init.body as string)).toEqual({ user_id: "u2", expected_revision: 3 });
    expect((post.init.headers as Record<string, string>)["x-requested-with"]).toBe("labeling");
  });

  it("explains an assignment conflict and refreshes", async () => {
    const { calls } = renderWith(<Board roles={["admin"]} />, (url) => {
      if (url.endsWith("/members")) return json(members);
      if (url.endsWith("/assignment")) return apiError(409, "revision_conflict");
      return json({ items: [job()], next_cursor: null });
    });
    await userEvent.click(await screen.findByRole("button", { name: /reassign/i }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.selectOptions(await within(dialog).findByLabelText("Assignee"), "u2");
    const before = calls.filter((c) => c.url.includes("/jobs?")).length;
    await userEvent.click(within(dialog).getByRole("button", { name: "Assign" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/someone else just changed/i);
    await vi.waitFor(() => expect(calls.filter((c) => c.url.includes("/jobs?")).length).toBeGreaterThan(before));
  });
});

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ReviewDialog } from "./ReviewDialog";
import { apiError, job, json, renderWith } from "./test-utils";

const review = (over = {}) => ({ portal_state: "in_review", revision: 5, live_snapshot_sha256: "a".repeat(64), events: [], issues: [], ...over });

describe("ReviewDialog (reviewer)", () => {
  const inReview = job({ portal_state: "in_review", revision: 5 });

  it("approves only with an attestation, bound to the content hash the server returned", async () => {
    const { calls } = renderWith(<ReviewDialog job={inReview} open onOpenChange={() => {}} />, (url, init) =>
      init.method === "POST" ? json({ id: "j1", portal_state: "approved", revision: 6 }) : json(review())
    );
    const approve = await screen.findByRole("button", { name: "Approve" });
    expect(approve).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/attestation/i), "All frames reviewed");
    await userEvent.click(approve);
    await waitFor(() => expect(calls.some((c) => c.init.method === "POST")).toBe(true));
    const post = calls.find((c) => c.init.method === "POST")!;
    expect(post.url).toContain("/jobs/j1/transitions");
    expect(JSON.parse(post.init.body as string)).toEqual({
      expected_revision: 5, command: "approve", attestation: "All frames reviewed", expected_snapshot_sha256: "a".repeat(64),
    });
  });

  it("rejects with frame issues and needs at least one", async () => {
    const { calls } = renderWith(<ReviewDialog job={inReview} open onOpenChange={() => {}} />, (url, init) =>
      init.method === "POST" ? json({ id: "j1", portal_state: "rework", revision: 6 }) : json(review())
    );
    const reject = await screen.findByRole("button", { name: "Request rework" });
    expect(reject).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Issue description"), "box too loose");
    await userEvent.click(screen.getByRole("button", { name: "Add issue" }));
    expect(screen.getByRole("list", { name: "Issues to send back" })).toHaveTextContent("box too loose");
    await userEvent.click(reject);
    await waitFor(() => expect(calls.some((c) => c.init.method === "POST")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.init.method === "POST")!.init.body as string)).toMatchObject({
      command: "reject", issues: [{ frame: 0, text: "box too loose" }],
    });
  });

  it("tells someone who is not the assigned reviewer that they cannot decide", async () => {
    renderWith(<ReviewDialog job={inReview} open onOpenChange={() => {}} />, () => json(review({ live_snapshot_sha256: null })));
    expect(await screen.findByRole("alert")).toHaveTextContent(/only the assigned reviewer/i);
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("explains a stale decision and refreshes instead of failing silently", async () => {
    renderWith(<ReviewDialog job={inReview} open onOpenChange={() => {}} />, (url, init) =>
      init.method === "POST" ? apiError(409, "revision_conflict", "job revision is 6") : json(review())
    );
    await userEvent.type(await screen.findByLabelText(/attestation/i), "ok");
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByText(/someone else just changed this job/i)).toBeInTheDocument();
  });
});

describe("ReviewDialog (annotator)", () => {
  const rework = job({ portal_state: "rework", revision: 7 });
  const issues = [{ id: "i1", frame: 3, position: null, text: "missed car", blocking: true, status: "open" }];
  const handler = (calls: string[]) => (url: string, init: RequestInit) => {
    if (init.method === "POST") return json({ id: "j1", portal_state: "in_review", revision: 8 });
    if (url.includes("/reviewers")) return json({ items: [{ user_id: "rev1", email: "rev@example.com" }] });
    calls.push(url);
    return json(review({ portal_state: "rework", live_snapshot_sha256: null, issues }));
  };

  it("cannot resubmit until every open issue is marked fixed, then names the reviewer", async () => {
    const { calls } = renderWith(<ReviewDialog job={rework} open onOpenChange={() => {}} />, handler([]));
    const submit = await screen.findByRole("button", { name: "Submit for review" });
    await userEvent.selectOptions(await screen.findByLabelText("Reviewer"), "rev1");
    expect(submit).toBeDisabled(); // issue on frame 3 is not yet fixed
    await userEvent.click(screen.getByLabelText("Mark issue on frame 3 as fixed"));
    expect(submit).toBeEnabled();
    await userEvent.click(submit);
    await waitFor(() => expect(calls.some((c) => c.init.method === "POST")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.init.method === "POST")!.init.body as string)).toEqual({
      expected_revision: 7, command: "submit", reviewer_id: "rev1", resolved_issue_ids: ["i1"],
    });
  });

  it("shows when there is no reviewer to pick", async () => {
    renderWith(<ReviewDialog job={rework} open onOpenChange={() => {}} />, (url) =>
      url.includes("/reviewers") ? json({ items: [] }) : json(review({ portal_state: "rework", live_snapshot_sha256: null }))
    );
    expect(await screen.findByText(/no reviewers available/i)).toBeInTheDocument();
  });
});

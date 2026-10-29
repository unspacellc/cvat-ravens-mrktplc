import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { BatchDetail } from "./BatchDetail";
import { apiError, json, renderWith } from "./test-utils";

const batch = (over = {}) => ({
  id: "b1", name: "Crossing batch", lifecycle: "provisioning", revision: 1, job_counts: { created: 4, annotating: 2 },
  tasks: [{ id: "t1", source_asset_id: "clip-1", status: "ready" }], operation: { id: "o1", state: "succeeded", error: null }, ...over,
});

describe("BatchDetail", () => {
  it("shows loading, error and not-found-style states", async () => {
    const a = renderWith(<BatchDetail batchId="b1" />, () => "pending");
    expect(screen.getByRole("status")).toHaveTextContent(/loading batch/i);
    a.unmount();
    renderWith(<BatchDetail batchId="b1" />, () => apiError(404, "not_found", "batch not found"));
    expect(await screen.findByRole("alert")).toHaveTextContent("batch not found");
  });

  it("shows counts per status and clips", async () => {
    renderWith(<BatchDetail batchId="b1" />, () => json(batch()));
    expect(await screen.findByText("Crossing batch")).toBeInTheDocument();
    expect(screen.getByText("Not started").parentElement).toHaveTextContent("4");
    expect(screen.getByText("clip-1")).toBeInTheDocument();
  });

  it("shows an empty jobs state while ingestion is running", async () => {
    renderWith(<BatchDetail batchId="b1" />, () => json(batch({ job_counts: {}, operation: { id: "o1", state: "running", error: null } })));
    expect(await screen.findByTestId("empty")).toHaveTextContent("No jobs yet");
    expect(screen.getByRole("status", { name: "" })).toHaveTextContent(/preparing tasks/i);
  });

  it("surfaces a failed provisioning operation", async () => {
    renderWith(<BatchDetail batchId="b1" />, () => json(batch({ operation: { id: "o1", state: "failed", error: { message: "clip unreadable" } } })));
    expect(await screen.findByRole("alert")).toHaveTextContent("clip unreadable");
  });

  describe("dataset delivery", () => {
    it("is only offered to admins and only once the batch is ready", async () => {
      const a = renderWith(<BatchDetail batchId="b1" roles={["reviewer"]} />, () => json(batch({ lifecycle: "created" })));
      await screen.findByText("Crossing batch");
      expect(screen.queryByRole("button", { name: /send off/i })).not.toBeInTheDocument();
      a.unmount();
      renderWith(<BatchDetail batchId="b1" roles={["admin"]} />, () => json(batch({ lifecycle: "provisioning" })));
      await screen.findByText("Crossing batch");
      expect(screen.queryByRole("button", { name: /send off/i })).not.toBeInTheDocument();
    });

    it("lists why a send-off is refused", async () => {
      renderWith(<BatchDetail batchId="b1" roles={["admin"]} />, (url, init) =>
        init.method === "POST"
          ? new Response(JSON.stringify({ error: "batch is not eligible", statusCode: 409, code: "not_eligible", details: { reasons: ["job x: state is in_review, not approved"] } }), { status: 409 })
          : json(batch({ lifecycle: "created" }))
      );
      await userEvent.click(await screen.findByRole("button", { name: /send off dataset/i }));
      expect(await screen.findByRole("alert")).toHaveTextContent("state is in_review, not approved");
    });

    it("sends with an idempotency key, then shows the published version and offers a revision batch", async () => {
      const { calls } = renderWith(<BatchDetail batchId="b1" roles={["admin"]} />, (url, init) => {
        if (url.endsWith("/send-offs")) return json({ dataset_version_id: "dv-1234567890", operation_id: "o2" }, 202);
        if (url.endsWith("/revision-batches")) return json({ batch_id: "b2", operation_id: "o3" }, 202);
        return json(batch({ lifecycle: "sent_off", dataset_version: { id: "dv-1234567890", status: "published" } }));
      });
      expect(await screen.findByRole("status", { name: "" })).toBeDefined();
      expect(await screen.findByText(/published/)).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: /start revision batch/i }));
      await waitFor(() => expect(calls.some((c) => c.url.endsWith("/dv-1234567890/revision-batches"))).toBe(true));
      expect((calls.find((c) => c.url.endsWith("/revision-batches"))!.init.headers as Record<string, string>)["idempotency-key"]).toBeTruthy();
    });
  });
});

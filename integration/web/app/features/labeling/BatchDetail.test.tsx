import { screen } from "@testing-library/react";

import { BatchDetail } from "./BatchDetail";
import { apiError, json, renderWith } from "./test-utils";

const batch = (over = {}) => ({
  id: "b1", name: "Crossing batch", lifecycle: "provisioning", revision: 1, job_counts: { created: 4, in_progress: 2 },
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
});

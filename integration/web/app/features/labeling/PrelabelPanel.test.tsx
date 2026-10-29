import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { BatchDetail } from "./BatchDetail";
import { json, renderWith } from "./test-utils";

const batch = (prelabel: unknown = null, over = {}) => ({
  id: "b1", name: "Crossing batch", lifecycle: "created", revision: 1, job_counts: { created: 2 }, tasks: [], operation: { id: "o1", state: "succeeded", error: null },
  prelabel, ...over,
});
const run = (over = {}) => ({ id: "r1", status: "succeeded", model_id: "m1", error: null, zero_detection: false, detections_imported: 12, ...over });
const models = { items: [{ model_id: "m1", provider: "sagemaker_async", version: "1.0.0", supported: true }, { model_id: "nova", provider: "bedrock_nova", version: null, supported: false }] };

const handler = (b: unknown, extra: (url: string, init: RequestInit) => Response | null = () => null) => (url: string, init: RequestInit) =>
  extra(url, init) ?? (url.endsWith("/models") ? json(models) : json(b));

describe("PrelabelPanel", () => {
  it("is optional, offers only supported models, and starts with an idempotency key", async () => {
    const { calls } = renderWith(<BatchDetail batchId="b1" roles={["admin"]} />, handler(batch(), (url, init) => (init.method === "POST" ? json({ run_id: "r1" }, 202) : null)));
    expect(await screen.findByText(/skip it to label manually/i)).toBeInTheDocument();
    const select = await screen.findByLabelText("Model");
    expect(screen.queryByRole("option", { name: /nova/ })).not.toBeInTheDocument();
    await userEvent.selectOptions(select, "m1");
    await userEvent.click(screen.getByRole("button", { name: "Start pre-labeling" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/prelabel-runs"))).toBe(true));
    const post = calls.find((c) => c.url.endsWith("/prelabel-runs"))!;
    expect(JSON.parse(post.init.body as string)).toEqual({ model_id: "m1" });
    expect((post.init.headers as Record<string, string>)["idempotency-key"]).toBeTruthy();
  });

  it("says labeling is disabled while running and offers cancel and manual fallback", async () => {
    const { calls } = renderWith(<BatchDetail batchId="b1" roles={["creator"]} />, handler(batch(run({ status: "running" })), (url, init) => (init.method === "POST" ? json({ id: "r1", status: "cancelled" }) : null)));
    expect(await screen.findByRole("status", { name: "" })).toBeDefined();
    expect(await screen.findByText(/labeling is disabled until the import finishes/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel pre-labeling" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/prelabel-runs/r1/cancel"))).toBe(true));
    expect(screen.getByRole("button", { name: "Label manually instead" })).toBeInTheDocument();
  });

  it("keeps success, zero detections and failure visibly different", async () => {
    const a = renderWith(<BatchDetail batchId="b1" roles={["admin"]} />, handler(batch(run())));
    expect(await screen.findByText(/12 boxes imported/)).toBeInTheDocument();
    a.unmount();
    const b = renderWith(<BatchDetail batchId="b1" roles={["admin"]} />, handler(batch(run({ zero_detection: true, detections_imported: 0 }))));
    expect(await screen.findByText(/found no objects/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    b.unmount();
    renderWith(<BatchDetail batchId="b1" roles={["admin"]} />, handler(batch(run({ status: "failed", error: "2 of 10 inference request(s) failed" }))));
    expect(await screen.findByRole("alert")).toHaveTextContent(/failed.*Nothing was imported/);
    expect(screen.getByRole("button", { name: "Retry failed requests" })).toBeInTheDocument();
  });

  it("is hidden from reviewers", async () => {
    renderWith(<BatchDetail batchId="b1" roles={["reviewer"]} />, handler(batch()));
    await screen.findByText("Crossing batch");
    expect(screen.queryByLabelText("Pre-labeling")).not.toBeInTheDocument();
  });
});

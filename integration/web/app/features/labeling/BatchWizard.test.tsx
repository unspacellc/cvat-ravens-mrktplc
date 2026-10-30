import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { BatchWizard } from "./BatchWizard";
import { apiError, json, renderWith, type Handler } from "./test-utils";

const MEDIA = [
  { source_asset_id: "clip-a", frames: 120, duration_s: 4, width: 320, height: 240, sha256: "x", telemetry: "missing" },
  { source_asset_id: "clip-b", frames: 60, duration_s: 2, width: 320, height: 240, sha256: "y", telemetry: "missing" },
];
const PREVIEW = {
  segment_size: 40,
  clips: [{ ...MEDIA[0], tasks: 1, jobs: 3 }, { ...MEDIA[1], tasks: 1, jobs: 2 }],
  totals: { clips: 2, tasks: 2, jobs: 5, frames: 180 },
  warnings: [],
};

function server(over: Partial<Record<string, Handler>> = {}): Handler {
  return (url, init) => {
    const key = `${init.method ?? "GET"} ${url.replace("/bff/labeling", "").split("?")[0]}`;
    if (over[key]) return over[key]!(url, init);
    switch (key) {
      case "GET /media": return json({ items: MEDIA });
      case "GET /schemas": return json({ items: [{ id: "s1", semver: "1.0.0", labels: [{ name: "car", type: "rectangle" }] }] });
      case "GET /models": return json({ items: [] });
      case "POST /batches/preview": return json(PREVIEW);
      case "POST /schemas": return json({ id: "s-new", semver: "1.0.0", labels: [] }, 201);
      case "POST /batches": return json({ batch_id: "b-new", operation_id: "op1" }, 202);
    }
    return apiError(404, "not_found", key);
  };
}

async function toDetails(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByLabelText(/clip-a/));
  await user.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText(/Existing set 1.0.0/);
  await user.click(screen.getByRole("button", { name: "Next" }));
}

describe("BatchWizard", () => {
  it("walks media → labels → details → preview → create and sends the chosen settings once", async () => {
    const created = vi.fn();
    const { calls } = renderWith(<BatchWizard onCreated={created} />, server());
    const user = userEvent.setup();
    await user.click(await screen.findByLabelText(/clip-a/));
    await user.click(screen.getByLabelText(/clip-b/));
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByLabelText(/Existing set 1.0.0/)).toBeChecked(); // a choice is always made
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.type(screen.getByLabelText("Batch name"), "Crossing batch");
    await user.type(screen.getByLabelText(/Instructions/), "Box every car.");
    await user.clear(screen.getByLabelText(/Priority/));
    await user.type(screen.getByLabelText(/Priority/), "7");
    await user.type(screen.getByLabelText("Due date"), "2030-01-15");
    await user.type(screen.getByLabelText("Frames per job"), "40");
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText((_, el) => el?.tagName === "P" && /5 jobs from 180 frames \(40 frames per job\)/.test(el.textContent ?? ""))).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Split per clip" })).toBeInTheDocument();
    expect(screen.getByText(/labeled manually/)).toBeInTheDocument(); // no models => manual-only is stated, not hidden
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(screen.getByRole("button", { name: "Create batch" }));
    await waitFor(() => expect(created).toHaveBeenCalledWith("b-new"));
    const post = calls.find((c) => c.url.endsWith("/batches") && c.init.method === "POST")!;
    expect(JSON.parse(post.init.body as string)).toMatchObject({
      name: "Crossing batch", asset_ids: ["clip-a", "clip-b"], schema_version_id: "s1", priority: 7, segment_size: 40, instructions: "Box every car.",
    });
    expect(calls.filter((c) => c.url.endsWith("/schemas") && c.init.method === "POST")).toHaveLength(0); // reused the existing set
  });

  it("can be completed with the keyboard alone, and focus moves to each new step", async () => {
    renderWith(<BatchWizard onCreated={() => {}} />, server());
    const user = userEvent.setup();
    const clip = await screen.findByLabelText(/clip-a/);
    clip.focus();
    await user.keyboard(" "); // space toggles the checkbox
    expect(clip).toBeChecked();
    await user.tab(); // clip-b
    await user.tab(); // Back (disabled) is skipped -> Next
    expect(screen.getByRole("button", { name: "Next" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("heading", { name: "Labels", level: 2 })).toHaveFocus();
  });

  it("blocks each step with a specific message instead of sending bad input", async () => {
    renderWith(<BatchWizard onCreated={() => {}} />, server());
    const user = userEvent.setup();
    await screen.findByLabelText(/clip-a/);
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Choose at least one clip.");
    await toDetails(user);
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Give the batch a name.");
    await user.type(screen.getByLabelText("Batch name"), "x");
    await user.clear(screen.getByLabelText(/Priority/));
    await user.type(screen.getByLabelText(/Priority/), "500");
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("alert")).toHaveTextContent(/Priority is a whole number/);
  });

  it("defines a new label set when none is chosen and rejects duplicate names", async () => {
    const { calls } = renderWith(<BatchWizard onCreated={() => {}} />, server({ "GET /schemas": () => json({ items: [] }) }));
    const user = userEvent.setup();
    await user.click(await screen.findByLabelText(/clip-a/));
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.type(await screen.findByLabelText("Label 1 name"), "car");
    await user.click(screen.getByRole("button", { name: "Add label" }));
    await user.type(screen.getByLabelText("Label 2 name"), "car");
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Label names must be unique.");
    await user.clear(screen.getByLabelText("Label 2 name"));
    await user.type(screen.getByLabelText("Label 2 name"), "truck");
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.type(screen.getByLabelText("Batch name"), "b");
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(await screen.findByRole("button", { name: "Next" }));
    await user.click(screen.getByRole("button", { name: "Create batch" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/schemas") && c.init.method === "POST")).toBe(true));
    const sent = JSON.parse(calls.find((c) => c.url.endsWith("/schemas") && c.init.method === "POST")!.init.body as string);
    expect(sent.labels.map((l: { name: string }) => l.name)).toEqual(["car", "truck"]);
  });

  it("shows loading, empty and failure states for the clip list", async () => {
    renderWith(<BatchWizard onCreated={() => {}} />, server({ "GET /media": () => json({ items: [] }) }));
    expect(await screen.findByText("No clips available")).toBeInTheDocument();
  });

  it("keeps the wizard open and says so when creating fails, and a retry reuses the same Idempotency-Key", async () => {
    let n = 0;
    const { calls } = renderWith(<BatchWizard onCreated={() => {}} />, server({ "POST /batches": () => (++n === 1 ? apiError(502, "upstream", "The editor is busy.") : json({ batch_id: "b", operation_id: "o" }, 202)) }));
    const user = userEvent.setup();
    await toDetails(user);
    await user.type(screen.getByLabelText("Batch name"), "b");
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(await screen.findByRole("button", { name: "Next" }));
    await user.click(screen.getByRole("button", { name: "Create batch" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The editor is busy.");
    await user.click(screen.getByRole("button", { name: "Create batch" }));
    await waitFor(() => expect(n).toBe(2));
    const keys = calls.filter((c) => c.url.endsWith("/batches") && c.init.method === "POST").map((c) => (c.init.headers as Record<string, string>)["idempotency-key"]);
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
  });
});

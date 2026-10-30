import { screen } from "@testing-library/react";

import { MetricsPanel } from "./MetricsPanel";
import { apiError, json, renderWith } from "./test-utils";

const overview = (over = {}) => ({
  window_days: 14, jobs_by_state: { created: 2, in_review: 1 }, throughput: { approved_per_day: { "2026-10-29": 2, "2026-10-30": 1 }, approved_total: 3 },
  rejection: { rejected: 1, approved: 3, rate: 0.25 }, queue_age: { in_review: { jobs: 1, oldest_age_s: 7200 } },
  prelabel: { runs: 0, by_status: {}, inference_duration_s: null, correction_metrics: "per run" },
  time_on_task: { available: false, reason: "not instrumented; no estimate is shown" }, tasks_by_status: { ready: 2 }, ...over,
});

describe("MetricsPanel", () => {
  it("shows throughput, rejection and queue age from the server numbers", async () => {
    renderWith(<MetricsPanel />, () => json(overview()));
    expect(await screen.findByRole("region", { name: "Rejection rate" })).toHaveTextContent("25%");
    expect(screen.getByRole("region", { name: /Throughput/ })).toHaveTextContent("3");
    expect(screen.getByRole("region", { name: /Queue age/ })).toHaveTextContent("oldest 2 h ago");
  });

  it("labels time on task as unavailable and does not invent pre-label numbers", async () => {
    renderWith(<MetricsPanel />, () => json(overview()));
    expect(await screen.findByTestId("time-on-task")).toHaveTextContent("Not available: not instrumented");
    expect(screen.getByRole("region", { name: "Pre-labeling" })).toHaveTextContent("No pre-label runs. Labeling is manual.");
  });

  it("shows pre-label inference duration when runs exist, and 'no decisions yet' with none", async () => {
    renderWith(<MetricsPanel />, () => json(overview({ rejection: { rejected: 0, approved: 0, rate: null }, prelabel: { runs: 2, by_status: { succeeded: 2 }, inference_duration_s: { avg: 12.5, max: 20 }, correction_metrics: "per run" } })));
    expect(await screen.findByRole("region", { name: "Rejection rate" })).toHaveTextContent("No decisions yet");
    expect(screen.getByRole("region", { name: "Pre-labeling" })).toHaveTextContent("inference avg 12.5s, max 20s");
  });

  it("has empty and error states", async () => {
    const a = renderWith(<MetricsPanel />, () => json(overview({ jobs_by_state: {} })));
    expect(await screen.findByTestId("empty")).toHaveTextContent("No work yet");
    a.unmount();
    renderWith(<MetricsPanel />, () => apiError(403, "forbidden", "no"));
    expect(await screen.findByRole("alert")).toHaveTextContent("You are not allowed to do that.");
  });
});

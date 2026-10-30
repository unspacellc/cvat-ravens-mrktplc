import { screen } from "@testing-library/react";

import { Batches } from "./Batches";
import { apiError, json, renderWith } from "./test-utils";

const row = { id: "b1", name: "Crossing batch", lifecycle: "created", priority: 3, due_date: "2030-01-15T23:59:59Z", clips: 2, job_counts: { created: 3, annotating: 1 }, created_at: "2026-10-30T00:00:00Z" };

describe("Batches", () => {
  it("lists batches with clip count, due date and job counts, linking to the batch", async () => {
    renderWith(<Batches roles={["admin"]} />, () => json({ items: [row] }));
    const link = await screen.findByRole("link", { name: "Crossing batch" });
    expect(link).toHaveAttribute("href", "/batches/b1");
    expect(screen.getByText("2 clips")).toBeInTheDocument();
    expect(screen.getByText("Not started").parentElement).toHaveTextContent("3");
    expect(screen.getByRole("link", { name: "New batch" })).toHaveAttribute("href", "/batches/new");
  });

  it("hides New batch from roles that cannot create, and says what the empty list means", async () => {
    renderWith(<Batches roles={["reviewer"]} />, () => json({ items: [] }));
    expect(await screen.findByTestId("empty")).toHaveTextContent("No batches yet");
    expect(screen.queryByRole("link", { name: "New batch" })).toBeNull();
  });

  it("shows loading and failure states", async () => {
    const a = renderWith(<Batches roles={["admin"]} />, () => "pending");
    expect(screen.getByRole("status")).toHaveTextContent(/loading batches/i);
    a.unmount();
    renderWith(<Batches roles={["admin"]} />, () => apiError(500, "boom", "Server exploded"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Server exploded");
  });
});

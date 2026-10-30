import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ThemeToggle } from "./ThemeToggle";

beforeEach(() => {
  document.documentElement.classList.remove("dark");
  localStorage.clear();
  window.matchMedia = ((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia;
});

describe("ThemeToggle", () => {
  it("switches the dark class, remembers the choice and exposes the pressed state", async () => {
    const user = userEvent.setup();
    render(<ThemeToggle />);
    await user.click(screen.getByRole("button", { name: "dark" }));
    expect(document.documentElement).toHaveClass("dark");
    expect(screen.getByRole("button", { name: "dark" })).toHaveAttribute("aria-pressed", "true");
    expect(localStorage.getItem("theme")).toBe("dark");
    await user.click(screen.getByRole("button", { name: "light" }));
    expect(document.documentElement).not.toHaveClass("dark");
  });

  it("still switches when storage is blocked", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    const user = userEvent.setup();
    render(<ThemeToggle />);
    await user.click(screen.getByRole("button", { name: "dark" }));
    expect(document.documentElement).toHaveClass("dark");
    vi.restoreAllMocks();
  });
});

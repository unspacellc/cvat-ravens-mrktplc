import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const css = readFileSync(resolve(__dirname, "globals.css"), "utf8");

function block(selector: string): Record<string, string> {
  const m = css.match(new RegExp(`(?:^|\\n)${selector.replace(".", "\\.")}\\s*\\{([^}]*)\\}`));
  if (!m) throw new Error(`no ${selector} block`);
  return Object.fromEntries([...m[1].matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})/g)].map((x) => [x[1], x[2]]));
}

const lum = (hex: string) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (a: string, b: string) => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

// text on its background (WCAG AA normal text = 4.5), focus ring against the page (non-text = 3)
const TEXT_PAIRS: [string, string][] = [
  ["foreground", "background"], ["foreground", "card"], ["muted-foreground", "background"], ["muted-foreground", "card"], ["muted-foreground", "muted"],
  ["primary-foreground", "primary"], ["brand", "background"], ["brand", "card"],
  ["danger-soft-fg", "danger-soft"], ["danger-strong-fg", "danger-strong"], ["warn-soft-fg", "warn-soft"], ["info-soft-fg", "info-soft"],
  ["ok-soft-fg", "ok-soft"], ["neutral-soft-fg", "neutral-soft"],
];

describe.each([[":root"], [".dark"]])("%s theme tokens", (sel) => {
  // .dark only overrides; fall back to :root for anything it does not redefine, as the cascade does
  const t = { ...block(":root"), ...(sel === ".dark" ? block(".dark") : {}) };
  it.each(TEXT_PAIRS)("%s on %s meets 4.5:1", (fg, bg) => {
    expect(contrast(t[fg], t[bg])).toBeGreaterThanOrEqual(4.5);
  });
  it("focus ring is visible against the page (3:1)", () => {
    expect(contrast(t.ring, t.background)).toBeGreaterThanOrEqual(3);
  });
});

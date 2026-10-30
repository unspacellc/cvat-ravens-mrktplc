import { useLabeling } from "./api";

const TONES: Record<string, string> = {
  created: "bg-neutral-soft text-neutral-soft-fg",
  annotating: "bg-info-soft text-info-soft-fg",
  in_review: "bg-warn-soft text-warn-soft-fg",
  rework: "bg-danger-soft text-danger-soft-fg",
  approved: "bg-ok-soft text-ok-soft-fg",
  sent_off: "bg-ok-soft text-ok-soft-fg",
};

const LABELS: Record<string, string> = {
  created: "Not started",
  annotating: "In progress",
  in_review: "In review",
  rework: "Needs rework",
  approved: "Approved",
  sent_off: "Sent off",
};

/** Text label first; colour is reinforcement only. */
export function StatusBadge({ state }: { state: string }) {
  return (
    <span className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ${TONES[state] ?? TONES.created}`}>
      {LABELS[state] ?? state}
    </span>
  );
}

export function formatAge(s: number): string {
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function SyncAge({ seconds }: { seconds: number | null }) {
  const { staleAfterS } = useLabeling();
  const stale = seconds === null || seconds > staleAfterS;
  return (
    <span className={stale ? "rounded bg-warn-soft px-1.5 py-0.5 text-xs text-warn-soft-fg" : "text-xs text-muted-foreground"} data-testid="sync-age">
      {seconds === null ? "Not synced yet" : `${stale ? "Stale · " : ""}Synced ${formatAge(seconds)}`}
    </span>
  );
}

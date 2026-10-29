import { useLabeling } from "./api";

const TONES: Record<string, string> = {
  created: "bg-slate-200 text-slate-900",
  annotating: "bg-blue-100 text-blue-950",
  in_review: "bg-amber-100 text-amber-950",
  rework: "bg-red-100 text-red-950",
  approved: "bg-green-100 text-green-950",
  sent_off: "bg-green-100 text-green-950",
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
    <span className={stale ? "rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-950" : "text-xs text-muted-foreground"} data-testid="sync-age">
      {seconds === null ? "Not synced yet" : `${stale ? "Stale · " : ""}Synced ${formatAge(seconds)}`}
    </span>
  );
}

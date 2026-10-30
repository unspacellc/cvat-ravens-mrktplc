import { useQuery } from "@tanstack/react-query";

import { useLabeling } from "./api";
import { Async, Empty } from "./states";
import { formatAge, StatusBadge } from "./status";
import type { Overview } from "./types";

const pct = (r: number | null) => (r === null ? "No decisions yet" : `${Math.round(r * 100)}%`);

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-md border border-border p-3" aria-label={title}>
      <h2 className="mb-2 text-sm font-medium text-muted-foreground">{title}</h2>
      {children}
    </section>
  );
}

/** Operational metrics. Anything that is not measured says so; nothing is estimated. */
export function MetricsPanel({ days = 14 }: { days?: number }) {
  const { api } = useLabeling();
  const q = useQuery({ queryKey: ["labeling", "overview", days], queryFn: () => api.overview(days), refetchInterval: 60_000 });
  return (
    <section aria-labelledby="metrics-h">
      <h1 id="metrics-h" className="mb-3 text-xl font-semibold">
        Metrics <span className="text-sm font-normal text-muted-foreground">last {days} days</span>
      </h1>
      <Async query={q} label="Loading metrics" isEmpty={(o) => Object.keys(o.jobs_by_state).length === 0} empty={<Empty title="No work yet" hint="Metrics appear once a batch has jobs." />}>
        {(o) => <Body o={o} />}
      </Async>
    </section>
  );
}

function Body({ o }: { o: Overview }) {
  const days = Object.entries(o.throughput.approved_per_day);
  const max = Math.max(1, ...days.map(([, n]) => n));
  const queue = Object.entries(o.queue_age);
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Card title="Jobs by status">
        <ul className="flex flex-wrap gap-2">
          {Object.entries(o.jobs_by_state).map(([s, n]) => (
            <li key={s} className="flex items-center gap-1">
              <StatusBadge state={s} /> <span className="font-medium">{n}</span>
            </li>
          ))}
        </ul>
      </Card>
      <Card title="Throughput (approved jobs per day)">
        <p className="text-2xl font-semibold">{o.throughput.approved_total}</p>
        {days.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing approved in this period.</p>
        ) : (
          <ul className="mt-2 space-y-1 text-xs">
            {days.map(([day, n]) => (
              <li key={day} className="flex items-center gap-2">
                <span className="w-20 text-muted-foreground">{day}</span>
                <span className="h-2 rounded bg-primary" style={{ width: `${(n / max) * 100}%`, minWidth: 4 }} aria-hidden />
                <span>{n}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card title="Rejection rate">
        <p className="text-2xl font-semibold">{pct(o.rejection.rate)}</p>
        <p className="text-sm text-muted-foreground">
          {o.rejection.rejected} rejected · {o.rejection.approved} approved
        </p>
      </Card>
      <Card title="Queue age (oldest job per status)">
        {queue.length === 0 ? (
          <p className="text-sm text-muted-foreground">No open jobs.</p>
        ) : (
          <table className="w-full text-left text-sm">
            <tbody>
              {queue.map(([s, v]) => (
                <tr key={s}>
                  <td className="py-0.5">
                    <StatusBadge state={s} />
                  </td>
                  <td className="py-0.5">{v.jobs} job{v.jobs === 1 ? "" : "s"}</td>
                  <td className="py-0.5 text-muted-foreground">oldest {formatAge(v.oldest_age_s)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Card title="Pre-labeling">
        {o.prelabel.runs === 0 ? (
          <p className="text-sm text-muted-foreground">No pre-label runs. Labeling is manual.</p>
        ) : (
          <p className="text-sm">
            {o.prelabel.runs} run{o.prelabel.runs === 1 ? "" : "s"}
            {o.prelabel.inference_duration_s ? ` · inference avg ${o.prelabel.inference_duration_s.avg}s, max ${o.prelabel.inference_duration_s.max}s` : ""}
          </p>
        )}
        <p className="mt-1 text-xs text-muted-foreground">Correction metrics are measured per run against the approved result.</p>
      </Card>
      <Card title="Time on task">
        <p className="text-sm text-muted-foreground" data-testid="time-on-task">
          Not available: {o.time_on_task.reason}.
        </p>
      </Card>
    </div>
  );
}

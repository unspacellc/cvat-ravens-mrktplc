import { useQuery } from "@tanstack/react-query";

import { useLabeling } from "./api";
import { Async, Empty } from "./states";
import { StatusBadge } from "./status";
import type { Role } from "./types";

/** Batch list for creators/admins/reviewers. Links are plain anchors so the module does not depend on the host's router. */
export function Batches({ roles, base = "" }: { roles: Role[]; base?: string }) {
  const { api } = useLabeling();
  const q = useQuery({ queryKey: ["labeling", "batches"], queryFn: api.batches, refetchInterval: 20_000 });
  const canCreate = roles.includes("admin") || roles.includes("creator");
  return (
    <section aria-labelledby="batches-h">
      <div className="mb-3 flex items-center justify-between">
        <h1 id="batches-h" className="text-xl font-semibold">
          Batches
        </h1>
        {canCreate ? (
          <a href={`${base}/batches/new`} className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground">
            New batch
          </a>
        ) : null}
      </div>
      <Async query={q} label="Loading batches" isEmpty={(d) => d.items.length === 0} empty={<Empty title="No batches yet" hint={canCreate ? "Create one to start labeling." : "Batches appear here once an admin creates them."} />}>
        {(d) => (
          <ul className="divide-y divide-border rounded-md border border-border">
            {d.items.map((b) => (
              <li key={b.id} className="flex flex-wrap items-center gap-3 p-3 text-sm">
                <a className="font-medium underline" href={`${base}/batches/${b.id}`}>
                  {b.name}
                </a>
                <span className="text-muted-foreground">{b.clips ?? 0} clip{b.clips === 1 ? "" : "s"}</span>
                {b.due_date ? <span className="text-muted-foreground">due {new Date(b.due_date).toLocaleDateString()}</span> : null}
                <span className="ml-auto flex flex-wrap gap-1">
                  {Object.entries(b.job_counts ?? {}).map(([state, n]) => (
                    <span key={state} className="inline-flex items-center gap-1">
                      <StatusBadge state={state} />
                      <span>{n}</span>
                    </span>
                  ))}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Async>
    </section>
  );
}

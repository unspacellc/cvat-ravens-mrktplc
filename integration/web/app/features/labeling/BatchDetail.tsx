import { useQuery } from "@tanstack/react-query";

import { useLabeling } from "./api";
import { Async, Empty } from "./states";
import { StatusBadge } from "./status";

export function BatchDetail({ batchId }: { batchId: string }) {
  const { api } = useLabeling();
  const query = useQuery({ queryKey: ["labeling", "batch", batchId], queryFn: () => api.batch(batchId), refetchInterval: 15_000 });
  return (
    <Async query={query} label="Loading batch" empty={<Empty title="Batch not found" />}>
      {(b) => (
        <section aria-labelledby="batch-h">
          <h1 id="batch-h" className="text-xl font-semibold">
            {b.name}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">Status: {b.lifecycle}</p>
          {b.operation?.state === "failed" || b.operation?.error ? (
            <p role="alert" className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-900">
              Provisioning problem: {b.operation.error?.message ?? "see operation log"}
            </p>
          ) : b.operation && b.operation.state !== "succeeded" ? (
            <p role="status" className="mt-3 rounded-md bg-blue-50 px-3 py-2 text-sm text-blue-950">
              Preparing tasks in the editor ({b.operation.state})…
            </p>
          ) : null}
          <h2 className="mb-2 mt-5 font-medium">Jobs by status</h2>
          {Object.keys(b.job_counts).length === 0 ? (
            <Empty title="No jobs yet" hint="Jobs appear once clips finish ingesting." />
          ) : (
            <ul className="flex flex-wrap gap-3">
              {Object.entries(b.job_counts).map(([state, n]) => (
                <li key={state} className="flex items-center gap-2 rounded-md border border-border px-3 py-2">
                  <StatusBadge state={state} />
                  <span className="font-medium">{n}</span>
                </li>
              ))}
            </ul>
          )}
          <h2 className="mb-2 mt-5 font-medium">Clips</h2>
          <ul className="divide-y divide-border rounded-md border border-border">
            {b.tasks.map((t) => (
              <li key={t.id} className="flex justify-between p-3 text-sm">
                <span>{t.source_asset_id}</span>
                <span className="text-muted-foreground">{t.status}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </Async>
  );
}

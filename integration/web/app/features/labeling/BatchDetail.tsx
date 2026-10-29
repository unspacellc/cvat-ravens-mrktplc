import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";

import { ApiError, useLabeling } from "./api";
import { PrelabelPanel } from "./PrelabelPanel";
import { Async, Empty } from "./states";
import { StatusBadge } from "./status";

export function BatchDetail({ batchId, roles = [] }: { batchId: string; roles?: string[] }) {
  const { api } = useLabeling();
  const qc = useQueryClient();
  const key = useRef(crypto.randomUUID()); // one send-off intent per page load: double clicks replay, never duplicate
  const sendOff = useMutation({
    mutationFn: () => api.sendOff(batchId, key.current),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["labeling", "batch", batchId] }),
  });
  const revise = useMutation({
    mutationFn: (versionId: string) => api.revisionBatch(versionId, crypto.randomUUID()),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["labeling"] }),
  });
  const reasons = sendOff.error instanceof ApiError ? ((sendOff.error.details.reasons as string[] | undefined) ?? []) : [];
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
          {roles.some((r) => r === "admin" || r === "creator") ? <PrelabelPanel batch={b} /> : null}
          {roles.includes("admin") ? (
            <div className="mt-4 rounded-md border border-border p-3">
              <h2 className="font-medium">Dataset delivery</h2>
              {b.dataset_version ? (
                <p className="mt-1 text-sm" role="status">
                  Dataset version {b.dataset_version.id.slice(0, 8)}: {b.dataset_version.status === "published" ? "published" : "publishing…"}
                </p>
              ) : null}
              {sendOff.isError ? (
                <div role="alert" className="mt-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-900">
                  <p>{(sendOff.error as Error).message}</p>
                  {reasons.length > 0 ? (
                    <ul className="mt-1 list-disc pl-5">
                      {reasons.map((r) => (
                        <li key={r}>{r}</li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ) : null}
              {!b.dataset_version && b.lifecycle === "created" ? (
                <button className="mt-2 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-60" disabled={sendOff.isPending} onClick={() => sendOff.mutate()}>
                  {sendOff.isPending ? "Starting…" : "Send off dataset"}
                </button>
              ) : null}
              {b.dataset_version?.status === "published" ? (
                <button className="mt-2 rounded-md border border-border px-3 py-1.5 text-sm disabled:opacity-60" disabled={revise.isPending} onClick={() => revise.mutate(b.dataset_version!.id)}>
                  Start revision batch
                </button>
              ) : null}
              {revise.isSuccess ? <p className="mt-2 text-sm">Revision batch is being prepared.</p> : null}
            </div>
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

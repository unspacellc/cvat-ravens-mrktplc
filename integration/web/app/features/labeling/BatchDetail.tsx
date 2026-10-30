import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";

import { ApiError, useLabeling } from "./api";
import { PrelabelPanel } from "./PrelabelPanel";
import { Async, Empty } from "./states";
import { StatusBadge } from "./status";
import type { Provisioning } from "./types";

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
  const retry = useMutation({
    mutationFn: (operationId: string) => api.retryOperation(operationId),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["labeling", "batch", batchId] }),
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
          {b.settings ? (
            <dl className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-sm text-muted-foreground">
              <div><dt className="inline">Priority </dt><dd className="inline text-foreground">{b.settings.priority}</dd></div>
              {b.settings.due_date ? <div><dt className="inline">Due </dt><dd className="inline text-foreground">{new Date(b.settings.due_date).toLocaleDateString()}</dd></div> : null}
              {b.settings.segment_size ? <div><dt className="inline">Frames per job </dt><dd className="inline text-foreground">{b.settings.segment_size}</dd></div> : null}
              {b.settings.instructions_version ? <div><dt className="inline">Instructions </dt><dd className="inline text-foreground">v{b.settings.instructions_version}</dd></div> : null}
            </dl>
          ) : null}
          {b.settings?.instructions ? (
            <details className="mt-2 text-sm">
              <summary className="cursor-pointer">Instructions for labelers</summary>
              <p className="mt-1 whitespace-pre-wrap">{b.settings.instructions}</p>
            </details>
          ) : null}
          {b.provisioning && roles.some((r) => r === "admin" || r === "creator") ? (
            <ProvisioningPanel p={b.provisioning} onRetry={(id) => retry.mutate(id)} retrying={retry.isPending} retryFailed={retry.isError} />
          ) : b.operation?.state === "failed" || b.operation?.error ? (
            <p role="alert" className="mt-3 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger-soft-fg">
              Provisioning problem: {b.operation.error?.message ?? "see operation log"}
            </p>
          ) : b.operation && b.operation.state !== "succeeded" ? (
            <p role="status" className="mt-3 rounded-md bg-info-soft px-3 py-2 text-sm text-info-soft-fg">
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
                <div role="alert" className="mt-2 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger-soft-fg">
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

function ProvisioningPanel({ p, onRetry, retrying, retryFailed }: { p: Provisioning; onRetry: (operationId: string) => void; retrying: boolean; retryFailed: boolean }) {
  const t = p.tasks;
  const summary = `${t.ready ?? 0} of ${t.total ?? 0} clips ready · ${p.jobs_created} job${p.jobs_created === 1 ? "" : "s"} created`;
  if (p.state === "complete") return <p className="mt-3 text-sm text-muted-foreground">{summary}.</p>;
  if (p.state === "failed") {
    return (
      <div role="alert" className="mt-3 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger-soft-fg">
        <p>
          Provisioning problem: {p.error?.message ?? "see operation log"}
        </p>
        <p className="mt-1">
          {summary}
          {t.failed ? ` · ${t.failed} failed` : ""}. Clips that finished are kept; retrying only redoes the rest.
        </p>
        {p.recovery.map((r) => (
          <button key={r.operation_id} className="mt-2 rounded-md bg-danger-strong px-3 py-1.5 text-danger-strong-fg disabled:opacity-60" disabled={retrying} onClick={() => onRetry(r.operation_id)}>
            {retrying ? "Retrying…" : r.label}
          </button>
        ))}
        {retryFailed ? <p className="mt-1">The retry could not be started. Try again.</p> : null}
      </div>
    );
  }
  return (
    <p role="status" className="mt-3 rounded-md bg-info-soft px-3 py-2 text-sm text-info-soft-fg">
      Preparing tasks in the editor ({p.state === "retrying" ? "retrying after a temporary problem" : "in progress"}): {summary}.
    </p>
  );
}

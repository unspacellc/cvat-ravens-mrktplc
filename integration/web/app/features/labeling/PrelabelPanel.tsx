import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { useLabeling } from "./api";
import type { BatchDetailData } from "./types";

const ACTIVE = ["queued", "running"];

/** Outcome text keeps failure, "model found nothing" and cancellation visibly different. */
function outcome(r: NonNullable<BatchDetailData["prelabel"]>): { text: string; tone: "info" | "ok" | "warn" | "error" } {
  switch (r.status) {
    case "queued":
    case "running":
      return { text: "Pre-labeling is running. Labeling is disabled until the import finishes.", tone: "info" };
    case "succeeded":
      return r.zero_detection
        ? { text: "Pre-labeling finished: the model ran successfully but found no objects. Nothing was imported.", tone: "warn" }
        : { text: `Pre-labeling finished: ${r.detections_imported ?? 0} boxes imported. Jobs are ready for labeling.`, tone: "ok" };
    case "failed":
      return { text: `Pre-labeling failed: ${r.error ?? "unknown error"}. Nothing was imported.`, tone: "error" };
    case "cancelled":
      return { text: "Pre-labeling was cancelled. Nothing was imported.", tone: "warn" };
    case "manual_fallback":
      return { text: "Manual labeling was chosen. Nothing was imported.", tone: "warn" };
    default:
      return { text: r.status, tone: "info" };
  }
}

const TONE = { info: "bg-blue-50 text-blue-950", ok: "bg-green-50 text-green-950", warn: "bg-amber-50 text-amber-950", error: "bg-red-50 text-red-900" };

export function PrelabelPanel({ batch }: { batch: BatchDetailData }) {
  const { api } = useLabeling();
  const qc = useQueryClient();
  const key = useRef(crypto.randomUUID());
  const [model, setModel] = useState("");
  const models = useQuery({ queryKey: ["labeling", "models"], queryFn: api.models });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["labeling"] });
  const start = useMutation({ mutationFn: () => api.startPrelabel(batch.id, model, key.current), onSettled: refresh });
  const command = useMutation({ mutationFn: (v: { id: string; c: "cancel" | "retry" | "manual-fallback" }) => api.prelabelCommand(v.id, v.c), onSettled: refresh });
  const run = batch.prelabel;
  const active = run ? ACTIVE.includes(run.status) : false;
  const usable = models.data?.items?.filter((m) => m.supported) ?? [];
  const canStart = batch.lifecycle === "created" && (!run || ["failed", "cancelled", "manual_fallback"].includes(run.status));

  return (
    <div className="mt-4 rounded-md border border-border p-3" aria-label="Pre-labeling">
      <h2 className="font-medium">Pre-labeling</h2>
      {run ? (
        <p role={run.status === "failed" ? "alert" : "status"} className={`mt-2 rounded-md px-3 py-2 text-sm ${TONE[outcome(run).tone]}`}>
          {outcome(run).text}
        </p>
      ) : (
        <p className="mt-1 text-sm text-muted-foreground">Optional. Skip it to label manually.</p>
      )}
      {run && (active || run.status === "failed") ? (
        <div className="mt-2 flex gap-2">
          {active ? (
            <button className="rounded-md border border-border px-3 py-1.5 text-sm" disabled={command.isPending} onClick={() => command.mutate({ id: run.id, c: "cancel" })}>
              Cancel pre-labeling
            </button>
          ) : (
            <button className="rounded-md border border-border px-3 py-1.5 text-sm" disabled={command.isPending} onClick={() => command.mutate({ id: run.id, c: "retry" })}>
              Retry failed requests
            </button>
          )}
          <button className="rounded-md border border-border px-3 py-1.5 text-sm" disabled={command.isPending} onClick={() => command.mutate({ id: run.id, c: "manual-fallback" })}>
            Label manually instead
          </button>
        </div>
      ) : null}
      {canStart ? (
        <div className="mt-2 flex items-end gap-2">
          <label className="text-sm">
            Model
            <select className="mt-1 block rounded-md border border-border bg-background p-2" value={model} onChange={(e) => setModel(e.target.value)}>
              <option value="">Select a model…</option>
              {usable.map((m) => (
                <option key={m.model_id} value={m.model_id}>
                  {m.model_id} {m.version ? `(${m.version})` : ""}
                </option>
              ))}
            </select>
          </label>
          <button className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-60" disabled={!model || start.isPending} onClick={() => start.mutate()}>
            {start.isPending ? "Starting…" : "Start pre-labeling"}
          </button>
        </div>
      ) : null}
      {models.status === "success" && usable.length === 0 && canStart ? <p className="mt-2 text-sm">No supported models are available for this tenant.</p> : null}
      {start.isError ? (
        <p role="alert" className="mt-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-900">
          {(start.error as Error).message}
        </p>
      ) : null}
    </div>
  );
}

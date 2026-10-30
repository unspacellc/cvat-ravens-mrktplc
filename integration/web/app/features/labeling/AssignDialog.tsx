import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError, useLabeling } from "./api";
import { ErrorState, Loading } from "./states";
import type { Job } from "./types";

export function AssignDialog({ job, open, onOpenChange }: { job: Job; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { api } = useLabeling();
  const qc = useQueryClient();
  const [userId, setUserId] = useState(job.assignee_user_id ?? "");
  const members = useQuery({ queryKey: ["labeling", "members"], queryFn: api.members, enabled: open });
  const assign = useMutation({
    mutationFn: () => api.assign(job.id, userId, job.revision),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["labeling"] });
      onOpenChange(false);
    },
    onError: () => void qc.invalidateQueries({ queryKey: ["labeling"] }), // pick up whoever changed it
  });
  const candidates = members.data?.items.filter((m) => m.roles.includes("labeler") || m.roles.includes("reviewer")) ?? [];
  const conflict = assign.error instanceof ApiError && assign.error.code === "revision_conflict";

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/40" />
        <Dialog.Content className="fixed left-1/2 top-1/2 w-[28rem] max-w-[92vw] -translate-x-1/2 -translate-y-1/2 rounded-lg bg-card p-5 shadow-lg">
          <Dialog.Title className="text-lg font-semibold">Assign job</Dialog.Title>
          <Dialog.Description className="mt-1 text-sm text-muted-foreground">
            {job.batch_name} · {job.source_asset_id} · frames {job.start_frame}–{job.stop_frame}
          </Dialog.Description>
          <div className="mt-4">
            {members.status === "pending" ? <Loading label="Loading people" /> : null}
            {members.status === "error" ? <ErrorState error={members.error} onRetry={() => void members.refetch()} /> : null}
            {members.status === "success" ? (
              candidates.length === 0 ? (
                <p className="text-sm">No labelers or reviewers in this tenant yet.</p>
              ) : (
                <label className="block text-sm">
                  Assignee
                  <select className="mt-1 w-full rounded-md border border-border bg-background p-2" value={userId} onChange={(e) => setUserId(e.target.value)}>
                    <option value="">Select a person…</option>
                    {candidates.map((m) => (
                      <option key={m.user_id} value={m.user_id}>
                        {m.email ?? m.user_id} ({m.roles.join(", ")})
                      </option>
                    ))}
                  </select>
                </label>
              )
            ) : null}
            {assign.isError ? (
              <p role="alert" className="mt-3 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger-soft-fg">
                {conflict ? "Someone else just changed this job. The list was refreshed — review it and try again." : (assign.error as Error).message}
              </p>
            ) : null}
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <Dialog.Close className="rounded-md border border-border px-3 py-1.5 text-sm">Cancel</Dialog.Close>
            <button
              className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-60"
              disabled={!userId || userId === job.assignee_user_id || assign.isPending}
              onClick={() => assign.mutate()}
            >
              {assign.isPending ? "Assigning…" : "Assign"}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

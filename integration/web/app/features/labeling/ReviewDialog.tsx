import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError, useLabeling } from "./api";
import { ErrorState, Loading } from "./states";
import type { Job, TransitionBody } from "./types";

const MESSAGES: Record<string, string> = {
  revision_conflict: "Someone else just changed this job. It was refreshed — look again before deciding.",
  illegal_transition: "This job is no longer in a state where that is possible. It was refreshed.",
  invalid_command: "",
  forbidden: "You are not allowed to do that on this job.",
};

function problem(e: unknown): string {
  if (e instanceof ApiError) return MESSAGES[e.code] || e.message;
  return "Something went wrong. Try again.";
}

/**
 * One dialog, two roles. The assigned reviewer sees the work in review (approve with an attestation, or reject with
 * frame issues); the annotator in rework sees the open issues and resubmits to an independent reviewer. The server
 * decides who may do what; this only offers the actions that fit the job's state.
 */
export function ReviewDialog({ job, open, onOpenChange }: { job: Job; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { api } = useLabeling();
  const qc = useQueryClient();
  const review = useQuery({ queryKey: ["labeling", "review", job.id, job.revision], queryFn: () => api.review(job.id), enabled: open });
  const inReview = job.portal_state === "in_review";
  const reviewers = useQuery({ queryKey: ["labeling", "reviewers"], queryFn: api.reviewers, enabled: open && !inReview });
  const [attestation, setAttestation] = useState("");
  const [reason, setReason] = useState("");
  const [issueText, setIssueText] = useState("");
  const [issueFrame, setIssueFrame] = useState("0");
  const [issues, setIssues] = useState<{ frame: number; text: string }[]>([]);
  const [reviewer, setReviewer] = useState("");
  const [resolved, setResolved] = useState<string[]>([]);

  const run = useMutation({
    mutationFn: (body: TransitionBody) => api.transition(job.id, job.revision, body),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["labeling"] });
      onOpenChange(false);
    },
    onError: () => void qc.invalidateQueries({ queryKey: ["labeling"] }),
  });

  const data = review.data;
  const openIssues = data?.issues.filter((i) => i.status === "open") ?? [];
  const allResolved = openIssues.every((i) => resolved.includes(i.id));

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/40" />
        <Dialog.Content className="fixed left-1/2 top-1/2 max-h-[90vh] w-[34rem] max-w-[94vw] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg bg-card p-5 shadow-lg">
          <Dialog.Title className="text-lg font-semibold">{inReview ? "Review job" : "Submit for review"}</Dialog.Title>
          <Dialog.Description className="mt-1 text-sm text-muted-foreground">
            {job.batch_name} · {job.source_asset_id} · frames {job.start_frame}–{job.stop_frame}
          </Dialog.Description>

          {review.status === "pending" ? <Loading label="Loading review" /> : null}
          {review.status === "error" ? <ErrorState error={review.error} onRetry={() => void review.refetch()} /> : null}

          {data ? (
            <div className="mt-4 space-y-4 text-sm">
              {data.issues.length > 0 ? (
                <section aria-label="Frame issues">
                  <h3 className="font-medium">Frame issues</h3>
                  <ul className="mt-1 space-y-1">
                    {data.issues.map((i) => (
                      <li key={i.id} className="flex items-start gap-2 rounded-md border border-border p-2">
                        {!inReview && i.status === "open" ? (
                          <input
                            type="checkbox"
                            aria-label={`Mark issue on frame ${i.frame} as fixed`}
                            checked={resolved.includes(i.id)}
                            onChange={(e) => setResolved((r) => (e.target.checked ? [...r, i.id] : r.filter((x) => x !== i.id)))}
                          />
                        ) : null}
                        <span>
                          Frame {i.frame}: {i.text} <span className="text-muted-foreground">({i.status})</span>
                        </span>
                      </li>
                    ))}
                  </ul>
                </section>
              ) : null}

              {inReview ? (
                <>
                  {data.live_snapshot_sha256 === null ? (
                    <p role="alert" className="rounded-md bg-warn-soft px-3 py-2 text-warn-soft-fg">
                      Only the assigned reviewer can decide on this job.
                    </p>
                  ) : (
                    <>
                      <label className="block">
                        Review attestation
                        <textarea
                          className="mt-1 w-full rounded-md border border-border bg-background p-2"
                          placeholder="e.g. All frames reviewed"
                          value={attestation}
                          onChange={(e) => setAttestation(e.target.value)}
                        />
                      </label>
                      <fieldset className="rounded-md border border-border p-3">
                        <legend className="px-1 font-medium">Request rework</legend>
                        <div className="flex gap-2">
                          <input aria-label="Issue frame" type="number" min={0} className="w-20 rounded-md border border-border p-1.5" value={issueFrame} onChange={(e) => setIssueFrame(e.target.value)} />
                          <input aria-label="Issue description" className="flex-1 rounded-md border border-border p-1.5" value={issueText} onChange={(e) => setIssueText(e.target.value)} />
                          <button
                            type="button"
                            className="rounded-md border border-border px-2"
                            disabled={!issueText.trim()}
                            onClick={() => {
                              setIssues((l) => [...l, { frame: Number(issueFrame) || 0, text: issueText.trim() }]);
                              setIssueText("");
                            }}
                          >
                            Add issue
                          </button>
                        </div>
                        {issues.length > 0 ? (
                          <ul className="mt-2 list-disc pl-5" aria-label="Issues to send back">
                            {issues.map((i, n) => (
                              <li key={n}>
                                Frame {i.frame}: {i.text}
                              </li>
                            ))}
                          </ul>
                        ) : null}
                        <label className="mt-2 block">
                          Reason
                          <input className="mt-1 w-full rounded-md border border-border p-1.5" value={reason} onChange={(e) => setReason(e.target.value)} />
                        </label>
                      </fieldset>
                    </>
                  )}
                </>
              ) : (
                <label className="block">
                  Reviewer
                  {reviewers.status === "pending" ? <Loading label="Loading reviewers" /> : null}
                  {reviewers.status === "success" && reviewers.data.items.length === 0 ? (
                    <span className="mt-1 block">No reviewers available in this tenant.</span>
                  ) : (
                    <select className="mt-1 w-full rounded-md border border-border bg-background p-2" value={reviewer} onChange={(e) => setReviewer(e.target.value)}>
                      <option value="">Select a reviewer…</option>
                      {reviewers.data?.items.map((r) => (
                        <option key={r.user_id} value={r.user_id}>
                          {r.email ?? r.user_id}
                        </option>
                      ))}
                    </select>
                  )}
                </label>
              )}
            </div>
          ) : null}

          {run.isError ? (
            <p role="alert" className="mt-3 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger-soft-fg">
              {problem(run.error)}
            </p>
          ) : null}

          <div className="mt-5 flex justify-end gap-2">
            <Dialog.Close className="rounded-md border border-border px-3 py-1.5 text-sm">Close</Dialog.Close>
            {inReview && data?.live_snapshot_sha256 ? (
              <>
                <button
                  className="rounded-md border border-border px-3 py-1.5 text-sm disabled:opacity-60"
                  disabled={run.isPending || issues.length === 0}
                  onClick={() => run.mutate({ command: "reject", reason: reason.trim() || "Rework requested", issues })}
                >
                  Request rework
                </button>
                <button
                  className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-60"
                  disabled={run.isPending || !attestation.trim()}
                  onClick={() => run.mutate({ command: "approve", attestation: attestation.trim(), expected_snapshot_sha256: data.live_snapshot_sha256! })}
                >
                  Approve
                </button>
              </>
            ) : null}
            {!inReview ? (
              <button
                className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-60"
                disabled={run.isPending || !reviewer || !allResolved}
                onClick={() => run.mutate({ command: "submit", reviewer_id: reviewer, resolved_issue_ids: resolved })}
              >
                {run.isPending ? "Submitting…" : "Submit for review"}
              </button>
            ) : null}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

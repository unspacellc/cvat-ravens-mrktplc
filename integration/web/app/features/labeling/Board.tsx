import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";

import { AssignDialog } from "./AssignDialog";
import { useLabeling } from "./api";
import { dueLabel, JobTitle } from "./JobRow";
import { Async, Empty } from "./states";
import { StatusBadge, SyncAge } from "./status";
import type { Job, Role } from "./types";

const STATES = ["created", "annotating", "in_review", "rework", "approved", "sent_off"];

/** Tenant-wide job board for leads/admins/reviewers. Assignment is shown only to roles the server would allow. */
export function Board({ roles, batchId }: { roles: Role[]; batchId?: string }) {
  const { api } = useLabeling();
  const [state, setState] = useState("");
  const [assignee, setAssignee] = useState<"" | "unassigned">("");
  const [editing, setEditing] = useState<Job | null>(null);
  const canAssign = roles.includes("admin") || roles.includes("creator");
  const q = useInfiniteQuery({
    queryKey: ["labeling", "board", { state, batchId }],
    queryFn: ({ pageParam }) => api.jobs({ state: state || undefined, batchId }, pageParam),
    initialPageParam: 0,
    getNextPageParam: (p) => p.next_cursor ?? undefined,
  });
  const all = q.data?.pages.flatMap((p) => p.items);
  const rows = assignee === "unassigned" ? all?.filter((j) => !j.assignee_user_id) : all;
  const view = { status: q.status, data: rows, error: q.error, refetch: q.refetch };

  return (
    <section aria-labelledby="board-h">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h1 id="board-h" className="text-xl font-semibold">
          Board
        </h1>
        <div className="flex gap-2 text-sm">
          <label>
            <span className="sr-only">Filter by status</span>
            <select className="rounded-md border border-border bg-background p-1.5" value={state} onChange={(e) => setState(e.target.value)}>
              <option value="">All statuses</option>
              {STATES.map((s) => (
                <option key={s} value={s}>
                  {s.replace("_", " ")}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="sr-only">Filter by assignment</span>
            <select className="rounded-md border border-border bg-background p-1.5" value={assignee} onChange={(e) => setAssignee(e.target.value as "" | "unassigned")}>
              <option value="">Everyone</option>
              <option value="unassigned">Unassigned</option>
            </select>
          </label>
        </div>
      </div>
      <Async query={view} label="Loading board" isEmpty={(d) => d.length === 0} empty={<Empty title="No jobs match" hint="Adjust the filters or create a batch." />}>
        {(jobs) => (
          <>
            <table className="w-full text-left text-sm">
              <thead className="text-muted-foreground">
                <tr>
                  <th className="py-2 pr-3 font-medium">Job</th>
                  <th className="py-2 pr-3 font-medium">Assignee</th>
                  <th className="py-2 pr-3 font-medium">Status</th>
                  <th className="py-2 pr-3 font-medium">Due</th>
                  <th className="py-2 pr-3 font-medium">Sync</th>
                  <th className="py-2 font-medium">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {jobs.map((j) => (
                  <tr key={j.id}>
                    <td className="py-2 pr-3">
                      <JobTitle job={j} />
                    </td>
                    <td className="py-2 pr-3">{j.assignee_user_id ?? <span className="text-muted-foreground">Unassigned</span>}</td>
                    <td className="py-2 pr-3">
                      <StatusBadge state={j.portal_state} />
                    </td>
                    <td className="py-2 pr-3">{dueLabel(j.due_date)}</td>
                    <td className="py-2 pr-3">
                      <SyncAge seconds={j.sync_age_s} />
                    </td>
                    <td className="py-2">
                      {canAssign ? (
                        <button className="rounded-md border border-border px-2.5 py-1" onClick={() => setEditing(j)} aria-label={`${j.assignee_user_id ? "Reassign" : "Assign"} ${j.batch_name} ${j.source_asset_id} frames ${j.start_frame}`}>
                          {j.assignee_user_id ? "Reassign" : "Assign"}
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {q.hasNextPage ? (
              <button className="mt-3 rounded-md border border-border px-3 py-1.5 text-sm" disabled={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>
                {q.isFetchingNextPage ? "Loading…" : "Load more"}
              </button>
            ) : null}
          </>
        )}
      </Async>
      {editing ? <AssignDialog key={editing.id + editing.revision} job={editing} open onOpenChange={(o) => !o && setEditing(null)} /> : null}
    </section>
  );
}

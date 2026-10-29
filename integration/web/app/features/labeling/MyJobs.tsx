import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { useLabeling } from "./api";
import { dueLabel, JobTitle } from "./JobRow";
import { ReviewDialog } from "./ReviewDialog";
import { Async, Empty } from "./states";
import { StatusBadge, SyncAge } from "./status";
import { launchMessage, useEditorLaunch } from "./useEditorLaunch";

/** Labeler/reviewer inbox: only work assigned to the signed-in user (the server enforces this, not the UI). */
export function MyJobs() {
  const { api } = useLabeling();
  const query = useQuery({ queryKey: ["labeling", "my-jobs"], queryFn: () => api.jobs({ assignee: "me" }, 0, 100), refetchInterval: 60_000 });
  const launch = useEditorLaunch();
  const [reviewing, setReviewing] = useState<string | null>(null);

  return (
    <section aria-labelledby="my-jobs-h">
      <h1 id="my-jobs-h" className="mb-4 text-xl font-semibold">
        My jobs
      </h1>
      {launch.isError ? (
        <p role="alert" className="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-900">
          {launchMessage(launch.error)}
        </p>
      ) : null}
      <Async
        query={query}
        label="Loading your jobs"
        isEmpty={(d) => d.items.length === 0}
        empty={<Empty title="No jobs assigned to you" hint="When a lead assigns you work it will appear here." />}
      >
        {(d) => (
          <ul className="divide-y divide-border rounded-md border border-border">
            {d.items.map((j) => (
              <li key={j.id} className="flex flex-wrap items-center justify-between gap-3 p-3">
                <JobTitle job={j} />
                <div className="flex items-center gap-3">
                  <span className="text-sm text-muted-foreground">{dueLabel(j.due_date)}</span>
                  <StatusBadge state={j.portal_state} />
                  <SyncAge seconds={j.sync_age_s} />
                  {["created", "annotating", "rework", "in_review"].includes(j.portal_state) ? (
                    <>
                      <button
                        className="rounded-md border border-border px-3 py-1.5 text-sm"
                        onClick={() => setReviewing(j.id)}
                        aria-label={`${j.portal_state === "in_review" ? "Review" : "Submit"} ${j.batch_name} ${j.source_asset_id}`}
                      >
                        {j.portal_state === "in_review" ? "Review" : "Submit"}
                      </button>
                      {reviewing === j.id ? <ReviewDialog job={j} open onOpenChange={(o) => !o && setReviewing(null)} /> : null}
                    </>
                  ) : null}
                  <button
                    className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-60"
                    disabled={launch.isPending && launch.variables === j.id}
                    onClick={() => launch.mutate(j.id)}
                    aria-label={`Open editor for ${j.batch_name} ${j.source_asset_id}`}
                  >
                    {launch.isPending && launch.variables === j.id ? "Opening…" : "Open editor"}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Async>
    </section>
  );
}

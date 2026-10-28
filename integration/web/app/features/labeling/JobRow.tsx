import type { Job } from "./types";

export function frames(j: Job): string {
  return `Frames ${j.start_frame}–${j.stop_frame}`;
}

export function dueLabel(d: string | null): string {
  return d ? `Due ${new Date(d).toISOString().slice(0, 10)}` : "No due date";
}

export function JobTitle({ job }: { job: Job }) {
  return (
    <div>
      <p className="font-medium">{job.batch_name}</p>
      <p className="text-sm text-muted-foreground">
        {job.source_asset_id} · {frames(job)}
      </p>
    </div>
  );
}

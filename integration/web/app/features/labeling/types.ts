export type Role = "labeler" | "reviewer" | "creator" | "admin";

export interface Job {
  id: string;
  batch_id: string;
  batch_name: string;
  task_id: string;
  source_asset_id: string;
  start_frame: number;
  stop_frame: number;
  assignee_user_id: string | null;
  portal_state: string;
  cvat_stage: string | null;
  cvat_state: string | null;
  revision: number;
  sync_age_s: number | null;
  due_date: string | null;
  priority: number;
}

export interface JobsPage {
  items: Job[];
  next_cursor: number | null;
}

export interface Member {
  user_id: string;
  roles: Role[];
  email: string | null;
}

export interface BatchSummary {
  id: string;
  name: string;
  lifecycle: string;
  priority: number;
  created_at: string;
}

export interface BatchDetailData {
  id: string;
  name: string;
  lifecycle: string;
  revision: number;
  job_counts: Record<string, number>;
  tasks: { id: string; source_asset_id: string; status: string }[];
  operation: { id: string; state: string; error: { message?: string } | null } | null;
  dataset_version?: { id: string; status: string } | null;
  predecessor_version_id?: string | null;
}

export interface ReviewIssue {
  id: string;
  frame: number;
  position: number[] | null;
  text: string;
  blocking: boolean;
  status: "open" | "resolved";
}

export interface ReviewData {
  portal_state: string;
  revision: number;
  live_snapshot_sha256: string | null;
  events: { decision: string; actor_id: string; reason: string | null; annotation_revision: number; created_at: string }[];
  issues: ReviewIssue[];
}

export type TransitionBody =
  | { command: "submit"; reviewer_id: string; resolved_issue_ids?: string[] }
  | { command: "approve"; attestation: string; expected_snapshot_sha256: string }
  | { command: "reject"; reason: string; issues: { frame: number; text: string }[] }
  | { command: "reopen"; reason: string };

export interface Reviewer {
  user_id: string;
  email: string | null;
}

export interface Me {
  user_id: string;
  tenant_id: string;
  roles: Role[];
  email: string | null;
}

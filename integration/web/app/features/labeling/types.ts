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
  due_date?: string | null;
  clips?: number;
  job_counts?: Record<string, number>;
  created_at: string;
}

export interface MediaItem {
  source_asset_id: string;
  frames: number;
  duration_s: number;
  width: number;
  height: number;
  sha256: string;
  telemetry: string | null;
}

export interface LabelDef {
  name: string;
  type: string;
  attributes?: unknown[];
}

export interface SchemaVersion {
  id: string;
  semver: string;
  labels: LabelDef[];
}

export interface PreviewResult {
  segment_size: number;
  clips: (MediaItem & { tasks: number; jobs: number })[];
  totals: { clips: number; tasks: number; jobs: number; frames: number };
  warnings: string[];
}

export interface NewBatch {
  name: string;
  asset_ids: string[];
  schema_version_id: string;
  segment_size?: number;
  instructions?: string;
  priority?: number;
  due_date?: string;
}

export interface Provisioning {
  state: "in_progress" | "retrying" | "failed" | "complete";
  tasks: Record<string, number>;
  jobs_created: number;
  error: { message?: string } | null;
  recovery: { action: "retry"; operation_id: string; label: string }[];
}

export interface Overview {
  window_days: number;
  jobs_by_state: Record<string, number>;
  throughput: { approved_per_day: Record<string, number>; approved_total: number };
  rejection: { rejected: number; approved: number; rate: number | null };
  queue_age: Record<string, { jobs: number; oldest_age_s: number }>;
  prelabel: { runs: number; by_status: Record<string, number>; inference_duration_s: { avg: number; max: number } | null; correction_metrics: string };
  time_on_task: { available: boolean; reason: string };
  tasks_by_status: Record<string, number>;
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
  prelabel?: { id: string; status: string; model_id: string; error: string | null; zero_detection: boolean | null; detections_imported: number | null } | null;
  predecessor_version_id?: string | null;
  settings?: { priority: number; due_date: string | null; instructions: string | null; instructions_version: string | null; segment_size: number | null };
  provisioning?: Provisioning;
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

export interface ModelOption {
  model_id: string;
  provider: string | null;
  version: string | null;
  supported: boolean;
}

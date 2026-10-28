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
}

export interface Me {
  user_id: string;
  tenant_id: string;
  roles: Role[];
  email: string | null;
}

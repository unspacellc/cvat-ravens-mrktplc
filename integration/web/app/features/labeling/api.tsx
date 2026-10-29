import { createContext, useContext, useMemo, type ReactNode } from "react";

import type { BatchDetailData, BatchSummary, JobsPage, Me, Member, ModelOption, ReviewData, Reviewer, TransitionBody } from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId?: string,
    public details: Record<string, unknown> = {}
  ) {
    super(message);
  }
}

export interface JobFilters {
  state?: string;
  assignee?: string;
  batchId?: string;
}

export function createApi(base = "/bff/labeling", doFetch: typeof fetch = (...a) => fetch(...a)) {
  async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
    const method = init.method ?? "GET";
    const res = await doFetch(`${base}${path}`, {
      ...init,
      headers: {
        accept: "application/json",
        ...(method !== "GET" ? { "x-requested-with": "labeling", "content-type": "application/json" } : {}),
        ...init.headers,
      },
    });
    if (res.status === 204) return undefined as T;
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new ApiError(res.status, body.code ?? "error", body.error ?? res.statusText, body.requestId, body.details ?? {});
    }
    return body as T;
  }

  return {
    me: () => call<Me>("/me"),
    jobs: (f: JobFilters, cursor = 0, limit = 50) => {
      const q = new URLSearchParams({ cursor: String(cursor), limit: String(limit) });
      if (f.state) q.set("state", f.state);
      if (f.assignee) q.set("assignee", f.assignee);
      if (f.batchId) q.set("batch_id", f.batchId);
      return call<JobsPage>(`/jobs?${q}`);
    },
    batches: () => call<{ items: BatchSummary[] }>("/batches"),
    batch: (id: string) => call<BatchDetailData>(`/batches/${id}`),
    members: () => call<{ items: Member[] }>("/members"),
    assign: (jobId: string, userId: string, expectedRevision: number) =>
      call<{ id: string; revision: number }>(`/jobs/${jobId}/assignment`, {
        method: "POST",
        body: JSON.stringify({ user_id: userId, expected_revision: expectedRevision }),
      }),
    review: (jobId: string) => call<ReviewData>(`/jobs/${jobId}/review`),
    reviewers: () => call<{ items: Reviewer[] }>("/reviewers"),
    transition: (jobId: string, expectedRevision: number, body: TransitionBody) =>
      call<{ id: string; portal_state: string; revision: number }>(`/jobs/${jobId}/transitions`, {
        method: "POST",
        body: JSON.stringify({ expected_revision: expectedRevision, ...body }),
      }),
    sendOff: (batchId: string, key: string) =>
      call<{ dataset_version_id: string; operation_id: string }>(`/batches/${batchId}/send-offs`, { method: "POST", headers: { "idempotency-key": key } }),
    revisionBatch: (versionId: string, key: string) =>
      call<{ batch_id: string; operation_id: string }>(`/dataset-versions/${versionId}/revision-batches`, { method: "POST", headers: { "idempotency-key": key } }),
    models: () => call<{ items: ModelOption[] }>("/models"),
    startPrelabel: (batchId: string, modelId: string, key: string) =>
      call<{ run_id: string }>(`/batches/${batchId}/prelabel-runs`, { method: "POST", headers: { "idempotency-key": key }, body: JSON.stringify({ model_id: modelId }) }),
    prelabelCommand: (runId: string, command: "cancel" | "retry" | "manual-fallback") =>
      call<{ id: string; status: string }>(`/prelabel-runs/${runId}/${command}`, { method: "POST" }),
    launch: (jobId: string) => call<{ url: string }>(`/jobs/${jobId}/editor-launch`, { method: "POST" }),
  };
}

export type LabelingApi = ReturnType<typeof createApi>;

interface Ctx {
  api: LabelingApi;
  /** Where to send the browser for a fresh sign-in. */
  loginUrl: string;
  /** Replaceable in tests; production navigates the whole window to the editor. */
  navigate: (url: string) => void;
  /** Projection older than this (seconds) is flagged as stale. */
  staleAfterS: number;
}

const LabelingContext = createContext<Ctx | null>(null);

export function LabelingProvider({
  api,
  loginUrl = "/login",
  navigate = (u) => window.location.assign(u),
  staleAfterS = 600,
  children,
}: Partial<Ctx> & { api: LabelingApi; children: ReactNode }) {
  const value = useMemo(() => ({ api, loginUrl, navigate, staleAfterS }), [api, loginUrl, navigate, staleAfterS]);
  return <LabelingContext.Provider value={value}>{children}</LabelingContext.Provider>;
}

export function useLabeling(): Ctx {
  const v = useContext(LabelingContext);
  if (!v) throw new Error("LabelingProvider missing");
  return v;
}

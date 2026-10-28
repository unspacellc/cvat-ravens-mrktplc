import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";

import { createApi, LabelingProvider } from "./api";
import type { Job } from "./types";

export type Handler = (url: string, init: RequestInit) => Response | Promise<Response> | "pending";

export const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
export const apiError = (status: number, code: string, error = code) => json({ error, statusCode: status, code, details: {}, requestId: "req-1" }, status);

export function job(over: Partial<Job> = {}): Job {
  return {
    id: "j1", batch_id: "b1", batch_name: "Crossing batch", task_id: "t1", source_asset_id: "clip-1", start_frame: 0, stop_frame: 299,
    assignee_user_id: "u1", portal_state: "in_progress", cvat_stage: "annotation", cvat_state: "in progress", revision: 3,
    sync_age_s: 30, due_date: null, priority: 0, ...over,
  };
}

export function renderWith(ui: ReactElement, handler: Handler, opts: { navigate?: (u: string) => void; staleAfterS?: number } = {}) {
  const calls: { url: string; init: RequestInit }[] = [];
  const doFetch = (async (url: string, init: RequestInit = {}) => {
    calls.push({ url, init });
    const r = handler(url, init);
    return r === "pending" ? new Promise<Response>(() => {}) : r;
  }) as unknown as typeof fetch;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={client}>
      <LabelingProvider api={createApi("/bff/labeling", doFetch)} navigate={opts.navigate ?? (() => {})} staleAfterS={opts.staleAfterS}>
        {ui}
      </LabelingProvider>
    </QueryClientProvider>
  );
  return { ...utils, calls, client };
}

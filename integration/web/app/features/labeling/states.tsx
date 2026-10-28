import type { ReactNode } from "react";

import { ApiError, useLabeling } from "./api";

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" className="space-y-2" data-testid="loading">
      <span className="sr-only">{label}…</span>
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-10 animate-pulse rounded-md bg-muted" />
      ))}
    </div>
  );
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="rounded-md border border-dashed border-border p-8 text-center" data-testid="empty">
      <p className="font-medium">{title}</p>
      {hint ? <p className="mt-1 text-sm text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

export function describeError(e: unknown): { message: string; sessionExpired: boolean; requestId?: string } {
  if (e instanceof ApiError) {
    if (e.status === 401) return { message: "Your session has expired.", sessionExpired: true, requestId: e.requestId };
    if (e.code === "not_a_member") return { message: "You do not have access to labeling.", sessionExpired: false, requestId: e.requestId };
    if (e.status === 403) return { message: "You are not allowed to do that.", sessionExpired: false, requestId: e.requestId };
    return { message: e.message, sessionExpired: false, requestId: e.requestId };
  }
  return { message: "Something went wrong. Check your connection and try again.", sessionExpired: false };
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { loginUrl } = useLabeling();
  const d = describeError(error);
  return (
    <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-4 text-red-900" data-testid="error">
      <p className="font-medium">{d.message}</p>
      {d.requestId ? <p className="mt-1 text-xs">Reference {d.requestId}</p> : null}
      <div className="mt-3 flex gap-2">
        {d.sessionExpired ? (
          <a className="rounded-md bg-red-900 px-3 py-1.5 text-sm text-white" href={loginUrl}>
            Sign in again
          </a>
        ) : onRetry ? (
          <button className="rounded-md border border-red-900 px-3 py-1.5 text-sm" onClick={onRetry}>
            Try again
          </button>
        ) : null}
      </div>
    </div>
  );
}

/** Failed refresh while earlier data is on screen: keep the data, say it may be old. */
export function StaleBanner({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const d = describeError(error);
  return (
    <div role="status" className="mb-3 flex items-center justify-between rounded-md bg-amber-100 px-3 py-2 text-sm text-amber-950" data-testid="stale-banner">
      <span>Showing the last data we received. {d.message}</span>
      {onRetry ? (
        <button className="underline" onClick={onRetry}>
          Refresh
        </button>
      ) : null}
    </div>
  );
}

interface QueryLike<T> {
  status: "pending" | "error" | "success";
  data: T | undefined;
  error: unknown;
  isRefetchError?: boolean;
  refetch: () => unknown;
}

export function Async<T>({
  query,
  isEmpty,
  empty,
  children,
  label,
}: {
  query: QueryLike<T>;
  isEmpty?: (d: T) => boolean;
  empty: ReactNode;
  children: (d: T) => ReactNode;
  label?: string;
}) {
  if (query.data !== undefined) {
    return (
      <>
        {query.status === "error" ? <StaleBanner error={query.error} onRetry={() => void query.refetch()} /> : null}
        {isEmpty?.(query.data) ? empty : children(query.data)}
      </>
    );
  }
  if (query.status === "error") return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  return <Loading label={label} />;
}

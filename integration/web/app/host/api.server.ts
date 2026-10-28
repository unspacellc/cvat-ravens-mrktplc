import { env } from "./env.server";

export async function apiFetch(path: string, token: string, tenantId?: string, init: RequestInit = {}): Promise<Response> {
  return fetch(`${env.apiUrl}/api/v1/labeling${path}`, {
    ...init,
    headers: { authorization: `Bearer ${token}`, ...(tenantId ? { "x-labeling-tenant": tenantId } : {}), ...init.headers },
  });
}

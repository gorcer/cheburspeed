import { browser } from "$app/environment";

const apiBase = (import.meta.env.PUBLIC_API_BASE ?? "/api") as string;

export interface AdminMetric {
  id: number;
  client_ip: string;
  client_isp: string | null;
  client_lat: number | null;
  client_lng: number | null;
  server_id: number;
  server_alias: string;
  region_code: string;
  region_title: string;
  ip: string | null;
  metric_alias: string;
  value: number | null;
  created_at: string;
}

export interface AdminMetricList {
  items: AdminMetric[];
  limit: number;
  offset: number;
  total: number;
}

export interface AdminMetricPayload {
  client_ip: string;
  client_isp?: string | null;
  client_lat?: number | null;
  client_lng?: number | null;
  server_id: number;
  ip?: string | null;
  metric_alias: string;
  value?: number | null;
}

export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, body: unknown, message: string) {
    super(message);
    this.status = status;
    this.body = body;
    this.name = "ApiError";
  }
}

const TOKEN_STORAGE_KEY = "xspeed:admin-token";

function getStoredToken(): string | null {
  if (!browser) return null;
  return window.localStorage.getItem(TOKEN_STORAGE_KEY);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  if (init?.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const token = getStoredToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`${apiBase}${path}`, { ...init, headers });

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = await response.text().catch(() => null);
    }
    const detail =
      body && typeof body === "object" && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : `${response.status} ${response.statusText}`;
    throw new ApiError(response.status, body, detail);
  }

  return (await response.json()) as T;
}

export interface ListAdminMetricsParams {
  limit?: number;
  offset?: number;
}

export function listAdminMetrics(
  params: ListAdminMetricsParams = {},
): Promise<AdminMetricList> {
  const search = new URLSearchParams();
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  if (params.offset !== undefined) search.set("offset", String(params.offset));
  const query = search.toString();
  return request<AdminMetricList>(
    `/v1/admin/metrics${query ? `?${query}` : ""}`,
  );
}

export function createAdminMetric(
  payload: AdminMetricPayload,
): Promise<AdminMetric> {
  return request<AdminMetric>("/v1/admin/metrics", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getAdminMetric(id: number): Promise<AdminMetric> {
  return request<AdminMetric>(`/v1/admin/metrics/${id}`);
}

export const isClient = browser;

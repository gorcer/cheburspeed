import { browser } from "$app/environment";

const apiBase = (import.meta.env.PUBLIC_API_BASE ?? "/api") as string;

export type TaskStatus = "pending" | "running" | "completed" | "failed";

export interface LoginResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
}

export interface AdminProbe {
  id: number;
  alias: string;
  token: string;
  region_id: number;
  region_code: string;
  region_title: string;
  ip: string | null;
  is_public: boolean;
  last_seen_at: string | null;
  created_at: string;
}

export interface AdminProbeList {
  items: AdminProbe[];
  limit: number;
  offset: number;
  total: number;
}

export interface AdminProbePayload {
  alias?: string;
  region_id: number;
  is_public?: boolean;
  token?: string;
}

export interface AdminProbeUpdate {
  alias?: string;
  region_id?: number;
  is_public?: boolean;
  token?: string;
}

export interface AdminTaskSummary {
  id: number;
  link: string;
  status: TaskStatus;
  probe_tokens: string[] | null;
  destination_id: number | null;
  destination_name: string | null;
  destination_url: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  result_count: number;
  error_count: number;
}

export interface AdminTaskList {
  items: AdminTaskSummary[];
  limit: number;
  offset: number;
  total: number;
}

export interface AdminTaskResult {
  id: number;
  task_id: number;
  probe_id: number;
  probe_alias: string;
  region_id: number;
  region_code: string;
  region_title: string;
  speed_mbps: number | null;
  error: string | null;
  created_at: string;
}

export interface AdminTaskDetail {
  id: number;
  link: string;
  status: TaskStatus;
  probe_tokens: string[] | null;
  assigned_to: string | null;
  destination_id: number | null;
  destination_name: string | null;
  destination_url: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  results: AdminTaskResult[];
}

export interface AdminStatsRegion {
  region_id: number;
  region_code: string;
  region_title: string;
  median_mbps: number | null;
  sample_count: number;
}

export interface AdminStatsBucket {
  bucket: string;
  min_mbps: number;
  max_mbps: number | null;
  count: number;
}

export interface AdminStatsTotals {
  regions: number;
  probes: number;
  tasks: number;
  results_24h: number;
  results_7d: number;
}

export interface AdminStats {
  median_per_region_7d: AdminStatsRegion[];
  histogram: AdminStatsBucket[];
  archive_lag_seconds: number | null;
  totals: AdminStatsTotals;
  generated_at: string;
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

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export function adminLogin(
  email: string,
  password: string,
): Promise<LoginResponse> {
  return request<LoginResponse>("/v1/admin/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function listAdminProbes(
  params: { limit?: number; offset?: number } = {},
): Promise<AdminProbeList> {
  const search = new URLSearchParams();
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  if (params.offset !== undefined) search.set("offset", String(params.offset));
  const query = search.toString();
  return request<AdminProbeList>(`/v1/admin/probes${query ? `?${query}` : ""}`);
}

export function createAdminProbe(
  payload: AdminProbePayload,
): Promise<AdminProbe> {
  return request<AdminProbe>("/v1/admin/probes", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateAdminProbe(
  id: number,
  payload: AdminProbeUpdate,
): Promise<AdminProbe> {
  return request<AdminProbe>(`/v1/admin/probes/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function deleteAdminProbe(id: number): Promise<void> {
  await request<void>(`/v1/admin/probes/${id}`, { method: "DELETE" });
}

export interface ListAdminTasksParams {
  status?: TaskStatus;
  from?: string;
  to?: string;
  region?: string;
  limit?: number;
  offset?: number;
}

export function listAdminTasks(
  params: ListAdminTasksParams = {},
): Promise<AdminTaskList> {
  const search = new URLSearchParams();
  if (params.status) search.set("status", params.status);
  if (params.from) search.set("from", params.from);
  if (params.to) search.set("to", params.to);
  if (params.region) search.set("region", params.region);
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  if (params.offset !== undefined) search.set("offset", String(params.offset));
  const query = search.toString();
  return request<AdminTaskList>(`/v1/admin/tasks${query ? `?${query}` : ""}`);
}

export function getAdminTask(id: number): Promise<AdminTaskDetail> {
  return request<AdminTaskDetail>(`/v1/admin/tasks/${id}`);
}

export function getAdminStats(): Promise<AdminStats> {
  return request<AdminStats>("/v1/admin/stats");
}

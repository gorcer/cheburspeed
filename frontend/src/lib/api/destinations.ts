import { browser } from "$app/environment";

const apiBase = (import.meta.env.PUBLIC_API_BASE ?? "/api") as string;

export interface Destination {
  id: number;
  name: string;
  url: string;
}

export interface DestinationList {
  items: Destination[];
}

export interface AdminDestination {
  id: number;
  name: string;
  url: string;
  created_at: string;
}

export interface AdminDestinationList {
  items: AdminDestination[];
  limit: number;
  offset: number;
  total: number;
}

export interface AdminDestinationPayload {
  name: string;
  url: string;
}

export interface AdminDestinationUpdate {
  name?: string;
  url?: string;
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

export function listDestinations(): Promise<DestinationList> {
  return request<DestinationList>("/v1/destinations");
}

export interface ListAdminDestinationsParams {
  limit?: number;
  offset?: number;
}

export function listAdminDestinations(
  params: ListAdminDestinationsParams = {},
): Promise<AdminDestinationList> {
  const search = new URLSearchParams();
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  if (params.offset !== undefined) search.set("offset", String(params.offset));
  const query = search.toString();
  return request<AdminDestinationList>(
    `/v1/admin/destinations${query ? `?${query}` : ""}`,
  );
}

export function createAdminDestination(
  payload: AdminDestinationPayload,
): Promise<AdminDestination> {
  return request<AdminDestination>("/v1/admin/destinations", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateAdminDestination(
  id: number,
  payload: AdminDestinationUpdate,
): Promise<AdminDestination> {
  return request<AdminDestination>(`/v1/admin/destinations/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function deleteAdminDestination(id: number): Promise<void> {
  await request<void>(`/v1/admin/destinations/${id}`, { method: "DELETE" });
}

export const isClient = browser;

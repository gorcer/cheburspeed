import { browser } from '$app/environment';

const apiBase = (import.meta.env.PUBLIC_API_BASE ?? '/api') as string;

export interface Region {
  id: number;
  code: string;
  title: string;
  lat: number | null;
  lon: number | null;
  probe_count: number;
  last_median_mbps: number | null;
}

export interface RegionList {
  items: Region[];
}

export interface OnlineRegion {
  online: number;
  median_mbps: number | null;
  last_speedtest_at: string | null;
}

export interface OnlineProbesResponse {
  regions: Record<string, OnlineRegion>;
}

export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, body: unknown, message: string) {
    super(message);
    this.status = status;
    this.body = body;
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = await response.text().catch(() => null);
    }
    const detail =
      body && typeof body === 'object' && 'detail' in body
        ? String((body as { detail: unknown }).detail)
        : `${response.status} ${response.statusText}`;
    throw new ApiError(response.status, body, detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export function listRegions(): Promise<RegionList> {
  return request<RegionList>('/v1/regions');
}

export function getOnlineProbes(): Promise<OnlineProbesResponse> {
  return request<OnlineProbesResponse>('/v1/probes/online');
}

export const isClient = browser;
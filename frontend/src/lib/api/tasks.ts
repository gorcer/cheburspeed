import { browser } from '$app/environment';

const apiBase = (import.meta.env.PUBLIC_API_BASE ?? '/api') as string;

export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed';

export interface TaskResult {
  id: number;
  probe_id: number;
  region_id: number;
  speed_mbps: number | null;
  error: string | null;
  created_at: string;
}

export interface Task {
  id: number;
  link: string;
  status: TaskStatus;
  probe_tokens: string[] | null;
  created_at: string;
  completed_at: string | null;
  results: TaskResult[];
}

export interface TaskSummary {
  id: number;
  link: string;
  status: TaskStatus;
  created_at: string;
  completed_at: string | null;
}

export interface TaskList {
  items: TaskSummary[];
  limit: number;
  offset: number;
  total: number;
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

export function createTask(link: string, probeTokens?: string[]): Promise<TaskSummary> {
  return request<TaskSummary>('/v1/tasks', {
    method: 'POST',
    body: JSON.stringify({ link, probe_tokens: probeTokens }),
  });
}

export function getTask(taskId: number): Promise<Task> {
  return request<Task>(`/v1/tasks/${taskId}`);
}

export interface ListTasksParams {
  limit?: number;
  offset?: number;
}

export function listTasks(params: ListTasksParams = {}): Promise<TaskList> {
  const search = new URLSearchParams();
  if (params.limit !== undefined) search.set('limit', String(params.limit));
  if (params.offset !== undefined) search.set('offset', String(params.offset));
  const query = search.toString();
  return request<TaskList>(`/v1/tasks${query ? `?${query}` : ''}`);
}

export const isClient = browser;
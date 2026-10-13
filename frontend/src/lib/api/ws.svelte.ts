import { browser } from '$app/environment';

const wsBase = (import.meta.env.PUBLIC_WS_BASE ?? '/ws') as string;

export type WsStatus = 'connecting' | 'open' | 'closed';

export interface WsEnvelope<T = unknown> {
  type: string;
  payload?: T;
}

export type WsListener = (envelope: WsEnvelope) => void;

export type FrontendEvent =
  | 'task.created'
  | 'task.updated'
  | 'result.added'
  | 'region.updated';

interface FrontendEventPayloadMap {
  'task.created': { id: number; status: string };
  'task.updated': { id: number; status: string; completed_at?: string | null };
  'result.added': { task_id: number; probe_id: number; region_id: number; speed_mbps: number | null; error?: string | null };
  'region.updated': { code: string; median_mbps: number | null; online: number };
}

class WsClient {
  status = $state<WsStatus>('closed');
  retryIn = $state<number | null>(null);

  private socket: WebSocket | null = null;
  private listeners = new Set<WsListener>();
  private retryAttempt = 0;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private explicitClose = false;
  private countdownTimer: ReturnType<typeof setInterval> | null = null;

  connect() {
    if (!browser) return;
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
      return;
    }

    this.explicitClose = false;
    this.status = 'connecting';
    const url = this.resolveUrl();
    let socket: WebSocket;
    try {
      socket = new WebSocket(url);
    } catch (error) {
      console.warn('[ws] failed to construct WebSocket', error);
      this.scheduleReconnect();
      return;
    }

    this.socket = socket;

    socket.addEventListener('open', () => {
      this.retryAttempt = 0;
      this.status = 'open';
      this.retryIn = null;
      this.stopCountdown();
    });

    socket.addEventListener('message', (event) => {
      try {
        const data = JSON.parse(String(event.data)) as WsEnvelope;
        this.dispatch(data);
      } catch (error) {
        console.warn('[ws] malformed message', error);
      }
    });

    socket.addEventListener('close', () => {
      this.socket = null;
      if (this.explicitClose) {
        this.status = 'closed';
        return;
      }
      this.scheduleReconnect();
    });

    socket.addEventListener('error', () => {
      // Browsers emit close afterwards; nothing to do here.
    });
  }

  disconnect() {
    this.explicitClose = true;
    this.stopCountdown();
    if (this.retryTimer !== null) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
    this.status = 'closed';
    this.retryIn = null;
  }

  on(listener: WsListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onEvent<K extends FrontendEvent>(
    event: K,
    listener: (payload: FrontendEventPayloadMap[K]) => void,
  ): () => void {
    return this.on((envelope) => {
      if (envelope.type === event) {
        listener((envelope.payload ?? {}) as FrontendEventPayloadMap[K]);
      }
    });
  }

  private dispatch(envelope: WsEnvelope) {
    for (const listener of this.listeners) {
      try {
        listener(envelope);
      } catch (error) {
        console.error('[ws] listener threw', error);
      }
    }
  }

  private resolveUrl(): string {
    if (typeof window === 'undefined') return wsBase;
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    if (wsBase.startsWith('ws://') || wsBase.startsWith('wss://')) {
      return wsBase;
    }
    const path = wsBase.startsWith('/') ? wsBase : `/${wsBase}`;
    return `${protocol}://${window.location.host}${path}`;
  }

  private scheduleReconnect() {
    this.status = 'closed';
    this.retryAttempt += 1;
    const delayMs = Math.min(30_000, 1_000 * 2 ** Math.min(5, this.retryAttempt - 1));
    this.retryIn = Math.ceil(delayMs / 1000);
    this.startCountdown();

    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      this.retryIn = null;
      this.stopCountdown();
      this.connect();
    }, delayMs);
  }

  private startCountdown() {
    if (this.countdownTimer !== null) return;
    this.countdownTimer = setInterval(() => {
      if (this.retryIn === null) {
        this.stopCountdown();
        return;
      }
      this.retryIn = Math.max(0, this.retryIn - 1);
      if (this.retryIn === 0) this.stopCountdown();
    }, 1000);
  }

  private stopCountdown() {
    if (this.countdownTimer !== null) {
      clearInterval(this.countdownTimer);
      this.countdownTimer = null;
    }
  }
}

export const ws = new WsClient();
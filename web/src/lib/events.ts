// Single shared EventSource for GET /api/events (named SSE events with JSON data).
import { useEffect, useRef, useSyncExternalStore } from 'react';
import type { HubEventMap } from '../api/types';

type EventName = keyof HubEventMap;
type Handler<K extends EventName> = (data: HubEventMap[K]) => void;

const NAMED: EventName[] = ['download', 'torrent', 'assistant', 'library', 'batch', 'import', 'search_history', 'ping'];

class HubEvents {
  private es: EventSource | null = null;
  private listeners = new Map<string, Set<(data: unknown) => void>>();
  private stateListeners = new Set<() => void>();
  private everOpened = false;
  private retry = 0;
  private timer: number | undefined;
  private running = false;
  connected = false;

  start() {
    if (this.running) return;
    this.running = true;
    this.everOpened = false;
    this.connect();
    document.addEventListener('visibilitychange', this.onWake);
    window.addEventListener('online', this.onWake);
  }

  stop() {
    this.running = false;
    window.clearTimeout(this.timer);
    this.es?.close();
    this.es = null;
    this.setConnected(false);
    document.removeEventListener('visibilitychange', this.onWake);
    window.removeEventListener('online', this.onWake);
  }

  on<K extends EventName>(name: K, fn: Handler<K>): () => void {
    let set = this.listeners.get(name);
    if (!set) this.listeners.set(name, (set = new Set()));
    const wrapped = fn as (data: unknown) => void;
    set.add(wrapped);
    return () => set!.delete(wrapped);
  }

  subscribeState = (fn: () => void) => {
    this.stateListeners.add(fn);
    return () => this.stateListeners.delete(fn);
  };

  private onWake = () => {
    if (!this.running || document.visibilityState !== 'visible') return;
    // Android freezes background pages: the stream may be silently dead after resume.
    if (!this.es || this.es.readyState === EventSource.CLOSED) {
      window.clearTimeout(this.timer);
      this.retry = 0;
      this.connect();
    }
  };

  private setConnected(v: boolean) {
    if (this.connected === v) return;
    this.connected = v;
    this.stateListeners.forEach((fn) => fn());
  }

  private emit(name: string, data: unknown) {
    this.listeners.get(name)?.forEach((fn) => {
      try {
        fn(data);
      } catch (e) {
        console.warn('event handler failed', e);
      }
    });
  }

  private parseAndEmit(name: string, raw: string) {
    let data: unknown = {};
    if (raw) {
      try {
        data = JSON.parse(raw);
      } catch {
        return;
      }
    }
    this.emit(name, data);
  }

  private connect() {
    this.es?.close();
    const es = new EventSource('/api/events', { withCredentials: true });
    this.es = es;
    es.onopen = () => {
      this.retry = 0;
      this.setConnected(true);
      if (this.everOpened) this.emit('reconnect', {});
      this.everOpened = true;
    };
    for (const name of NAMED) {
      es.addEventListener(name, (ev) => this.parseAndEmit(name, (ev as MessageEvent<string>).data));
    }
    // Unnamed messages: accept the envelope {"event": "...", "data": {...}} as well.
    es.onmessage = (ev: MessageEvent<string>) => {
      try {
        const msg = JSON.parse(ev.data) as { event?: unknown; data?: unknown };
        if (typeof msg.event === 'string') this.emit(msg.event, msg.data ?? {});
      } catch {
        /* ignore */
      }
    };
    es.onerror = () => {
      this.setConnected(false);
      if (es.readyState === EventSource.CLOSED && this.running) {
        // The browser gave up (HTTP error / 401): reconnect ourselves with backoff.
        this.es = null;
        const delay = Math.min(30000, 1000 * 2 ** this.retry++);
        this.timer = window.setTimeout(() => this.running && this.connect(), delay);
      }
    };
  }
}

export const hubEvents = new HubEvents();

/** Subscribe to a hub event for the lifetime of the component (handler may change freely). */
export function useHubEvent<K extends EventName>(name: K, handler: Handler<K>) {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => hubEvents.on(name, (d) => ref.current(d)), [name]);
}

export function useHubConnected(): boolean {
  return useSyncExternalStore(
    hubEvents.subscribeState,
    () => hubEvents.connected,
    () => false,
  );
}

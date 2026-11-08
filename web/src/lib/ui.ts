// Global toasts and confirm dialogs (module-level stores, rendered by <Overlays/>).
import { useSyncExternalStore } from 'react';
import { errorMessage } from '../api/client';

type Listener = () => void;

function createStore<T>(initial: T) {
  let state = initial;
  const listeners = new Set<Listener>();
  return {
    get: () => state,
    set(next: T) {
      state = next;
      listeners.forEach((l) => l());
    },
    subscribe(l: Listener) {
      listeners.add(l);
      return () => listeners.delete(l);
    },
  };
}

// --- toasts ---------------------------------------------------------------------------------

export interface Toast {
  id: number;
  text: string;
  kind: 'info' | 'success' | 'error';
  /** a button: goes to `to`, or runs `run` (an undo) */
  link?: { label: string; to?: string; run?: () => void };
}

const toastStore = createStore<Toast[]>([]);
let toastSeq = 0;

export function toast(text: string, kind: Toast['kind'] = 'info', link?: Toast['link']) {
  const t: Toast = { id: ++toastSeq, text, kind, link };
  toastStore.set([...toastStore.get().slice(-2), t]);
  window.setTimeout(() => dismissToast(t.id), kind === 'error' ? 6000 : 3500);
}

export function dismissToast(id: number) {
  toastStore.set(toastStore.get().filter((t) => t.id !== id));
}

export const toastError = (e: unknown) => toast(errorMessage(e), 'error');

export function useToasts() {
  return useSyncExternalStore(toastStore.subscribe, toastStore.get, toastStore.get);
}

// --- confirm dialog ---------------------------------------------------------------------------

export interface DialogState {
  title: string;
  body?: string;
  okLabel: string;
  cancelLabel: string;
  danger: boolean;
  resolve: (ok: boolean) => void;
}

const dialogStore = createStore<DialogState | null>(null);

export function confirmDialog(opts: {
  title: string;
  body?: string;
  okLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
}): Promise<boolean> {
  return new Promise((resolve) => {
    dialogStore.get()?.resolve(false);
    dialogStore.set({
      title: opts.title,
      body: opts.body,
      okLabel: opts.okLabel ?? 'Да',
      cancelLabel: opts.cancelLabel ?? 'Отмена',
      danger: opts.danger ?? false,
      resolve: (ok) => {
        dialogStore.set(null);
        resolve(ok);
      },
    });
  });
}

export function useDialog() {
  return useSyncExternalStore(dialogStore.subscribe, dialogStore.get, dialogStore.get);
}

/** Run an action, toast its error, return undefined on failure. */
export async function attempt<R>(fn: () => Promise<R>, success?: string): Promise<R | undefined> {
  try {
    const r = await fn();
    if (success) toast(success, 'success');
    return r;
  } catch (e) {
    toastError(e);
    return undefined;
  }
}

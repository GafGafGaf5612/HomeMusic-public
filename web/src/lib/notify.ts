// FR-7: assistant notifications and the active downloads badge.
import { api } from '../api/client';
import type { DownloadItemOut } from '../api/types';
import { hubEvents } from './events';
import { useSyncExternalStore } from 'react';
import { DOWNLOAD_ACTIVE } from './format';
import { toast } from './ui';

export type NotifyPermission = NotificationPermission | 'unsupported';

export function notifyPermission(): NotifyPermission {
  return 'Notification' in window ? Notification.permission : 'unsupported';
}

export async function requestNotifyPermission(): Promise<NotifyPermission> {
  if (!('Notification' in window)) return 'unsupported';
  try {
    return await Notification.requestPermission();
  } catch {
    return Notification.permission;
  }
}

/**
 * Visible app → in-app toast; hidden app → system notification through the service worker
 * (so a tap focuses the PWA and opens `route`).
 */
export async function notify(title: string, body: string, route: string, tag: string) {
  if (document.visibilityState === 'visible') {
    toast(`${title}: ${body}`, 'info', { label: 'Открыть', to: route });
    return;
  }
  if (notifyPermission() !== 'granted') return;
  const options: NotificationOptions = {
    body,
    tag,
    icon: '/icons/icon-192.png',
    badge: '/icons/badge-96.png',
    data: { url: '/#' + route },
  };
  try {
    const reg = 'serviceWorker' in navigator ? await navigator.serviceWorker.getRegistration() : undefined;
    if (reg) {
      await reg.showNotification(title, options);
      return;
    }
    const n = new Notification(title, options);
    n.onclick = () => {
      window.focus();
      window.location.hash = route;
      n.close();
    };
  } catch (e) {
    console.warn('notification failed', e);
  }
}

// --- active downloads badge ------------------------------------------------------------------

const activeDownloads = new Set<number>();

// Number of active downloads (tab bar badge), kept in sync with the same data.
let activeCount = 0;
const activeListeners = new Set<() => void>();

function recount() {
  if (activeDownloads.size !== activeCount) {
    activeCount = activeDownloads.size;
    activeListeners.forEach((l) => l());
  }
}

export function useActiveDownloads(): number {
  return useSyncExternalStore(
    (l) => {
      activeListeners.add(l);
      return () => activeListeners.delete(l);
    },
    () => activeCount,
    () => 0,
  );
}

function remember(item: DownloadItemOut) {
  if (!item.star && DOWNLOAD_ACTIVE.includes(item.status)) activeDownloads.add(item.id);
  else activeDownloads.delete(item.id);
}

function seed(signal: AbortSignal) {
  void api
    .downloads(undefined, 300, signal)
    .then((items) => {
      if (signal.aborted) return;
      activeDownloads.clear();
      for (const it of items) remember(it);
      recount();
    })
    .catch(() => undefined);
}

function onDownload(item: DownloadItemOut) {
  remember(item);
  recount();
}

let stopFns: Array<() => void> = [];

export function startNotificationWatcher() {
  stopNotificationWatcher();
  const controller = new AbortController();
  const refresh = () => seed(controller.signal);
  let pendingSeed: number | undefined;
  refresh();
  stopFns = [
    () => {
      controller.abort();
      window.clearTimeout(pendingSeed);
    },
    hubEvents.on('download', onDownload),
    hubEvents.on('reconnect', refresh),
    // A new batch was queued: count its items before they start.
    hubEvents.on('batch', () => {
      pendingSeed ??= window.setTimeout(() => {
        pendingSeed = undefined;
        refresh();
      }, 800);
    }),
    hubEvents.on('assistant', (d) => {
      if (d.status === 'awaiting_confirmation') {
        void notify('Ассистент нашёл треки', 'Проверьте список и подтвердите', `/assistant/${d.id}`, `as-${d.id}`);
      } else if (d.status === 'failed') {
        void notify('Ассистент', 'Задача завершилась с ошибкой', `/assistant/${d.id}`, `as-${d.id}`);
      }
    }),
  ];
}

export function stopNotificationWatcher() {
  stopFns.forEach((f) => f());
  stopFns = [];
  activeDownloads.clear();
  recount();
}

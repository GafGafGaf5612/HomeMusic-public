import { useEffect, useSyncExternalStore } from 'react';
import { api } from '../api/client';
import type { FavoriteSubject, FavoriteTarget, FavoriteType } from '../api/types';
import { typedFavoritesSupported } from './capabilities';
import { hubEvents } from './events';
import { HUB_DOWNLOAD_EVENT } from './listen';
import { newEventId, playEvent, routeContext } from './plays';
import { toast, toastError } from './ui';

export interface LikeTarget {
  targetType?: FavoriteType;
  songId?: string | null;
  ndId?: string | null;
  ref?: string | null;
  data?: Record<string, unknown>;
}

type Decision = { liked: boolean; aliases: string[]; eventId?: string; acknowledgedAt?: number };
let snapshot = new Map<string, boolean>();
let own = new Map<string, boolean>();
let linkedAliases = new Map<string, string[]>();
const local = new Map<string, Decision>();
const pendingRefs = new Set<string>();
const refSongs = new Map<string, string>();
const busy = new Set<string>();
// A failed/ambiguous delivery retries the exact same operation, never a new journal decision.
const retries = new Map<string, { eventId: string; ts: string; liked: boolean }>();
let version = 0;
let loadedAt = 0;
let loading: Promise<void> | null = null;
let refreshAgain = false;
let acknowledged = 0;
let epoch = 0;
const subscribers = new Set<() => void>();
const REFRESH_MS = 30_000;

const typeOf = (t: LikeTarget) => t.targetType ?? 'track';
const typedKey = (type: FavoriteType, alias: string) => `${type}:${alias}`;
function aliases(t: LikeTarget): string[] {
  const id = t.ndId ?? t.songId ?? (typeOf(t) === 'track' && t.ref ? refSongs.get(t.ref) : undefined);
  const direct = [t.ref ? `ref:${t.ref}` : '', id ? `nd:${id}` : ''].filter(Boolean).map((a) => typedKey(typeOf(t), a));
  return [...new Set(direct.flatMap((key) => [key, ...(linkedAliases.get(key) ?? [])]))];
}
function emit() {
  version++;
  for (const fn of subscribers) fn();
}
function subscribe(fn: () => void) {
  subscribers.add(fn);
  return () => { subscribers.delete(fn); };
}
function subjectAliases(s: FavoriteSubject): string[] {
  return [...new Set([...(s.aliases ?? []), ...(s.ref ? [`ref:${s.ref}`] : []), ...(s.nd_id ? [`nd:${s.nd_id}`] : [])])]
    .map((a) => typedKey(s.target_type, a));
}

/** Own decisions (including unlike) override delayed Navidrome/import snapshots. */
export function refreshStars(force = false): Promise<void> {
  if (loading) { if (force) refreshAgain = true; return loading; }
  if (!force && Date.now() - loadedAt < REFRESH_MS) return Promise.resolve();
  const generation = epoch;
  const acknowledgedBeforeRequest = acknowledged;
  loading = api.starred(AbortSignal.timeout(10_000)).then((r) => {
    if (generation !== epoch) return;
    const next = new Map<string, boolean>();
    for (const [type, ids] of [['track', r.song_ids], ['album', r.album_ids], ['artist', r.artist_ids ?? []]] as const) {
      for (const id of ids) next.set(typedKey(type, `nd:${id}`), true);
    }
    for (const ref of r.refs ?? []) next.set(typedKey('track', `ref:${ref}`), true);
    const decisions = new Map<string, boolean>();
    const links = new Map<string, string[]>();
    for (const s of r.subjects ?? []) {
      const keys = subjectAliases(s);
      for (const key of keys) links.set(key, keys);
      for (const key of keys) (s.provenance === 'own' ? decisions : next).set(key, s.liked);
      if (s.provenance === 'own') {
        for (const [key, value] of local) {
          if (!busy.has(key) && value.aliases.some((a) => keys.includes(a)) &&
            (value.eventId === s.event_id || (value.acknowledgedAt !== undefined && value.acknowledgedAt <= acknowledgedBeforeRequest))) local.delete(key);
        }
      }
    }
    snapshot = next;
    own = decisions;
    linkedAliases = links;
    loadedAt = Date.now();
    emit();
  }, () => { /* offline or logged out: keep the last known state */ }).finally(() => {
    loading = null;
    if (refreshAgain) { refreshAgain = false; void refreshStars(true); }
  });
  return loading;
}

export function isLiked(t: LikeTarget): boolean {
  const keys = aliases(t);
  for (const decision of [...local.values()].reverse()) {
    if (decision.aliases.some((a) => keys.includes(a))) return decision.liked;
  }
  for (const key of keys) if (own.has(key)) return own.get(key)!;
  return keys.some((key) => snapshot.get(key)) || (typeOf(t) === 'track' && !!t.ref && pendingRefs.has(t.ref));
}
export function useLiked(t: LikeTarget): boolean {
  useEffect(() => { void refreshStars(); }, []);
  useSyncExternalStore(subscribe, () => version);
  return isLiked(t);
}

function apiTarget(t: LikeTarget): FavoriteTarget {
  const type = typeOf(t);
  const id = t.ndId ?? t.songId ?? (type === 'track' && t.ref ? refSongs.get(t.ref) : undefined);
  return {
    target_type: type, ref: t.ref, target_data: t.data,
    ...(id ? { [type === 'track' ? 'nd_song_id' : type === 'album' ? 'nd_album_id' : 'nd_artist_id']: id } : {}),
  };
}

/** Track ♥ may download one song; album/artist ♥ only records the typed favorite. */
export async function toggleLike(t: LikeTarget): Promise<void> {
  const keys = aliases(t);
  const key = keys[0];
  if (!key || keys.some((a) => busy.has(a))) return;
  for (const a of keys) busy.add(a);
  const generation = epoch;
  const liked = !isLiked(t);
  const previous = local.get(key);
  const retry = retries.get(key);
  const operation = retry?.liked === liked ? retry : { eventId: newEventId(), ts: new Date().toISOString(), liked };
  retries.set(key, operation);
  try {
    const typed = await typedFavoritesSupported();
    if (typed === null) throw new Error('Не удалось проверить соединение с Хабом. Повторите попытку.');
    if (!typed && typeOf(t) !== 'track') throw new Error('Обновите Хаб, чтобы отмечать альбомы и исполнителей');
    if (generation !== epoch) return;
    local.set(key, { liked, aliases: keys, eventId: operation.eventId });
    emit();
    const target = apiTarget(t);
    const context = routeContext();
    const signal = AbortSignal.timeout(15_000);
    if (typed) {
      const result = await api.star({ targets: [target], unstar: !liked, event_id: operation.eventId, ts: operation.ts, client: 'pwa', context }, signal);
      if (!result.event_ids?.[0]) throw new Error('Хаб не подтвердил изменение отметки');
      if (generation !== epoch) return;
      local.set(key, { liked, aliases: keys, eventId: result.event_ids[0], acknowledgedAt: ++acknowledged });
      // Same journal id and target: the wave/batch delivery is a duplicate, not another like.
      if (typeOf(t) === 'track') playEvent(liked ? 'like' : 'unlike', target, { event_id: result.event_ids[0], ts: operation.ts, context });
    } else {
      if (target.nd_song_id) await api.star({ song_ids: [target.nd_song_id], unstar: !liked }, signal);
      else if (!liked && t.ref) await api.star({ refs: [t.ref], unstar: true }, signal);
      else if (t.ref) await api.download({ track_refs: [t.ref], star: true }, signal);
      if (generation !== epoch) return;
      playEvent(liked ? 'like' : 'unlike', target, { event_id: operation.eventId, ts: operation.ts, context });
    }
    retries.delete(key);
    if (!liked && t.ref) pendingRefs.delete(t.ref);
    window.dispatchEvent(new CustomEvent('hm:hub-favorite', { detail: { ref: t.ref, star: liked } }));
    toast(liked ? 'Добавлено в понравившиеся' : 'Убрано из понравившихся', 'success');
  } catch (e) {
    if (generation !== epoch) return;
    if (previous) local.set(key, previous);
    else local.delete(key);
    emit();
    toastError(e);
  } finally {
    for (const a of keys) busy.delete(a);
    if (generation === epoch) { emit(); void refreshStars(true); }
  }
}

export function markLikePending(ref: string) {
  const key = typedKey('track', `ref:${ref}`);
  if (own.get(key) === false || [...local.values()].some((d) => d.aliases.includes(key) && !d.liked) || pendingRefs.has(ref)) return;
  pendingRefs.add(ref);
  emit();
}
window.addEventListener(HUB_DOWNLOAD_EVENT, (e) => {
  const d = (e as CustomEvent<{ ref?: string; star?: boolean }>).detail;
  if (d?.ref && d.star) markLikePending(d.ref);
});
window.addEventListener('hm:hub-favorite', (e) => {
  const d = (e as CustomEvent<{ ref?: string; star?: boolean }>).detail;
  if (d?.ref && !d.star) pendingRefs.delete(d.ref);
  emit();
  void refreshStars(true);
});
hubEvents.on('download', (d) => {
  const ref = d.track_ref;
  if (!ref) return;
  if (d.status === 'done' && d.nd_song_id) refSongs.set(ref, d.nd_song_id);
  if (['done', 'failed', 'cancelled', 'needs_review'].includes(d.status)) {
    pendingRefs.delete(ref);
    // Terminal failures are rolled back by the server; never retain a phantom local like.
    if (d.status !== 'done') for (const [key, value] of local) {
      if (value.liked && value.aliases.includes(typedKey('track', `ref:${ref}`))) local.delete(key);
    }
    emit();
    void refreshStars(true);
  }
});
hubEvents.on('library', (e) => {
  if (e.kind === 'starred' || e.kind === 'unstarred') void refreshStars(true);
});
hubEvents.on('reconnect', () => void refreshStars(true));
if (typeof document !== 'undefined') document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') void refreshStars();
});
export function resetStars() {
  epoch++;
  if (loading) refreshAgain = true;
  snapshot = new Map();
  own = new Map();
  linkedAliases.clear();
  local.clear(); pendingRefs.clear(); refSongs.clear(); busy.clear(); retries.clear();
  loadedAt = 0;
  emit();
}

// Admin insight screens: the period and the filters live in the address (a reload by a new build keeps them).
import { ROUTE_EVENT, useRoute } from './router';

export type PeriodKey = '7' | '30' | '90' | 'all';

export const PERIODS: Array<{ value: PeriodKey; label: string }> = [
  { value: '7', label: '7 дн' },
  { value: '30', label: '30 дн' },
  { value: '90', label: '90 дн' },
  { value: 'all', label: 'Всё' },
];

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

/** Local dates `from`/`to` (inclusive) of a period; «Всё» is the API's limit of three years. */
export function periodRange(key: PeriodKey): { from: string; to: string } {
  const to = new Date();
  const from = new Date(to);
  if (key === 'all') {
    from.setFullYear(from.getFullYear() - 3);
    from.setDate(from.getDate() + 1);
  } else {
    from.setDate(from.getDate() - Number(key) + 1);
  }
  return { from: iso(from), to: iso(to) };
}

export function periodOf(q: URLSearchParams): PeriodKey {
  const p = q.get('p');
  return p === '7' || p === '90' || p === 'all' ? p : '30';
}

/** Changes the query of the current route in place: no history entry, no scroll to the top (unlike `navigate`). */
export function setQuery(patch: Record<string, string | null | undefined>) {
  const raw = window.location.hash.replace(/^#/, '') || '/';
  const qi = raw.indexOf('?');
  const path = qi >= 0 ? raw.slice(0, qi) : raw;
  const q = new URLSearchParams(qi >= 0 ? raw.slice(qi + 1) : '');
  for (const [k, v] of Object.entries(patch)) {
    if (v == null || v === '') q.delete(k);
    else q.set(k, v);
  }
  const s = q.toString();
  window.history.replaceState(window.history.state, '', '#' + path + (s ? `?${s}` : ''));
  window.dispatchEvent(new Event(ROUTE_EVENT));
}

/** The query of the current route and its setter. */
export function useQuery(): [URLSearchParams, typeof setQuery] {
  return [useRoute().query, setQuery];
}

/** A track key goes into an address only in the query, encoded (`nd:…`, `dz:…`, `file:…`). */
export const trackLink = (userId: number, key: string) => `/admin/user/${userId}/track?key=${encodeURIComponent(key)}`;

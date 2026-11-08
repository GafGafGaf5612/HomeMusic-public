// A tiny hash router: FastAPI only has to serve index.html, everything after "#" is client side.
import { useMemo, useSyncExternalStore, type AnchorHTMLAttributes, type MouseEvent } from 'react';

/** Fired by `navigate()` (pushState does not fire `hashchange`). */
export const ROUTE_EVENT = 'hm:route';

function subscribe(cb: () => void) {
  window.addEventListener('hashchange', cb);
  window.addEventListener(ROUTE_EVENT, cb);
  return () => {
    window.removeEventListener('hashchange', cb);
    window.removeEventListener(ROUTE_EVENT, cb);
  };
}

const getHash = () => window.location.hash;

export interface Route {
  /** Path without the leading "#", e.g. "/album/dz:123". */
  path: string;
  query: URLSearchParams;
  /** Full hash target ("/search?q=x"), usable as `navigate()` argument. */
  full: string;
}

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#/, '') || '/';
  const qi = raw.indexOf('?');
  const path = (qi >= 0 ? raw.slice(0, qi) : raw) || '/';
  const query = new URLSearchParams(qi >= 0 ? raw.slice(qi + 1) : '');
  return { path, query, full: raw };
}

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, getHash, getHash);
  return useMemo(() => parseHash(hash), [hash]);
}

// history.state.idx counts in-app entries so "back" never leaves the app.
const idx = (): number => {
  const st = window.history.state as { idx?: number } | null;
  return typeof st?.idx === 'number' ? st.idx : 0;
};

export function navigate(to: string, opts: { replace?: boolean } = {}) {
  const target = '#' + to;
  if (opts.replace) {
    window.history.replaceState({ idx: idx() }, '', target);
  } else if (window.location.hash !== target) {
    window.history.pushState({ idx: idx() + 1 }, '', target);
  } else {
    return;
  }
  window.dispatchEvent(new Event(ROUTE_EVENT));
  window.scrollTo(0, 0);
}

/** Go back if we have in-app history, otherwise to `fallback`. */
export function goBack(fallback: string) {
  if (idx() > 0) window.history.back();
  else navigate(fallback, { replace: true });
}

/** Match "/album/:ref" against a path. Returns decoded params or null. */
export function matchPath(pattern: string, path: string): Record<string, string> | null {
  const ps = pattern.split('/').filter(Boolean);
  const xs = path.split('/').filter(Boolean);
  if (ps.length !== xs.length) return null;
  const params: Record<string, string> = {};
  for (let i = 0; i < ps.length; i++) {
    const p = ps[i];
    if (p.startsWith(':')) {
      try {
        params[p.slice(1)] = decodeURIComponent(xs[i]);
      } catch {
        params[p.slice(1)] = xs[i];
      }
    } else if (p !== xs[i]) {
      return null;
    }
  }
  return params;
}

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> & { to: string; replace?: boolean };

export function Link({ to, replace, onClick, ...rest }: LinkProps) {
  const handle = (e: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(e);
    if (e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return;
    e.preventDefault();
    navigate(to, { replace });
  };
  return <a href={'#' + to} onClick={handle} {...rest} />;
}

export const enc = encodeURIComponent;

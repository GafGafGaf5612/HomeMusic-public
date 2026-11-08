import { useCallback, useEffect, useRef, useState, type DependencyList } from 'react';
import { errorMessage, isAbort } from '../api/client';

export interface AsyncState<T> {
  data: T | undefined;
  error: string | undefined;
  loading: boolean;
  /** Refetch; `silent` keeps the current data on screen (no spinner). */
  reload: (silent?: boolean) => void;
  setData: (fn: T | ((prev: T | undefined) => T | undefined)) => void;
}

/** Runs `fn` whenever deps change; aborts the previous run. */
export function useAsync<T>(fn: (signal: AbortSignal) => Promise<T>, deps: DependencyList, enabled = true): AsyncState<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState<boolean>(enabled);
  const [nonce, setNonce] = useState(0);
  const silentRef = useRef(false);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    const ctl = new AbortController();
    if (!silentRef.current) setLoading(true);
    silentRef.current = false;
    setError(undefined);
    fnRef
      .current(ctl.signal)
      .then((d) => {
        if (!ctl.signal.aborted) {
          setData(d);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (ctl.signal.aborted || isAbort(e)) return;
        setError(errorMessage(e));
        setLoading(false);
      });
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce, enabled]);

  const reload = useCallback((silent = false) => {
    silentRef.current = silent;
    setNonce((n) => n + 1);
  }, []);

  const set = useCallback((v: T | ((prev: T | undefined) => T | undefined)) => {
    setData(v as T | ((prev: T | undefined) => T));
  }, []);

  return { data, error, loading, reload, setData: set };
}

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function useInterval(fn: () => void, ms: number | null) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (ms == null) return;
    const t = window.setInterval(() => ref.current(), ms);
    return () => window.clearInterval(t);
  }, [ms]);
}

const scrollX = new Map<string, number>();

/** Ref for a horizontal row: going to an album and back remounts the row, and the browser only restores
 * the page's vertical scroll. This puts the row back where it was left, per `key`, until a reload. */
export function useRememberScrollX(key: string) {
  return useCallback(
    (el: HTMLElement | null) => {
      if (!el) return;
      const saved = scrollX.get(key);
      if (saved) el.scrollLeft = saved;
      const onScroll = () => scrollX.set(key, el.scrollLeft);
      el.addEventListener('scroll', onScroll, { passive: true });
      return () => el.removeEventListener('scroll', onScroll);
    },
    [key],
  );
}

import { useEffect, useState } from 'react';
import { insightsApi, type TasteModel, type TasteOut, type TasteTrackRow } from '../../api/insights';
import { DivergingBar } from '../../components/charts/Weight';
import { facetColor } from '../../components/charts/palette';
import { Button, ErrorState, Loading, Notice, Segmented } from '../../components/ui';
import { embedderLabel, fmtDec, fmtNum, fmtStamp, plural } from '../../lib/format';
import { useAsync, useDebounced } from '../../lib/hooks';
import { setQuery, trackLink } from '../../lib/insights';
import { attempt } from '../../lib/ui';
import { navigate, useRoute } from '../../lib/router';
import { Block, Chips, Columns, Swatch, trackName } from './common';

export const MODELS: Array<{ value: TasteModel; label: string }> = [
  { value: 'v2', label: 'v2' },
  { value: 'v1', label: 'v1 (локальный)' },
];

export const modelOf = (q: URLSearchParams): TasteModel => (q.get('model') === 'v1' ? 'v1' : 'v2');

const FILTERS = [
  { value: 'all', label: 'Все' },
  { value: 'positive', label: 'Положительные' },
  { value: 'seed', label: 'Сиды' },
  { value: 'negative', label: 'Отрицательные' },
  { value: 'liked', label: '♥' },
  { value: 'disliked', label: '👎' },
  { value: 'downloaded', label: 'Скачанные' },
];

const SORTS = [
  { value: 's', label: 'по весу' },
  { value: 'p', label: 'по P' },
  { value: 'n', label: 'по N' },
  { value: 'last_heard', label: 'по последнему прослушиванию' },
  { value: 'plays', label: 'по прослушиваниям' },
  { value: 'title', label: 'по названию' },
];

const PAGE = 50;

/** facet id → its rank (the color) in the summary's order (by weight). */
export function facetRanks(facets: Array<{ id: string }> | null | undefined): Map<string, number> {
  return new Map((facets ?? []).map((f, i) => [f.id, i]));
}

export function TasteTab({ userId }: { userId: number }) {
  const q = useRoute().query;
  const model = modelOf(q);
  const res = useAsync((s) => insightsApi.taste(userId, model, s), [userId, model]);
  return (
    <>
      <div className="flex items-center gap-2 px-4 pb-2">
        <span className="text-sm text-muted">Модель</span>
        <Segmented<TasteModel> label="Модель" value={model} options={MODELS} onChange={(v) => setQuery({ model: v === 'v2' ? null : v })} />
      </div>
      {res.loading ? <Loading /> : res.error || !res.data ? <ErrorState text={res.error ?? 'Нет данных'} onRetry={() => res.reload()} /> : <Summary d={res.data} userId={userId} />}
      {res.data && <Tracks userId={userId} model={model} ranks={facetRanks(res.data.facets)} />}
    </>
  );
}

function Summary({ d, userId }: { d: TasteOut; userId: number }) {
  const c = d.counts;
  const ranks = facetRanks(d.facets);
  const artists = d.top_artists;
  const pos = artists.filter((a) => a.score > 0).slice(0, 20);
  const neg = artists.filter((a) => a.score < 0);
  return (
    <>
      <div className="px-4 text-sm">
        <p className="text-xs text-muted">посчитано {fmtStamp(d.now)} · {d.timezone}</p>
        {d.model_note && <div className="pt-2"><Notice kind="info">{d.model_note}</Notice></div>}
        <p className="pt-2">
          {d.maturity != null && <>Зрелость {fmtDec(d.maturity)} · </>}опыт E={fmtDec(d.evidence, 1)}
          {d.active_units != null && <> · активных единиц {fmtDec(d.active_units, 1)}</>}
          {d.active_days != null && <> · активных дней {fmtNum(d.active_days)}</>}
        </p>
        <p className="text-muted">
          треков: +{fmtNum(c.positive)} / −{fmtNum(c.negative)} (из {fmtNum(c.total)}, s ≥ 1: {fmtNum(c.strong)}) · ♥ {fmtNum(c.liked)} · скачано {fmtNum(c.downloaded)} · ♥ альбома{' '}
          {fmtNum(c.album_liked)} · 👎 треков {fmtNum(c.explicit_dislikes)}, исполнителей {fmtNum(c.disliked_artists)}
        </p>
      </div>

      <Columns>
        <Block title="Грани вкуса">
          {d.facets == null ? (
            <Notice>Граней нет: embedder {embedderLabel(d.embedder)}.</Notice>
          ) : !d.facets.length ? (
            <p className="text-sm text-muted">Пока не из чего строить грани.</p>
          ) : (
            <ul className="space-y-2">
              {d.facets.map((f, i) => (
                <li key={f.id} className="text-sm">
                  <div className="flex items-center gap-2">
                    <Swatch color={facetColor(i)} />
                    <span className="font-semibold tabular-nums">{Math.round(f.weight * 100)} %</span>
                    <span className="min-w-0 flex-1 truncate">{f.artists.join(', ') || 'без имени'}</span>
                    <span className="shrink-0 text-xs text-muted">{f.size}</span>
                  </div>
                  <p className="truncate pl-4.5 text-xs text-muted">{f.top_members.map((m) => trackName(m)).join(' · ')}</p>
                </li>
              ))}
            </ul>
          )}
        </Block>
        <Block title={`Сиды сегодня (${d.seeds_today.length})`}>
          {d.seeds_today.length ? (
            <ul className="divide-y divide-line/50">
              {d.seeds_today.map((x) => (
                <li key={x.key}>
                  <button type="button" onClick={() => navigate(trackLink(userId, x.key))} className="flex min-h-10 w-full items-center gap-2 text-left text-sm hover:bg-fg/5">
                    <Swatch color={x.facet_id && ranks.has(x.facet_id) ? facetColor(ranks.get(x.facet_id)) : 'var(--color-dim)'} />
                    <span className="min-w-0 flex-1 truncate">{trackName(x)}</span>
                    <span className="shrink-0 tabular-nums">{fmtDec(x.s)}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted">Сидов нет.</p>
          )}
          {d.onboarding_reserve > 0 && (
            <p className="mt-2 text-xs text-muted">
              {d.onboarding_reserve} {plural(d.onboarding_reserve, ['место', 'места', 'мест'])} — онбординг
              {d.onboarding_artists.length ? `: ${d.onboarding_artists.map((a) => a.name).join(', ')}` : ''}
            </p>
          )}
          <p className="mt-1 text-xs text-muted">{d.seeds_note}</p>
        </Block>
      </Columns>

      <Block title="Исполнители" right={<span className="text-xs text-muted">A_artist</span>}>
        {pos.length + neg.length ? (
          <DivergingBar
            rows={[...pos, ...neg].map((a) => ({
              id: a.key,
              label: `${a.name}${a.liked ? ' ♥' : ''}${a.disliked ? ' 👎' : ''}`,
              value: a.score,
              extra: `${a.listens} ${plural(a.listens, ['прослушивание', 'прослушивания', 'прослушиваний'])}`,
            }))}
          />
        ) : (
          <p className="text-sm text-muted">Нет данных</p>
        )}
      </Block>

      {d.limitations.length > 0 && (
        <div className="px-4 pt-3">
          <Notice kind="info">Ограничения расчёта: {d.limitations.join('; ')}</Notice>
        </div>
      )}
    </>
  );
}

function Tracks({ userId, model, ranks }: { userId: number; model: TasteModel; ranks: Map<string, number> }) {
  const q = useRoute().query;
  const filter = q.get('filter') ?? 'all';
  const sort = q.get('sort') ?? 's';
  const [text, setText] = useState(q.get('q') ?? '');
  const needle = useDebounced(text, 300);
  useEffect(() => setQuery({ q: needle || null }), [needle]);
  const [more, setMore] = useState<TasteTrackRow[]>([]);
  const [busy, setBusy] = useState(false);
  const params = { model, filter, sort, q: needle || undefined, limit: PAGE };
  const res = useAsync((s) => insightsApi.tracks(userId, params, s), [userId, model, filter, sort, needle]);
  useEffect(() => setMore([]), [res.data]);
  const rows = [...(res.data?.rows ?? []), ...more];
  const loadMore = async () => {
    setBusy(true);
    const page = await attempt(() => insightsApi.tracks(userId, { ...params, offset: rows.length }));
    setBusy(false);
    if (page) setMore((prev) => [...prev, ...page.rows]);
  };
  const max = Math.max(1e-9, ...rows.map((r) => Math.abs(r.s)));

  return (
    <Block title={`Треки${res.data ? ` (${fmtNum(res.data.total)})` : ''}`}>
      <div className="space-y-2">
        <Chips label="Фильтр треков" value={filter} options={FILTERS} onChange={(v) => setQuery({ filter: v === 'all' ? null : v })} />
        <div className="flex flex-col gap-2 sm:flex-row">
          <input type="search" className="input min-w-0 flex-1" placeholder="Поиск по названию" value={text} onChange={(e) => setText(e.target.value)} />
          <select className="input shrink-0 sm:w-auto" aria-label="Сортировка" value={sort} onChange={(e) => setQuery({ sort: e.target.value === 's' ? null : e.target.value })}>
            {SORTS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
      </div>
      {res.loading && !rows.length ? (
        <Loading />
      ) : res.error ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : !rows.length ? (
        <p className="pt-3 text-sm text-muted">Ничего не нашлось</p>
      ) : (
        <ul className="mt-2 divide-y divide-line/50">
          {rows.map((r) => (
            <TrackRow key={r.key ?? r.ref} r={r} max={max} color={r.facet_id && ranks.has(r.facet_id) ? facetColor(ranks.get(r.facet_id)) : undefined} onClick={() => r.key && navigate(trackLink(userId, r.key))} />
          ))}
        </ul>
      )}
      {res.data && rows.length < res.data.total && (
        <Button small block className="mt-2" busy={busy} onClick={loadMore}>
          Показать ещё
        </Button>
      )}
    </Block>
  );
}

function TrackRow({ r, max, color, onClick }: { r: TasteTrackRow; max: number; color?: string; onClick: () => void }) {
  const flags = [r.liked && '♥', r.disliked && '👎', r.plays && `🔁${r.plays}`, r.early_skips && `⏭${r.early_skips}`, r.downloaded && 'скачан', r.seed_eligible && 'сид'].filter(Boolean);
  const w = (Math.abs(r.s) / max) * 100;
  return (
    <li>
      <button type="button" onClick={onClick} className="flex min-h-12 w-full items-center gap-2 py-1.5 text-left hover:bg-fg/5">
        {color ? <Swatch color={color} /> : <span className="w-2.5 shrink-0" />}
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm">{trackName(r)}</span>
          <span className="block truncate text-xs text-muted">
            P {fmtDec(r.p)} · N {fmtDec(r.n)}
            {flags.length ? ` · ${flags.join(' ')}` : ''}
          </span>
        </span>
        <span className="flex w-24 shrink-0 items-center gap-1.5">
          <span className="h-2 flex-1 overflow-hidden rounded-full bg-raised">
            <span className="block h-full rounded-full" style={{ width: `${w}%`, background: r.s < 0 ? 'var(--color-danger)' : 'var(--color-accent)' }} />
          </span>
          <span className={r.s < 0 ? 'w-10 text-right text-sm tabular-nums text-danger' : 'w-10 text-right text-sm tabular-nums'}>{fmtDec(r.s)}</span>
        </span>
      </button>
    </li>
  );
}

import { useEffect, useState } from 'react';
import { insightsApi, type MatrixOut } from '../../api/insights';
import { WeightCell } from '../../components/charts/Weight';
import { Button, ErrorState, Loading, PageHeader, Segmented } from '../../components/ui';
import { fmtDec, fmtNum } from '../../lib/format';
import { useAsync, useDebounced } from '../../lib/hooks';
import { setQuery, trackLink } from '../../lib/insights';
import { navigate, useRoute } from '../../lib/router';
import { attempt } from '../../lib/ui';
import { Chips, ScrollTable } from './common';

const FILTERS = [
  { value: 'all', label: 'Все' },
  { value: 'shared_liked', label: 'Общие любимые' },
  { value: 'conflict', label: 'Спорные' },
  { value: 'single', label: 'Только у одного' },
  { value: 'disliked', label: 'С 👎' },
];

const PAGE = 100;

type Row = MatrixOut['rows'][number];

/** `/admin/taste` — songs (or artists) × users: everyone's weight by the production taste code. */
export function TasteMatrixPage() {
  const q = useRoute().query;
  const by = q.get('by') === 'artist' ? 'artist' : 'track';
  const filter = q.get('filter') ?? 'all';
  const sort = q.get('sort') ?? 'sum';
  const [text, setText] = useState(q.get('q') ?? '');
  const needle = useDebounced(text, 300);
  useEffect(() => setQuery({ q: needle || null }), [needle]);
  const params = { by, filter, sort, q: needle || undefined, limit: PAGE };
  const res = useAsync((s) => insightsApi.matrix(params, s), [by, filter, sort, needle]);
  const [more, setMore] = useState<Row[]>([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => setMore([]), [res.data]);
  const d = res.data;
  const rows = [...(d?.rows ?? []), ...more];
  const max = Math.max(1e-9, ...rows.flatMap((r) => Object.values(r.cells).map((c) => Math.abs(c.s))));

  const loadMore = async () => {
    setBusy(true);
    const page = await attempt(() => insightsApi.matrix({ ...params, offset: rows.length }));
    setBusy(false);
    if (page) setMore((prev) => [...prev, ...page.rows]);
  };

  return (
    <div className="pb-6">
      <PageHeader title="Веса песен у всех" back="/admin" sub={d ? `модель ${d.model} · ${fmtNum(d.total)} строк` : undefined} />
      <div className="space-y-2 px-4 pb-2">
        <Segmented
          label="Строки"
          value={by}
          options={[{ value: 'track', label: 'Треки' }, { value: 'artist', label: 'Исполнители' }]}
          onChange={(v) => setQuery({ by: v === 'track' ? null : v, sort: null })}
        />
        <Chips label="Фильтр" value={filter} options={FILTERS} onChange={(v) => setQuery({ filter: v === 'all' ? null : v })} />
        <div className="flex flex-col gap-2 sm:flex-row">
          <input type="search" className="input min-w-0 flex-1" placeholder="Поиск" value={text} onChange={(e) => setText(e.target.value)} />
          <select className="input shrink-0 sm:w-auto" aria-label="Сортировка" value={sort} onChange={(e) => setQuery({ sort: e.target.value === 'sum' ? null : e.target.value })}>
            <option value="sum">Σ положительных</option>
            <option value="spread">по разбросу</option>
            <option value="title">по названию</option>
            {d?.users.map((u) => (
              <option key={u.id} value={`user:${u.id}`}>
                по {u.username}
              </option>
            ))}
          </select>
        </div>
      </div>
      {res.loading ? (
        <Loading />
      ) : res.error || !d ? (
        <ErrorState text={res.error ?? 'Нет данных'} onRetry={() => res.reload()} />
      ) : (
        <div className="px-4">
          <div className="rounded-xl bg-surface p-3">
            <ScrollTable<Row>
              rows={rows}
              rowKey={(r) => r.key}
              empty="Ничего не нашлось"
              cols={[
                { label: by === 'artist' ? 'Исполнитель' : 'Трек', cell: (r) => (by === 'artist' ? r.artist : [r.artist, r.title].filter(Boolean).join(' — ')) },
                ...d.users.map((u) => ({
                  label: u.username,
                  align: 'right' as const,
                  cell: (r: Row) => {
                    const c = r.cells[String(u.id)];
                    const cell = (
                      <WeightCell s={c?.s} max={max}>
                        {c?.liked ? ' ♥' : ''}
                        {c?.disliked ? ' 👎' : ''}
                      </WeightCell>
                    );
                    return by === 'track' && c ? (
                      <button type="button" className="block w-full min-w-14" onClick={() => navigate(trackLink(u.id, r.key))} aria-label={`Разложение веса у ${u.username}`}>
                        {cell}
                      </button>
                    ) : (
                      <span className="block min-w-14">{cell}</span>
                    );
                  },
                })),
                { label: 'Σ+', cell: (r) => fmtDec(r.sum_pos), align: 'right' },
              ]}
            />
          </div>
          <p className="mt-2 text-xs text-muted">
            Ячейка — вес s{by === 'artist' ? ' (A_artist)' : ''}: зелёный — плюс, красный — минус, «—» — у пользователя нет веса.
            {by === 'track' ? ' Тап по ячейке — разложение веса у этого пользователя.' : ''} Строки объединены только по проверенным псевдонимам трека.
          </p>
          {rows.length < d.total && (
            <Button small block className="mt-2" busy={busy} onClick={loadMore}>
              Показать ещё
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

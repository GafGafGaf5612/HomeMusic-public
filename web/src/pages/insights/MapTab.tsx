import { useMemo, useState } from 'react';
import { insightsApi, type MapLayer, type MapOut, type MapPoint } from '../../api/insights';
import { ScatterMap, type Anchor, type Dot } from '../../components/charts/ScatterMap';
import { facetColor } from '../../components/charts/palette';
import { Button, ErrorState, Loading, Notice, Segmented, Sheet, cx } from '../../components/ui';
import { fmtDec, fmtStamp, mapReasonLabel, outcomeLabel, seedKindLabel } from '../../lib/format';
import { useAsync } from '../../lib/hooks';
import { setQuery, trackLink } from '../../lib/insights';
import { Link, navigate, useRoute } from '../../lib/router';
import { MODELS, modelOf } from './Taste';
import { Swatch, trackName } from './common';
import type { TasteModel } from '../../api/insights';

type Layout = 'pca' | 'facets';
const LAYER_LABELS: Record<MapLayer, string> = { profile: 'Вкус', mix: 'Микс дня', wave: 'Волна' };
const ALL_LAYERS: MapLayer[] = ['profile', 'mix', 'wave'];

function layersOf(q: URLSearchParams): MapLayer[] {
  const raw = q.get('layers');
  if (raw == null) return ALL_LAYERS;
  return raw.split(',').filter((x): x is MapLayer => (ALL_LAYERS as string[]).includes(x));
}

/** Size by √|s| (a song outside the profile — the smallest). */
const radius = (s: number | null) => (s == null ? 3 : Math.min(11, 2.5 + 2.6 * Math.sqrt(Math.abs(s))));

export function MapTab({ userId }: { userId: number }) {
  const q = useRoute().query;
  const model = modelOf(q);
  const layout: Layout = q.get('layout') === 'facets' ? 'facets' : 'pca';
  const basis = q.get('basis') === 'user' ? 'user' : 'global';
  const mix = q.get('mix') ?? undefined;
  const wave = q.get('wave') ?? undefined;
  const layers = layersOf(q);
  const res = useAsync((s) => insightsApi.map(userId, { model, basis, mix, wave }, s), [userId, model, basis, mix, wave]);
  const mixes = useAsync((s) => insightsApi.mixes(userId, s), [userId]);
  const waves = useAsync((s) => insightsApi.waves(userId, { limit: 30 }, s), [userId]);

  const toggle = (l: MapLayer) => {
    const next = layers.includes(l) ? layers.filter((x) => x !== l) : [...layers, l];
    setQuery({ layers: next.length === ALL_LAYERS.length ? null : next.join(',') || 'none' });
  };

  return (
    <>
      <div className="space-y-2 px-4 pb-2">
        <div className="flex flex-wrap items-center gap-2">
          <Segmented<Layout> label="Раскладка" value={layout} options={[{ value: 'pca', label: 'Звук (PCA)' }, { value: 'facets', label: 'Грани' }]} onChange={(v) => setQuery({ layout: v === 'pca' ? null : v })} />
          {layout === 'pca' && (
            <Segmented label="Оси" value={basis} options={[{ value: 'global', label: 'Общие оси' }, { value: 'user', label: 'Личные' }]} onChange={(v) => setQuery({ basis: v === 'global' ? null : v })} />
          )}
          <Segmented<TasteModel> label="Модель" value={model} options={MODELS} onChange={(v) => setQuery({ model: v === 'v2' ? null : v })} />
        </div>
        <div className="flex flex-wrap gap-1.5">
          {ALL_LAYERS.map((l) => (
            <button
              key={l}
              type="button"
              role="checkbox"
              aria-checked={layers.includes(l)}
              onClick={() => toggle(l)}
              className={cx('min-h-8 rounded-full border px-3 text-[13px]', layers.includes(l) ? 'border-accent bg-accent/15' : 'border-line text-muted')}
            >
              {layers.includes(l) ? '✓ ' : ''}
              {LAYER_LABELS[l]}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <select className="input min-w-0 text-sm" aria-label="Микс на карте" value={mix ?? ''} onChange={(e) => setQuery({ mix: e.target.value || null })}>
            <option value="">Микс: текущий</option>
            {mixes.data?.map((m) => (
              <option key={m.id} value={m.id}>
                {fmtStamp(m.created_at)} · {m.title}
              </option>
            ))}
          </select>
          <select className="input min-w-0 text-sm" aria-label="Сессия волны на карте" value={wave ?? ''} onChange={(e) => setQuery({ wave: e.target.value || null })}>
            <option value="">Волна: последняя</option>
            {waves.data?.rows.filter((w) => !w.reconstructed).map((w) => (
              <option key={w.session_id} value={w.session_id}>
                {fmtStamp(w.created_at)} · {seedKindLabel(w.seed_kind)}
                {w.seed?.title ? ` «${w.seed.title}»` : ''}
              </option>
            ))}
          </select>
        </div>
      </div>
      {res.loading ? (
        <Loading />
      ) : res.error || !res.data ? (
        <ErrorState text={res.error ?? 'Нет данных'} onRetry={() => res.reload()} />
      ) : !res.data.available ? (
        <div className="px-4">
          <Notice>{mapReasonLabel(res.data.reason)} Вес песен по-прежнему виден на вкладке «Вкус».</Notice>
        </div>
      ) : (
        <TasteMap d={res.data} userId={userId} layout={layout} layers={layers} />
      )}
    </>
  );
}

function TasteMap({ d, userId, layout, layers }: { d: MapOut; userId: number; layout: Layout; layers: MapLayer[] }) {
  const q = useRoute().query;
  const [text, setText] = useState(q.get('q') ?? '');
  const [picked, setPicked] = useState<string | null>(null);
  const needle = text.trim().toLowerCase();
  const rank = useMemo(() => new Map(d.facets.map((f) => [f.id, f.color_index])), [d.facets]);
  const colorOf = (p: MapPoint) => (p.facet_id != null && rank.has(p.facet_id) ? facetColor(rank.get(p.facet_id)) : 'var(--color-dim)');
  const xy = (p: { x: number; y: number; fx: number; fy: number }) => (layout === 'pca' ? { x: p.x, y: p.y } : { x: p.fx, y: p.fy });

  const visible = d.points.filter((p) => p.layers.some((l) => layers.includes(l)));
  const dots: Dot[] = [];
  for (const p of visible) {
    const match = !needle || `${p.artist} ${p.title}`.toLowerCase().includes(needle);
    const at = xy(p);
    const inProfile = layers.includes('profile') && p.layers.includes('profile');
    const inMix = layers.includes('mix') && p.layers.includes('mix');
    const inWave = layers.includes('wave') && p.layers.includes('wave');
    const shape = inProfile ? 'circle' : inMix ? 'diamond' : 'square';
    dots.push({
      id: p.key,
      ...at,
      r: inProfile ? radius(p.s) : 4.5,
      color: colorOf(p),
      shape,
      hollow: !inProfile && inMix && p.familiar === false,
      ring: inProfile && p.seed,
      cross: p.disliked,
      negative: inProfile && (p.s ?? 0) < 0,
      label: inWave && p.wave_position != null ? String(p.wave_position + 1) : undefined,
      dim: !match,
    });
  }
  const anchors: Anchor[] = d.facets.map((f) => ({ id: f.id, ...xy(f), label: f.artists.join(', ') || 'грань', color: facetColor(f.color_index) }));
  const path = layers.includes('wave')
    ? d.points.filter((p) => p.layers.includes('wave') && p.wave_position != null).sort((a, b) => a.wave_position! - b.wave_position!).map(xy)
    : [];
  const explained = d.explained.reduce((a, b) => a + b, 0);
  const point = picked ? d.points.find((p) => p.key === picked) : undefined;
  const sorted = [...visible].sort((a, b) => (b.s ?? -99) - (a.s ?? -99));

  return (
    <div className="px-4">
      <input
        type="search"
        className="input mb-2"
        placeholder="Найти на карте"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => setQuery({ q: text.trim() || null })}
      />
      <div className="mx-auto max-w-3xl"><ScatterMap key={`${layout}:${d.basis}`} dots={dots} anchors={anchors} path={path} selected={picked} onPick={setPicked} label="Карта вкуса" /></div>
      <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
        <li>● вкус: размер ∝ √|s|, цвет — грань</li>
        <li>◎ сид</li>
        <li>○ красный контур — s &lt; 0</li>
        <li>× 👎</li>
        <li>◆ микс: заливка — знакомое, контур — новое</li>
        <li>■ 1→2→3 волна по порядку выдачи</li>
        <li>★ центр грани</li>
      </ul>
      {d.facets.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
          {d.facets.map((f) => (
            <li key={f.id} className="inline-flex items-center gap-1">
              <Swatch color={facetColor(f.color_index)} />
              {Math.round(f.weight * 100)} % {f.artists.join(', ')}
            </li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-xs text-muted">
        {layout === 'pca'
          ? `2D сохраняет ${Math.round(explained * 100)} % разброса звука: расстояния приблизительные, точная близость — раскладка «Грани». Оси: ${d.basis === 'user' ? 'личные (по песням с положительным весом)' : 'общие для всех пользователей'}.`
          : 'Грани: точка тянется к граням, к которым её звук ближе (косинус); у центра — далека от всех. Это та же близость, по которой работают рекомендации.'}
        {d.truncated ? ' Показаны не все песни профиля — самые весомые.' : ''}
      </p>
      <p className="mt-1 text-xs text-muted">
        {d.mix_id != null ? <Link to={`/admin/mix/${d.mix_id}`} className="text-accent">Микс на карте</Link> : 'Микса нет'} ·{' '}
        {d.wave_id ? <Link to={`/admin/wave/${encodeURIComponent(d.wave_id)}`} className="text-accent">Сессия волны</Link> : 'сессии волны нет'}
      </p>

      {d.missing.length > 0 && (
        <details className="mt-3 rounded-xl bg-surface p-3 text-sm">
          <summary className="cursor-pointer font-semibold">Без звука: {d.missing.length}</summary>
          <ul className="mt-2 space-y-1 text-muted">
            {d.missing.map((m) => (
              <li key={m.key} className="truncate">
                {trackName(m)} <span className="text-xs">({m.layers.map((l) => LAYER_LABELS[l as MapLayer] ?? l).join(', ')})</span>
              </li>
            ))}
          </ul>
        </details>
      )}
      <details className="mt-3 rounded-xl bg-surface p-3 text-sm">
        <summary className="cursor-pointer font-semibold">Список точек ({visible.length})</summary>
        <ul className="mt-2 divide-y divide-line/50">
          {sorted.map((p) => (
            <li key={p.key}>
              <button type="button" className="flex min-h-10 w-full items-center gap-2 text-left hover:bg-fg/5" onClick={() => setPicked(p.key)}>
                <Swatch color={colorOf(p)} />
                <span className="min-w-0 flex-1 truncate">{trackName(p)}</span>
                <span className="shrink-0 text-xs text-muted">{p.layers.map((l) => LAYER_LABELS[l]).join(', ')}</span>
                <span className="w-10 shrink-0 text-right tabular-nums">{p.s == null ? '—' : fmtDec(p.s)}</span>
              </button>
            </li>
          ))}
        </ul>
      </details>

      <Sheet open={!!point} onClose={() => setPicked(null)} title={point ? trackName(point) : ''}>
        {point && <PointInfo p={point} userId={userId} facetName={d.facets.find((f) => f.id === point.facet_id)?.artists.join(', ')} color={colorOf(point)} />}
      </Sheet>
    </div>
  );
}

function PointInfo({ p, userId, facetName, color }: { p: MapPoint; userId: number; facetName?: string; color: string }) {
  const flags = [p.seed && 'сид', p.liked && '♥', p.disliked && '👎', p.negative && 's < 0', p.familiar === true && 'знакомое', p.familiar === false && 'новое'].filter(Boolean);
  return (
    <div className="space-y-3 pb-2 text-sm">
      <div className="grid grid-cols-3 gap-2 text-center">
        <Block3 label="вес s" value={p.s == null ? '—' : fmtDec(p.s)} />
        <Block3 label="P" value={p.p == null ? '—' : fmtDec(p.p)} />
        <Block3 label="N" value={p.n == null ? '—' : fmtDec(p.n)} />
      </div>
      <p className="flex items-center gap-2">
        <Swatch color={color} /> Грань: {facetName || 'вне граней'}
      </p>
      {flags.length > 0 && <p className="text-muted">{flags.join(' · ')}</p>}
      <ul className="space-y-1">
        {p.layers.includes('profile') && <li>Вкус: {p.s == null ? 'нет веса' : `вес ${fmtDec(p.s)}`}</li>}
        {p.mix_position != null && (
          <li>
            В миксе №{p.mix_position + 1}
            {p.generator_title ? ` · ${p.generator_title}` : ''}
          </li>
        )}
        {p.wave_position != null && <li>Выдан волной {p.wave_position + 1}-м{p.generator_title ? ` · ${p.generator_title}` : ''}</li>}
        {p.outcome && <li>Итог: {outcomeLabel(p.outcome)}</li>}
      </ul>
      {p.s != null && (
        <Button block variant="primary" onClick={() => navigate(trackLink(userId, p.key))}>
          Почему такой вес
        </Button>
      )}
    </div>
  );
}

function Block3({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-raised py-2">
      <div className="font-semibold tabular-nums">{value}</div>
      <div className="text-[11px] text-muted">{label}</div>
    </div>
  );
}

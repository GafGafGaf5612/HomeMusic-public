import { insightsApi, type MixItem, type MixOut, type WaveOut, type WaveRow } from '../../api/insights';
import { Funnel } from '../../components/charts/Funnel';
import { ShareBar } from '../../components/charts/ShareBar';
import { facetColor } from '../../components/charts/palette';
import { Empty, ErrorState, Loading, Notice, PageHeader } from '../../components/ui';
import {
  attributionLabel,
  cleanupLabel,
  contributionLabel,
  fmtDec,
  fmtListened,
  fmtNum,
  fmtStamp,
  launchOriginLabel,
  mixStateLabel,
  outcomeLabel,
  parseTime,
  plural,
  rejectLabel,
  seedKindLabel,
  waveOriginLabel,
} from '../../lib/format';
import { useAsync } from '../../lib/hooks';
import { periodOf, periodRange } from '../../lib/insights';
import { navigate, useRoute } from '../../lib/router';
import { codeShares } from './Listening';
import { Block, Columns, Line, PeriodPicker, ScrollTable, Swatch, Tile, Tiles, trackName } from './common';

// --- mixes ---------------------------------------------------------------------------------------------------

export function MixesTab({ userId }: { userId: number }) {
  const res = useAsync((s) => insightsApi.mixes(userId, s), [userId]);
  if (res.loading) return <Loading />;
  if (res.error || !res.data) return <ErrorState text={res.error ?? 'Нет данных'} onRetry={() => res.reload()} />;
  if (!res.data.length) return <Empty icon="sparkle" title="Миксов ещё не было" />;
  return (
    <ul className="space-y-2 px-4">
      {res.data.map((m) => (
        <li key={m.id}>
          <button type="button" onClick={() => navigate(`/admin/mix/${m.id}`)} className="block w-full rounded-xl bg-surface p-3 text-left hover:bg-fg/5">
            <div className="flex items-baseline justify-between gap-2">
              <span className="min-w-0 truncate font-semibold">{m.title}</span>
              <span className="shrink-0 text-xs text-muted">{fmtStamp(m.created_at)}</span>
            </div>
            <div className="truncate text-xs text-muted">
              {m.mode_title}
              {m.taste_model ? ` · вкус ${m.taste_model}` : ''} · {m.status === 'active' ? 'действует' : 'закрыт'}
              {m.top_generators.length ? ` · ${m.top_generators.map((g) => g.title).join(', ')}` : ''}
            </div>
            <div className="mt-1 text-sm">
              {m.launches ? `запускал ${m.launches} ${plural(m.launches, ['раз', 'раза', 'раз'])}` : 'не запускал'} · {m.heard} из {m.size} прослушано
              {m.likes ? ` · ♥${m.likes}` : ''}
              {m.dislikes ? ` · 👎${m.dislikes}` : ''}
            </div>
          </button>
        </li>
      ))}
    </ul>
  );
}

/** A stacked bar of a song's score parts (positive to the right, penalties shown in red). */
function PartsBar({ item, max }: { item: MixItem; max: number }) {
  const p = item.score_parts;
  if (!p) return <span className="text-xs text-dim">{item.score == null ? '—' : fmtDec(item.score)}</span>;
  const parts = [
    { v: p.generators, c: 'var(--color-facet-1)', t: 'генераторы' },
    { v: p.sound, c: 'var(--color-facet-3)', t: 'звук' },
    { v: p.popularity, c: 'var(--color-facet-4)', t: 'популярность' },
  ];
  const pen = -(p.track_penalty + p.artist_penalty);
  return (
    <span className="flex items-center gap-1.5" title={`генераторы ${fmtDec(p.generators)} · звук ${fmtDec(p.sound)} · популярность ${fmtDec(p.popularity)} · штрафы ${fmtDec(-pen)}`}>
      <span className="flex h-2 w-20 overflow-hidden rounded-full bg-raised">
        {parts.map((x) => (
          <span key={x.t} style={{ width: `${(Math.max(0, x.v) / max) * 100}%`, background: x.c }} />
        ))}
        {pen > 0 && <span style={{ width: `${(pen / max) * 100}%`, background: 'var(--color-danger)' }} />}
      </span>
      <span className="tabular-nums">{fmtDec(p.total)}</span>
    </span>
  );
}

export function MixDetailPage({ id }: { id: number }) {
  const res = useAsync((s) => insightsApi.mix(id, s), [id]);
  const m = res.data;
  return (
    <div className="pb-6">
      <PageHeader title={m?.title ?? 'Микс'} back="/admin" sub={m ? `${fmtStamp(m.created_at)} · ${m.mode_title}` : undefined} />
      {res.loading ? <Loading /> : res.error || !m ? <ErrorState text={res.error ?? 'Не найдено'} onRetry={() => res.reload()} /> : <MixBody m={m} />}
    </div>
  );
}

function MixBody({ m }: { m: MixOut }) {
  const f = m.funnel;
  const rejected = f.rejected;
  const rejectedSum = rejected ? Object.values(rejected).reduce((a, b) => a + b, 0) : null;
  const facetRank = new Map((m.facets ?? []).map((x, i) => [x.id, i]));
  const active = m.items.filter((i) => i.state === 'active');
  const rest = m.items.filter((i) => i.state !== 'active');
  const max = Math.max(1e-9, ...m.items.map((i) => (i.score_parts ? i.score_parts.generators + i.score_parts.sound + i.score_parts.popularity - i.score_parts.track_penalty - i.score_parts.artist_penalty : 0)));
  const cleaned = m.items.filter((i) => i.cleanup);
  const trashed = cleaned.filter((i) => i.cleanup === 'trashed').length;
  return (
    <>
      {m.description && <p className="px-4 pb-2 text-sm text-muted">{m.description}</p>}
      <Block title="Воронка">
        <Funnel
          steps={[
            { label: 'Генераторы', value: f.generators ? f.generators.filter((g) => !g.off).length : null },
            { label: 'Кандидаты', value: f.candidates, share: false },
            {
              label: 'После фильтров',
              value: f.kept,
              note: rejected ? `отсеяно ${rejectedSum}: ${Object.entries(rejected).filter(([, v]) => v).map(([k, v]) => `${rejectLabel(k)} ${v}`).join(', ') || 'ничего'}` : 'причины отсева не записаны (микс до 31.10.2026)',
            },
            { label: 'В миксе', value: f.active, note: `${f.familiar} знакомых / ${f.new} новых` },
            { label: 'Начато', value: f.started, note: `${f.launches} ${plural(f.launches, ['запуск', 'запуска', 'запусков'])}` },
            { label: 'Прослушано', value: f.heard },
            { label: 'Дослушано', value: f.finished, note: `ранних пропусков ${f.early_skips} · ♥${f.liked} · 👎${f.disliked}` },
          ]}
        />
        {f.sound_scored != null && <p className="mt-2 text-xs text-muted">оценено по звуку: {f.sound_scored}</p>}
      </Block>

      <Columns>
        <Block title={`Сиды (${m.seeds.length})`}>
          {m.seeds.length ? (
            <ul className="divide-y divide-line/50">
              {m.seeds.map((s) => (
                <Line key={s.key} title={<span className="flex items-center gap-2"><Swatch color={s.facet_id && facetRank.has(s.facet_id) ? facetColor(facetRank.get(s.facet_id)) : 'var(--color-dim)'} />{trackName(s)}</span>} />
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted">Сиды не записаны.</p>
          )}
          {m.seed_labels?.length ? <p className="mt-2 text-xs text-muted">{m.seed_labels.join(' · ')}</p> : null}
        </Block>
        <Block title="Грани">
          {m.facets?.length ? (
            <ul className="space-y-1 text-sm">
              {m.facets.map((x, i) => (
                <li key={x.id} className="flex items-center gap-2">
                  <Swatch color={facetColor(i)} />
                  <span className="tabular-nums">{Math.round(x.weight * 100)} %</span>
                  <span className="min-w-0 flex-1 truncate">{x.artists.join(', ')}</span>
                  <span className="text-xs text-muted">{x.size}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted">Граней не было (embedder не работал или модель v1).</p>
          )}
          <p className="mt-2 text-xs text-muted">
            {m.maturity != null ? `зрелость ${fmtDec(m.maturity)} · ` : ''}
            {m.build_seconds != null ? `собран за ${fmtDec(m.build_seconds, 1)} с · ` : ''}
            {m.artists} {plural(m.artists, ['исполнитель', 'исполнителя', 'исполнителей'])}
          </p>
        </Block>
      </Columns>

      <Block title="Генераторы">
        {f.generators ? (
          <ScrollTable
            rows={f.generators.map((g) => {
              const mine = active.filter((i) => i.generator === g.generator);
              return {
                ...g,
                started: mine.filter((i) => i.outcome !== 'not_played').length,
                heard: mine.filter((i) => ['heard', 'finished', 'liked'].includes(i.outcome)).length,
                early: mine.filter((i) => i.outcome === 'early_skip').length,
                liked: mine.filter((i) => i.outcome === 'liked').length,
              };
            })}
            rowKey={(r) => r.generator}
            cols={[
              { label: 'Генератор', cell: (r) => (r.off ? `${r.title} (выкл.)` : r.title) },
              { label: 'Дал', cell: (r) => (r.error ? `ошибка` : fmtNum(r.tracks)), align: 'right' },
              { label: 'Взято', cell: (r) => fmtNum(r.used), align: 'right' },
              { label: 'Начато', cell: (r) => fmtNum(r.started), align: 'right' },
              { label: 'Прослуш.', cell: (r) => fmtNum(r.heard), align: 'right' },
              { label: 'Ранн. проп.', cell: (r) => fmtNum(r.early), align: 'right' },
              { label: '♥', cell: (r) => fmtNum(r.liked), align: 'right' },
              { label: 'Время', cell: (r) => (r.seconds == null ? '—' : `${fmtDec(r.seconds, 1)} с`), align: 'right' },
            ]}
          />
        ) : (
          <p className="text-sm text-muted">Не записано.</p>
        )}
        <p className="mt-2 text-xs text-muted">«Начато», «Прослушано» — по песням, где генератор главный, по итогам песен микса.</p>
      </Block>

      <Block title={`Песни (${active.length})`}>
        <MixItems items={active} max={max} />
        <p className="mt-2 flex flex-wrap gap-x-3 text-xs text-muted">
          <span><Swatch color="var(--color-facet-1)" /> генераторы</span>
          <span><Swatch color="var(--color-facet-3)" /> звук</span>
          <span><Swatch color="var(--color-facet-4)" /> популярность</span>
          <span><Swatch color="var(--color-danger)" /> штрафы</span>
          {active.some((i) => !i.score_parts) && <span>у старых миксов частей оценки нет — не пересчитываются</span>}
        </p>
      </Block>

      {rest.length > 0 && (
        <div className="px-4 pt-3">
          <details className="rounded-xl bg-surface p-3">
            <summary className="cursor-pointer text-sm font-semibold">Запас и не скачанные ({rest.length})</summary>
            <div className="mt-2">
              <MixItems items={rest} max={max} />
            </div>
          </details>
        </div>
      )}

      {cleaned.length > 0 && (
        <p className="px-4 pt-3 text-sm text-muted">
          Очистка после микса: удалено {trashed}, оставлено {cleaned.length - trashed}
          {' — '}
          {[...new Set(cleaned.filter((i) => i.cleanup !== 'trashed').map((i) => cleanupLabel(i.cleanup)))].join('; ')}
        </p>
      )}

      <Block title={`Запуски (${m.launch_list.length})`}>
        <ScrollTable
          rows={m.launch_list}
          rowKey={(r) => r.id}
          cols={[
            { label: 'Когда', cell: (r) => fmtStamp(r.started_at) },
            { label: 'Откуда', cell: (r) => launchOriginLabel(r.origin) },
            { label: 'Как найден', cell: (r) => attributionLabel(r.attribution) },
            { label: 'Начато', cell: (r) => fmtNum(r.started), align: 'right' },
            { label: 'Прослуш.', cell: (r) => fmtNum(r.heard), align: 'right' },
            { label: 'Ранн. проп.', cell: (r) => fmtNum(r.early_skips), align: 'right' },
            { label: 'Клиент', cell: (r) => r.client ?? '—' },
          ]}
          empty="Микс не запускали"
        />
      </Block>
    </>
  );
}

function MixItems({ items, max }: { items: MixItem[]; max: number }) {
  return (
    <ScrollTable
      rows={items}
      rowKey={(r) => `${r.state}:${r.position}`}
      cols={[
        { label: 'Трек', cell: (r) => `${r.position + 1}. ${trackName(r)}` },
        { label: 'Генератор', cell: (r) => r.generator_title ?? r.generator ?? '—' },
        { label: 'Знак./нов.', cell: (r) => (r.familiar ? 'знакомое' : 'новое') },
        { label: 'Оценка', cell: (r) => <PartsBar item={r} max={max} /> },
        { label: 'Итог', cell: (r) => outcomeLabel(r.outcome, r.early_skip_at) + (r.heard_outside ? ' · слушал вне микса' : '') },
        { label: 'Состояние', cell: (r) => [mixStateLabel(r.state), r.downloaded_for_mix && 'скачан для микса', cleanupLabel(r.cleanup)].filter(Boolean).join(' · ') },
      ]}
    />
  );
}

// --- waves ---------------------------------------------------------------------------------------------------

export function WavesTab({ userId }: { userId: number }) {
  const p = periodOf(useRoute().query);
  const res = useAsync((s) => insightsApi.waves(userId, { ...periodRange(p), limit: 100 }, s), [userId, p]);
  const d = res.data;
  return (
    <>
      <PeriodPicker />
      {res.loading ? (
        <Loading />
      ) : res.error || !d ? (
        <ErrorState text={res.error ?? 'Нет данных'} onRetry={() => res.reload()} />
      ) : (
        <>
          <Tiles>
            <Tile value={fmtNum(d.launches)} label="запусков" />
            <Tile value={fmtNum(d.restarts)} label="перезапусков" hint="приложение само пересоздало сессию — не действие пользователя" />
            <Tile value={fmtNum(d.total)} label="сессий" />
          </Tiles>
          <Columns>
            <Block title="Где запускали">
              <ShareBar parts={codeShares(d.by_origin, waveOriginLabel)} />
            </Block>
            <Block title="С чего запускали">
              <ShareBar parts={codeShares(d.by_seed_kind, seedKindLabel)} />
            </Block>
          </Columns>
          <Block title="Сессии">
            {d.rows.length ? (
              <ul className="divide-y divide-line/50">
                {d.rows.map((w) => (
                  <WaveLine key={w.session_id} w={w} />
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted">Волну в этот период не запускали.</p>
            )}
            {d.total > d.rows.length && <p className="mt-2 text-xs text-muted">Показаны {d.rows.length} новых из {d.total}.</p>}
          </Block>
        </>
      )}
    </>
  );
}

function seedText(w: WaveRow): string {
  if (w.reconstructed) return 'восстановлено по событиям';
  const name = w.seed ? [w.seed.artist, w.seed.title].filter(Boolean).join(' — ') : '';
  return `${seedKindLabel(w.seed_kind)}${name ? ` «${name}»` : ''}`;
}

function WaveLine({ w }: { w: WaveRow }) {
  return (
    <Line
      onClick={() => navigate(`/admin/wave/${encodeURIComponent(w.session_id)}`)}
      title={`${fmtStamp(w.created_at)} · ${w.restart ? 'перезапуск' : waveOriginLabel(w.origin)} · ${seedText(w)}`}
      sub={`${w.started} начато · ${w.heard} прослушано · ранних ${w.early_skips}${w.likes ? ` · ♥${w.likes}` : ''}${w.dislikes ? ` · 👎${w.dislikes}` : ''}${w.client ? ` · ${w.client}` : ''}`}
    />
  );
}

export function WaveDetailPage({ sid }: { sid: string }) {
  const res = useAsync((s) => insightsApi.wave(sid, s), [sid]);
  const w = res.data;
  return (
    <div className="pb-6">
      <PageHeader title="Сессия волны" back="/admin" sub={w ? `${fmtStamp(w.created_at)} · ${seedText(w)}` : undefined} />
      {res.loading ? <Loading /> : res.error || !w ? <ErrorState text={res.error ?? 'Не найдено'} onRetry={() => res.reload()} /> : <WaveBody w={w} />}
    </div>
  );
}

function WaveBody({ w }: { w: WaveOut }) {
  const minutes = Math.max(0, Math.round((parseTime(w.last_activity_at).getTime() - parseTime(w.created_at).getTime()) / 60000));
  const gens = Object.entries(w.gens);
  return (
    <>
      {w.reconstructed && (
        <div className="px-4 pb-2">
          <Notice>Сессия восстановлена по событиям: с чего и где её запускали, неизвестно.</Notice>
        </div>
      )}
      {w.cleared && (
        <div className="px-4 pb-2">
          <Notice kind="info">Состояние сессии очищено (старая сессия): осталась выжимка — выданные треки и итоги на месте.</Notice>
        </div>
      )}
      <Block title="Сессия">
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
          <dt className="text-muted">Место</dt>
          <dd>{w.restart ? `перезапуск${w.restart_of ? ` сессии ${w.restart_of}` : ''}` : waveOriginLabel(w.origin)}</dd>
          <dt className="text-muted">Сид</dt>
          <dd className="min-w-0 truncate">{seedText(w)}</dd>
          <dt className="text-muted">Клиент</dt>
          <dd>{w.client ?? 'не записано'}</dd>
          <dt className="text-muted">Режим, модель</dt>
          <dd>{[w.mode, w.taste_model].filter(Boolean).join(' · ') || '—'}</dd>
          <dt className="text-muted">Длительность</dt>
          <dd>{minutes} мин · слушал {fmtListened(w.listened_ms)}</dd>
          <dt className="text-muted">Вызовов next</dt>
          <dd>{w.next_calls ?? '—'}</dd>
          <dt className="text-muted">Итог</dt>
          <dd>
            выдано {w.handed ?? '—'} · начато {w.started} · прослушано {w.heard} · дослушано {w.finished} · ранних {w.early_skips}
            {w.likes ? ` · ♥${w.likes}` : ''}
            {w.dislikes ? ` · 👎${w.dislikes}` : ''}
            {w.downloads ? ` · скачано ${w.downloads}` : ''}
          </dd>
        </dl>
      </Block>
      <Block title="Генераторы">
        <ScrollTable
          rows={gens}
          rowKey={([g]) => g}
          cols={[
            { label: 'Генератор', cell: ([, v]) => (v.off ? `${v.title} (выкл.)` : v.title) },
            { label: 'Треков', cell: ([, v]) => fmtNum(v.tracks), align: 'right' },
            { label: 'Секунд', cell: ([, v]) => (v.seconds == null ? '—' : fmtDec(v.seconds, 1)), align: 'right' },
            { label: 'Ошибка', cell: ([, v]) => v.error ?? '' },
          ]}
          empty="Не записано"
        />
      </Block>
      <Block title={`Выданные треки (${w.items.length})`}>
        <ScrollTable
          rows={w.items}
          rowKey={(r) => `${r.position}:${r.key}`}
          cols={[
            { label: 'Трек', cell: (r) => `${r.position + 1}. ${trackName(r)}` },
            { label: 'Генератор', cell: (r) => r.generator_title ?? r.generator ?? '—' },
            { label: 'Знак./нов.', cell: (r) => (r.familiar == null ? '—' : r.familiar ? 'знакомое' : 'новое') },
            { label: 'sim', cell: (r) => (r.sim == null ? '—' : fmtDec(r.sim)), align: 'right' },
            { label: 'Итог', cell: (r) => outcomeLabel(r.outcome, r.early_skip_at) + (r.handed_count > 1 ? ` · выдан ${r.handed_count} раза` : '') },
          ]}
          empty="Волна ничего не записала о выданном (старая сессия)"
        />
      </Block>
      <Block title={`События (${w.events.length})`}>
        <ul className="divide-y divide-line/50">
          {w.events.map((e, i) => (
            <Line key={i} title={trackName(e.track)} sub={`${fmtStamp(e.ts)}${e.client ? ` · ${e.client}` : ''}`} value={contributionLabel(e.kind, e.position_s)} />
          ))}
        </ul>
      </Block>
    </>
  );
}

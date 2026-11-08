import { insightsApi, type Contribution, type TasteTrackOut } from '../../api/insights';
import { facetColor } from '../../components/charts/palette';
import { ErrorState, Loading, Notice, PageHeader, Segmented, cx } from '../../components/ui';
import { contributionLabel, fmtDate, fmtDec, fmtStamp, outcomeLabel, plural, sourceLabel } from '../../lib/format';
import { useAsync } from '../../lib/hooks';
import { setQuery } from '../../lib/insights';
import { Link, useRoute } from '../../lib/router';
import { MODELS, facetRanks, modelOf } from './Taste';
import { Block, Columns, Line, Swatch, trackName } from './common';
import type { TasteModel } from '../../api/insights';

/** `/admin/user/:id/track?key=` — why a song weighs what it does, down to single events. */
export function TrackWeightPage({ userId }: { userId: number }) {
  const q = useRoute().query;
  const key = q.get('key') ?? '';
  const model = modelOf(q);
  const res = useAsync((s) => insightsApi.track(userId, key, model, s), [userId, key, model]);
  // the facets' order (their colors) comes from the summary
  const taste = useAsync((s) => insightsApi.taste(userId, model, s), [userId, model], !!res.data?.facet_cos);
  const t = res.data;
  return (
    <div className="pb-6">
      <PageHeader title={t ? trackName({ artist: t.artist?.name, title: t.title }) : 'Вес песни'} back={`/admin/user/${userId}?tab=taste`} />
      <div className="px-4 pb-2">
        <Segmented<TasteModel> label="Модель" value={model} options={MODELS} onChange={(v) => setQuery({ model: v === 'v2' ? null : v })} />
      </div>
      {!key ? (
        <ErrorState text="Не указан трек" />
      ) : res.loading ? (
        <Loading />
      ) : res.error || !t ? (
        <ErrorState text={res.error ?? 'Не найдено'} onRetry={() => res.reload()} />
      ) : (
        <Body t={t} facets={taste.data?.facets ?? null} />
      )}
    </div>
  );
}

function Body({ t, facets }: { t: TasteTrackOut; facets: Array<{ id: string; artists: string[] }> | null }) {
  const ranks = facetRanks(facets);
  const flags = [
    t.liked && '♥',
    `дослушан ${t.finishes} ${plural(t.finishes, ['раз', 'раза', 'раз'])}`,
    `ранних пропусков ${t.early_skips}`,
    t.late_skips ? `поздних пропусков ${t.late_skips}` : null,
    t.familiar ? 'знакомый' : 'незнакомый',
    t.seed_eligible && 'может быть сидом',
    t.disliked && '👎',
    t.downloaded && 'скачан',
    t.album_liked && '♥ альбома',
  ].filter(Boolean);
  const a = t.artist;
  return (
    <>
      <div className="px-4">
        <div className="rounded-xl bg-surface p-3">
          <p className="font-mono text-[13px] leading-relaxed break-words">{t.formula}</p>
          <p className="mt-2 text-sm text-muted">{flags.join(' · ')}</p>
          <p className="mt-1 text-xs text-muted">
            посчитано {fmtStamp(t.now)}
            {t.last_heard ? ` · последний раз ${fmtStamp(t.last_heard)}` : ''}
            {t.positive_age != null ? ` · последний плюс ${fmtDec(t.positive_age, 1)} акт. ед. назад` : ''}
          </p>
        </div>
      </div>

      <Block title={`Вклады (${t.contributions.length})`} right={<span className="text-xs text-muted">новые сверху</span>}>
        {t.contributions.length ? (
          <ul className="divide-y divide-line/50">
            {t.contributions.map((c, i) => (
              <ContributionRow key={c.event_id ?? i} c={c} />
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">Вкладов нет.</p>
        )}
        <p className="mt-2 text-xs text-muted">w = 0,5^(A/30), A — активные единицы после дня события. Серым — вклад 0 и почему.</p>
      </Block>

      <Columns>
        <Block title="Исполнитель">
          {a ? (
            <div className="text-sm">
              <p>
                <b>{a.name}</b>: A_artist = {fmtDec(a.score)}
                {a.liked ? ' · ♥ исполнителя' : ''}
                {a.disliked ? ' · 👎' : ''}
              </p>
              <p className="text-muted">
                {a.tracks} {plural(a.tracks, ['песня', 'песни', 'песен'])} · {a.listens} {plural(a.listens, ['прослушивание', 'прослушивания', 'прослушиваний'])} · Σ+ {fmtDec(a.positive_sum)} · Σ−{' '}
                {fmtDec(a.negative_sum)} · песни {fmtDec(a.tracks_part)} + ♥ {fmtDec(a.likes_part)}
              </p>
              <p className="mt-1 font-mono text-[11px] text-muted break-words">{a.formula}</p>
            </div>
          ) : (
            <p className="text-sm text-muted">Нет данных</p>
          )}
        </Block>
        <Block title="Близость к граням">
          {t.facet_cos ? (
            <ul className="space-y-1.5 text-sm">
              {Object.entries(t.facet_cos)
                .sort((x, y) => y[1] - x[1])
                .map(([id, cos]) => (
                  <li key={id} className="flex items-center gap-2">
                    <Swatch color={ranks.has(id) ? facetColor(ranks.get(id)) : 'var(--color-dim)'} />
                    <span className="min-w-0 flex-1 truncate">{facets?.find((f) => f.id === id)?.artists.join(', ') ?? id}</span>
                    <span className="h-2 w-16 overflow-hidden rounded-full bg-raised">
                      <span className="block h-full bg-accent" style={{ width: `${Math.max(0, cos) * 100}%` }} />
                    </span>
                    <span className="w-10 text-right tabular-nums">{fmtDec(cos)}</span>
                  </li>
                ))}
            </ul>
          ) : (
            <p className="text-sm text-muted">Нет: модель v1 или embedder недоступен.</p>
          )}
        </Block>
      </Columns>

      <Block title="В рекомендациях">
        {!t.mixes.length && !t.waves.length ? (
          <p className="text-sm text-muted">Ни в миксах, ни в волне не был.</p>
        ) : (
          <ul className="divide-y divide-line/50">
            {t.mixes.map((m) => (
              <li key={`m${m.mix_id}:${m.position}`}>
                <Link to={`/admin/mix/${m.mix_id}`} className="flex min-h-11 items-center gap-3 py-1.5 hover:bg-fg/5">
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm">
                      Микс {fmtDate(m.created_at)} №{m.position + 1}
                      {m.state !== 'active' ? ` (${m.state === 'reserve' ? 'запас' : 'не скачался'})` : ''}
                    </span>
                    <span className="block truncate text-xs text-muted">
                      {m.generator_title ?? m.generator ?? '—'}
                      {m.score != null ? `, ${fmtDec(m.score)}` : ''}
                    </span>
                  </span>
                  <span className="shrink-0 text-sm">{outcomeLabel(m.outcome, m.early_skip_at)}</span>
                </Link>
              </li>
            ))}
            {t.waves.map((w) => (
              <li key={`w${w.session_id}:${w.position}`}>
                <Link to={`/admin/wave/${encodeURIComponent(w.session_id)}`} className="flex min-h-11 items-center gap-3 py-1.5 hover:bg-fg/5">
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm">
                      Волна {fmtStamp(w.first_handed_at)} · {w.position + 1}-й
                    </span>
                    <span className="block truncate text-xs text-muted">{w.generator ?? '—'}</span>
                  </span>
                  <span className="shrink-0 text-sm">{outcomeLabel(w.outcome, w.early_skip_at)}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Block>

      <Block title="Отдых">
        <ul className="divide-y divide-line/50 text-sm">
          <Line title="Не повторять в миксе до" value={fmtDate(t.rest.recent_until) || '—'} />
          <Line title="Снова в «Любимом» после" value={fmtDate(t.rest.favorites_after) || '—'} />
          <Line title="Снова в «Тайнике» после" value={fmtDate(t.rest.stash_after) || '—'} />
        </ul>
      </Block>
      {t.model === 'v1' && (
        <div className="px-4 pt-3">
          <Notice kind="info">Локальный v1 — для сравнения; рекомендации считают по v2.</Notice>
        </div>
      )}
      <p className="px-4 pt-3 text-xs text-muted">
        Ключ: <span className="break-all font-mono">{t.key}</span>
      </p>
    </>
  );
}

function ContributionRow({ c }: { c: Contribution }) {
  const zero = c.side === '0' || c.value === 0;
  const sign = c.side === 'N' ? '−' : '+';
  const context = c.context ? sourceLabel(c.context.split(':')[0]) + (c.context.includes(':') ? ` ${c.context.split(':').slice(1).join(':')}` : '') : null;
  return (
    <li className={cx('py-1.5 text-sm', zero && 'text-dim')}>
      <div className="flex items-baseline gap-2">
        <span className="min-w-0 flex-1 truncate">{contributionLabel(c.kind, c.position_s)}</span>
        <span className={cx('shrink-0 text-right text-xs tabular-nums', !zero && c.side === 'N' && 'text-danger')}>
          {zero ? (
            '0'
          ) : (
            <>
              {fmtDec(Math.abs(c.base))} × {fmtDec(c.w)} = {sign}
              {fmtDec(c.value)}
              {c.side === 'N' ? ' (N)' : ''}
            </>
          )}
        </span>
      </div>
      <div className="truncate text-xs text-muted">{[c.ts ? fmtStamp(c.ts) : null, context, c.client, c.note].filter(Boolean).join(' · ')}</div>
    </li>
  );
}

import { insightsApi, type GeneratorStats, type ListeningOut } from '../../api/insights';
import { BarsByDay, type DayBar } from '../../components/charts/BarsByDay';
import { Heatmap } from '../../components/charts/Heatmap';
import { SOURCE_GROUPS, seriesColor, sourceGroup } from '../../components/charts/palette';
import { ShareBar } from '../../components/charts/ShareBar';
import { ErrorState, Loading, Notice } from '../../components/ui';
import { fmtDay, fmtListened, fmtNum, fmtPct, fmtStamp, fmtWhen, plural, seedKindLabel, sourceLabel, waveOriginLabel } from '../../lib/format';
import { useAsync } from '../../lib/hooks';
import { periodOf, periodRange, trackLink } from '../../lib/insights';
import { navigate, useRoute } from '../../lib/router';
import { Block, Columns, Line, PeriodPicker, ScrollTable, ShowAll, Tile, Tiles, trackName } from './common';

/** Shares of a code → count map, colored by rank. */
export function codeShares(map: Record<string, number>, label: (code: string) => string) {
  const entries = Object.entries(map).sort((a, b) => b[1] - a[1]);
  return entries.map(([code, value], i) => ({ id: code, label: label(code), value, color: seriesColor(i, entries.length) }));
}

/** «Своя 5 · по треку 4» */
export const codeLine = (map: Record<string, number>, label: (code: string) => string) =>
  Object.entries(map).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${label(k)} ${v}`).join(' · ') || '—';

export function ListeningTab({ userId }: { userId: number }) {
  const p = periodOf(useRoute().query);
  const res = useAsync((s) => insightsApi.listening(userId, periodRange(p), s), [userId, p]);
  return (
    <>
      <PeriodPicker />
      {res.loading ? <Loading /> : res.error || !res.data ? <ErrorState text={res.error ?? 'Нет данных'} onRetry={() => res.reload()} /> : <Listening d={res.data} userId={userId} />}
    </>
  );
}

function Listening({ d, userId }: { d: ListeningOut; userId: number }) {
  const t = d.totals;
  const mix = d.recommendations.mix;
  const wave = d.recommendations.wave;
  const q = d.quality;

  const bars: DayBar[] = d.by_day.map((day) => {
    const values: Record<string, number> = {};
    for (const [src, n] of Object.entries(day.by_source)) values[sourceGroup(src)] = (values[sourceGroup(src)] ?? 0) + n;
    return { date: day.date, values };
  });
  const sources = [...d.by_source].sort((a, b) => b.heard - a.heard);

  return (
    <>
      <Tiles>
        <Tile value={fmtNum(t.counted_listens)} label="прослушиваний" hint={`по событиям: ${t.heard_events}`} />
        <Tile value={fmtListened(t.listened_ms)} label="время" />
        <Tile value={fmtPct(t.finished, t.starts)} label="дослушано" />
        <Tile value={fmtPct(t.early_skips, t.starts)} label="ранних пропусков" />
        <Tile value={fmtNum(t.new_tracks)} label="новых песен" />
        <Tile value={fmtNum(t.active_days)} label="активных дней" />
      </Tiles>
      <p className="px-4 pt-1.5 text-xs text-muted">
        По событиям (дослушал или пропустил после 30 с): {fmtNum(t.heard_events)} · начато {fmtNum(t.starts)} · песен {fmtNum(t.unique_tracks)},
        исполнителей {fmtNum(t.unique_artists)} ({fmtNum(t.new_artists)} новых)
      </p>

      <Columns>
        <Block title="По дням">
          <BarsByDay days={bars} series={SOURCE_GROUPS.map((g) => ({ id: g.id, label: g.label, color: g.color }))} />
        </Block>
        <Block title="Источники">
          <ShareBar parts={sources.map((s, i) => ({ id: s.source, label: sourceLabel(s.source), value: s.heard, color: seriesColor(i, sources.length) }))} />
          <div className="mt-3">
            <ScrollTable
              rows={sources}
              rowKey={(r) => r.source}
              cols={[
                { label: 'Источник', cell: (r) => sourceLabel(r.source) },
                { label: 'Запуски', cell: (r) => fmtNum(r.launches), align: 'right' },
                { label: 'Прослуш.', cell: (r) => fmtNum(r.heard), align: 'right' },
                { label: 'Дослуш.', cell: (r) => fmtPct(r.finished, r.starts), align: 'right' },
                { label: 'Ранн. проп.', cell: (r) => fmtPct(r.early_skips, r.starts), align: 'right' },
              ]}
            />
          </div>
        </Block>
      </Columns>

      <Block title="Рекомендации">
        <div className="space-y-3 text-sm">
          <div>
            <div className="font-semibold">Микс дня</div>
            <p className="text-muted">
              собрано {fmtNum(mix.built)}, запускал {fmtNum(mix.launched_mixes)} {plural(mix.launched_mixes, ['микс', 'микса', 'миксов'])}, {fmtNum(mix.launches)}{' '}
              {plural(mix.launches, ['запуск', 'запуска', 'запусков'])}, {fmtNum(mix.heard)} прослушано, прижилось {fmtNum(mix.adopted)}
            </p>
            <p className="text-xs text-muted">
              знакомое: начато {fmtNum(mix.familiar.started)}, прослушано {fmtNum(mix.familiar.heard)} · новое: начато {fmtNum(mix.new.started)}, прослушано{' '}
              {fmtNum(mix.new.heard)} · ♥ {mix.likes} · 👎 {mix.dislikes}
            </p>
          </div>
          <div>
            <div className="font-semibold">Моя волна</div>
            <p className="text-muted">
              {fmtNum(wave.launches)} {plural(wave.launches, ['запуск', 'запуска', 'запусков'])}
              {wave.restarts ? ` (+${wave.restarts} ${plural(wave.restarts, ['перезапуск', 'перезапуска', 'перезапусков'])})` : ''}: {codeLine(wave.by_origin, waveOriginLabel)}
            </p>
            <p className="text-xs text-muted">
              сиды: {codeLine(wave.by_seed_kind, seedKindLabel)} · прослушано {fmtNum(wave.heard)} · прижилось {fmtNum(wave.adopted)} · ♥ {wave.likes} · 👎 {wave.dislikes}
            </p>
          </div>
        </div>
      </Block>

      <Block title="Генераторы (микс и волна)">
        <ScrollTable<GeneratorStats & { list: string }>
          rows={[...mix.by_generator.map((g) => ({ ...g, list: 'Микс' })), ...wave.by_generator.map((g) => ({ ...g, list: 'Волна' }))]}
          rowKey={(r) => `${r.list}:${r.generator}`}
          cols={[
            { label: 'Генератор', cell: (r) => r.title || r.generator },
            { label: 'Где', cell: (r) => r.list },
            { label: 'Предложено', cell: (r) => fmtNum(r.offered), align: 'right' },
            { label: 'Начато', cell: (r) => fmtNum(r.started), align: 'right' },
            { label: 'Прослуш.', cell: (r) => fmtNum(r.heard), align: 'right' },
            { label: 'Ранн. проп.', cell: (r) => fmtNum(r.early_skips), align: 'right' },
            { label: '♥', cell: (r) => fmtNum(r.liked), align: 'right' },
          ]}
        />
      </Block>

      <Block title="Когда слушает">
        <Heatmap rows={d.heatmap} />
      </Block>

      <Columns>
        <Block title="Топ песен">
          <ShowAll
            items={d.top_tracks}
            render={(x, i) => (
              <Line key={x.key ?? i} title={trackName(x)} sub={fmtListened(x.listened_ms)} value={fmtNum(x.heard)} onClick={x.key ? () => navigate(trackLink(userId, x.key!)) : undefined} />
            )}
          />
        </Block>
        <Block title="Топ исполнителей">
          <ShowAll items={d.top_artists} render={(x) => <Line key={x.artist} title={x.artist} sub={`${x.tracks} ${plural(x.tracks, ['песня', 'песни', 'песен'])}`} value={fmtNum(x.heard)} />} />
        </Block>
      </Columns>

      <Block title="Открытия" right={<span className="text-xs text-muted">впервые за всю историю</span>}>
        <ShowAll
          items={d.discoveries}
          render={(x, i) => (
            <Line key={x.key ?? i} title={trackName(x)} sub={`${fmtDay(x.first_heard_at)} · ${sourceLabel(x.source)}`} onClick={x.key ? () => navigate(trackLink(userId, x.key!)) : undefined} />
          )}
        />
      </Block>

      <Columns>
        <Block title="♥ и 👎 за период">
          <ul className="divide-y divide-line/50">
            <Line title="♥ поставлено" value={fmtNum(t.likes)} />
            <Line title="♥ снято" value={fmtNum(t.unlikes)} />
            <Line title="👎 трекам" value={fmtNum(t.dislikes_track)} />
            <Line title="👎 исполнителям" value={fmtNum(t.dislikes_artist)} />
            <Line title="Скачано" value={fmtNum(t.downloads)} />
          </ul>
        </Block>
        <Block title="Клиенты и устройства">
          <ShowAll
            items={d.clients}
            render={(c) => (
              <Line
                key={c.client}
                title={c.client}
                sub={`${c.devices} ${plural(c.devices, ['устройство', 'устройства', 'устройств'])} · последний раз ${fmtWhen(c.last_at) || '—'}`}
                value={`${fmtNum(c.heard)} / ${fmtNum(c.starts)}`}
              />
            )}
          />
          <p className="mt-1 text-xs text-muted">справа — прослушано / начато</p>
        </Block>
      </Columns>

      <Block title="Качество данных">
        <div className="space-y-2">
          {q.inferred_launches > 0 && (
            <Notice>
              {q.inferred_launches} {plural(q.inferred_launches, ['запуск определён', 'запуска определены', 'запусков определены'])} приблизительно (старые события без id запуска).
            </Notice>
          )}
          {!!q.inferred_playlist_mixes && <Notice>{q.inferred_playlist_mixes} запусков плейлиста отнесены к миксу приблизительно (по составу песен).</Notice>}
          {q.reconstructed_waves > 0 && (
            <Notice>
              {q.reconstructed_waves} {plural(q.reconstructed_waves, ['сессия волны восстановлена', 'сессии волны восстановлены', 'сессий волны восстановлены'])} по событиям — с чего запускали,
              неизвестно.
            </Notice>
          )}
          <ul className="divide-y divide-line/50 text-sm">
            <Line title="События без контекста" value={fmtNum(q.events_without_context)} />
            <Line title="События без id попытки (старые клиенты)" value={fmtNum(q.events_without_playback)} />
            <Line title="Из истории Navidrome" value={fmtNum(q.scrobbles)} />
            <Line title="Старые «дослушал» без замера" value={fmtNum(q.legacy_finishes)} />
            <Line title="Первое событие" value={fmtStamp(q.first_event_at)} />
          </ul>
        </div>
      </Block>
    </>
  );
}

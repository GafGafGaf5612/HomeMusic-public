import { insightsApi, type OverviewRow } from '../../api/insights';
import { BarsByDay, type DayBar } from '../../components/charts/BarsByDay';
import { seriesColor } from '../../components/charts/palette';
import { ErrorState, IconButton, Loading, Notice, PageHeader } from '../../components/ui';
import { fmtListened, fmtNum, fmtWhen } from '../../lib/format';
import { useAsync } from '../../lib/hooks';
import { periodOf, periodRange } from '../../lib/insights';
import { navigate, useRoute } from '../../lib/router';
import { Block, PeriodPicker, ScrollTable, Tile, Tiles } from './common';

/** `/admin/insights` — all users: totals, listens by day stacked by user, a row per user. */
export function InsightsOverviewPage() {
  const p = periodOf(useRoute().query);
  const range = periodRange(p);
  const res = useAsync((s) => insightsApi.overview(range, s), [p]);
  const users = res.data?.users ?? [];
  // the overview has no days: the per-user listening of the same period gives them
  const days = useAsync(
    (s) => Promise.all(users.filter((u) => !u.user.excluded).map((u) => insightsApi.listening(u.user.id, range, s).then((x) => ({ id: u.user.id, x })))),
    [res.data],
    !!res.data,
  );
  const d = res.data;
  const t = d?.totals ?? {};

  const counted = users.filter((u) => !u.user.excluded);
  const series = counted.map((u, i) => ({ id: String(u.user.id), label: u.user.username, color: seriesColor(i, counted.length) }));
  const bars: DayBar[] = [];
  if (days.data) {
    const byDate = new Map<string, Record<string, number>>();
    for (const { id, x } of days.data) for (const day of x.by_day) byDate.set(day.date, { ...byDate.get(day.date), [String(id)]: day.heard });
    for (const [date, values] of [...byDate.entries()].sort()) bars.push({ date, values });
  }

  return (
    <div className="pb-6">
      <PageHeader title="Аналитика пользователей" back="/admin" right={<IconButton icon="refresh" label="Обновить" onClick={() => res.reload()} />} />
      <PeriodPicker />
      {res.loading ? (
        <Loading />
      ) : res.error || !d ? (
        <ErrorState text={res.error ?? 'Нет данных'} onRetry={() => res.reload()} />
      ) : (
        <>
          <Tiles>
            <Tile value={fmtNum(t.counted_listens)} label="прослушиваний" />
            <Tile value={fmtListened(t.listened_ms)} label="время" />
            <Tile value={fmtNum(t.mix_launches)} label="запусков микса" />
            <Tile value={fmtNum(t.wave_launches)} label={t.wave_restarts ? `запусков волны (+${t.wave_restarts} перезап.)` : 'запусков волны'} />
            <Tile value={fmtNum(t.new_tracks)} label="новых песен" />
            <Tile value={fmtNum(t.likes)} label="♥ поставлено" />
          </Tiles>

          <Block title="Прослушивания по дням">
            {days.loading ? <Loading /> : days.error ? <ErrorState text={days.error} /> : <BarsByDay days={bars} series={series} />}
          </Block>

          <Block title="По пользователям">
            <ScrollTable<OverviewRow>
              rows={users}
              rowKey={(r) => r.user.id}
              onRow={(r) => navigate(`/admin/user/${r.user.id}?tab=listening${p !== '30' ? `&p=${p}` : ''}`)}
              cols={[
                { label: 'Имя', cell: (r) => (r.user.excluded ? `${r.user.username} (вне итогов)` : r.user.username) },
                { label: 'Прослуш.', cell: (r) => fmtNum(r.counted_listens), align: 'right' },
                { label: 'Время', cell: (r) => fmtListened(r.listened_ms), align: 'right' },
                { label: 'Микс', cell: (r) => fmtNum(r.mix_launches), align: 'right' },
                { label: 'Волна', cell: (r) => (r.wave_restarts ? `${r.wave_launches} (+${r.wave_restarts})` : fmtNum(r.wave_launches)), align: 'right' },
                { label: '♥', cell: (r) => fmtNum(r.likes), align: 'right' },
                { label: '👎', cell: (r) => fmtNum(r.dislikes), align: 'right' },
                { label: 'Новые', cell: (r) => fmtNum(r.new_tracks), align: 'right' },
                { label: 'Активн. дн.', cell: (r) => fmtNum(r.active_days), align: 'right' },
                { label: 'Версия', cell: (r) => r.app_version ?? '—' },
                { label: 'Активность', cell: (r) => fmtWhen(r.last_activity_at) || '—' },
              ]}
            />
            <p className="mt-2 text-xs text-muted">Тап по строке — подробно о пользователе. Прослушивание засчитано от 30 секунд.</p>
          </Block>
          {users.some((u) => u.user.excluded) && (
            <div className="px-4 pt-3">
              <Notice kind="info">Пользователи из `analytics.exclude_users` показаны, но в общие итоги и график не входят.</Notice>
            </div>
          )}
        </>
      )}
    </div>
  );
}

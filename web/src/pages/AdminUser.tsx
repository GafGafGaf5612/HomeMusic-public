import type { ReactNode } from 'react';
import { api } from '../api/client';
import type { AdminUserDetailOut } from '../api/types';
import { ErrorState, Loading, PageHeader, SectionTitle, Segmented } from '../components/ui';
import { setQuery } from '../lib/insights';
import { useRoute } from '../lib/router';
import { ListeningTab } from './insights/Listening';
import { MapTab } from './insights/MapTab';
import { MixesTab, WavesTab } from './insights/Recs';
import { TasteTab } from './insights/Taste';
import { downloadStatusLabel, fmtDate, fmtUsd, fmtWhen, inputKindLabel, plural } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { Stat, UserBadges } from './Admin';

const MONTHS = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];

function monthLabel(m: string): string {
  const [y, mm] = m.split('-').map(Number);
  return `${MONTHS[mm - 1] ?? m} ${y}`;
}

const TABS = [
  { value: 'overview', label: 'Обзор' },
  { value: 'listening', label: 'Прослушивания' },
  { value: 'taste', label: 'Вкус' },
  { value: 'map', label: 'Карта' },
  { value: 'mixes', label: 'Миксы' },
  { value: 'waves', label: 'Волна' },
] as const;

type TabKey = (typeof TABS)[number]['value'];

const tabOf = (v: string | null): TabKey => TABS.find((t) => t.value === v)?.value ?? 'overview';

/** `/admin/user/:id?tab=…` — the user with tabs; the tab and its filters live in the address. */
export function AdminUserPage({ id }: { id: number }) {
  const res = useAsync((s) => api.adminUser(id, s), [id]);
  const u = res.data;
  const tab = tabOf(useRoute().query.get('tab'));

  return (
    <div className="pb-6">
      <PageHeader title={u?.username ?? 'Пользователь'} back="/admin" sub={u?.name && u.name !== u.username ? u.name : undefined} />
      <div className="px-4 pb-3">
        <Segmented<TabKey> label="Разделы" value={tab} options={[...TABS]} onChange={(v) => setQuery({ tab: v === 'overview' ? null : v })} />
      </div>
      {tab === 'listening' ? (
        <ListeningTab userId={id} />
      ) : tab === 'taste' ? (
        <TasteTab userId={id} />
      ) : tab === 'map' ? (
        <MapTab userId={id} />
      ) : tab === 'mixes' ? (
        <MixesTab userId={id} />
      ) : tab === 'waves' ? (
        <WavesTab userId={id} />
      ) : res.loading ? (
        <Loading />
      ) : res.error || !u ? (
        <ErrorState text={res.error ?? 'Не найдено'} onRetry={() => res.reload()} />
      ) : (
        <OverviewTab u={u} />
      )}
    </div>
  );
}

function OverviewTab({ u }: { u: AdminUserDetailOut }) {
  return (
    <>
      <div className="flex flex-wrap gap-1.5 px-4 pb-3">
        <UserBadges u={u} />
      </div>

      <div className="grid grid-cols-3 gap-2 px-4">
        <Stat value={fmtUsd(u.ai_cost_30d_usd)} label="ИИ за 30 дней" />
        <Stat value={fmtUsd(u.ai_cost_usd)} label="ИИ всего" />
        <Stat value={String(u.ai_requests)} label={plural(u.ai_requests, ['запрос к ИИ', 'запроса к ИИ', 'запросов к ИИ'])} />
        <Stat value={String(u.downloads_done)} label="скачано" />
        <Stat value={String(u.tracks_added)} label="в библиотеке" />
        <Stat value={String(u.uploads)} label="своих файлов" />
        <Stat value={u.starred_songs == null ? '—' : String(u.starred_songs)} label="♥ понравилось" />
        <Stat value={u.playlists == null ? '—' : String(u.playlists)} label="плейлистов" />
        {u.downloads_active > 0
          ? <Stat value={String(u.downloads_active)} label={u.downloads_failed ? `качается, ${u.downloads_failed} с ошибкой` : 'качается'} />
          : <Stat value={String(u.downloads_failed)} label="не скачалось" />}
      </div>

      <SectionTitle>Аккаунт</SectionTitle>
      <dl className="mx-4 overflow-hidden rounded-xl bg-surface text-sm">
        <Row k="Роль" v={u.is_admin ? 'Администратор' : 'Пользователь'} />
        <Row k="Приглашение" v={u.invite_note ?? (u.email ? 'нет, по почте' : '—')} />
        {u.email && <Row k="Почта" v={u.email} />}
        <Row k="Аккаунт в Хабе с" v={fmtDate(u.created_at)} />
        <Row k="Последний раз в Хабе" v={fmtWhen(u.last_seen_at) || '—'} />
        <Row k="Последний раз в плеере" v={fmtWhen(u.player_seen_at) || '—'} />
        <Row k="Google" v={u.google_email || 'не привязан'} />
        <Row k="Последний запрос к ИИ" v={fmtWhen(u.ai_last_at) || '—'} />
      </dl>

      {u.ai_months.length > 0 && (
        <>
          <SectionTitle>ИИ по месяцам</SectionTitle>
          <dl className="mx-4 overflow-hidden rounded-xl bg-surface text-sm">
            {u.ai_months.map((m) => (
              <Row key={m.month} k={monthLabel(m.month)} v={`${fmtUsd(m.cost_usd)} · ${m.requests} ${plural(m.requests, ['запрос', 'запроса', 'запросов'])}`} />
            ))}
          </dl>
        </>
      )}

      {u.ai_recent.length > 0 && (
        <>
          <SectionTitle>Последние запросы к ИИ</SectionTitle>
          <ul className="mx-4 overflow-hidden rounded-xl bg-surface text-sm">
            {u.ai_recent.map((r, i) => (
              <li key={i} className="flex min-h-12 items-center gap-3 border-b border-line/60 px-4 py-2 last:border-0">
                <span className="min-w-0 flex-1">
                  <span className="block">{inputKindLabel(r.kind)}</span>
                  <span className="block text-xs text-muted">
                    {fmtWhen(r.created_at)} · {(r.tokens_in + r.tokens_out).toLocaleString('ru-RU')} токенов
                  </span>
                </span>
                <span className="shrink-0 font-semibold">{fmtUsd(r.cost_usd)}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {u.recent_downloads.length > 0 && (
        <>
          <SectionTitle>Последние загрузки</SectionTitle>
          <ul className="mx-4 overflow-hidden rounded-xl bg-surface text-sm">
            {u.recent_downloads.map((d, i) => (
              <li key={i} className="flex min-h-12 items-center gap-3 border-b border-line/60 px-4 py-2 last:border-0">
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{d.title || '—'}</span>
                  <span className="block truncate text-xs text-muted">
                    {d.artist} · {fmtWhen(d.created_at)}
                  </span>
                </span>
                <span className="shrink-0 text-xs text-muted">{downloadStatusLabel(d.status)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

function Row({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex min-h-11 items-center gap-3 border-b border-line/60 px-4 py-2 last:border-0">
      <dt className="flex-1 text-muted">{k}</dt>
      <dd className="min-w-0 truncate text-right">{v}</dd>
    </div>
  );
}

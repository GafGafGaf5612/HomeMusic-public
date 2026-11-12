// Releases from rutracker (README, «Раздачи с rutracker»): search through Jackett, pick files, watch qBittorrent.
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { api, errorMessage } from '../api/client';
import type { TorrentFileOut, TorrentOut, TorrentReleaseOut, TorrentStatus } from '../api/types';
import { Icon } from '../components/Icon';
import { Button, Checkbox, Empty, ErrorState, IconButton, Loading, Notice, PageHeader, ProgressBar, Segmented, Sheet, Spinner, cx } from '../components/ui';
import { useHubEvent } from '../lib/events';
import { listen, useListen, type ListenTrack } from '../lib/listen';
import { fmtSize } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { Link, navigate } from '../lib/router';
import { attempt, confirmDialog, toast } from '../lib/ui';

type Tab = 'search' | 'list';

export function TorrentsPage({ initialQuery, albumRef, initialTab }: { initialQuery: string; albumRef?: string; initialTab?: Tab }) {
  const [tab, setTab] = useState<Tab>(initialTab ?? (initialQuery ? 'search' : 'list'));
  const config = useAsync((s) => api.torrentConfig(s), []);

  if (config.loading && !config.data) return <Loading />;
  if (config.data && !config.data.enabled)
    return (
      <>
        <PageHeader title="Раздачи" back="/more" />
        <Empty icon="disc" title="Раздачи не настроены" text="Нужны Jackett и qBittorrent: README, «Раздачи с rutracker»." />
      </>
    );

  return (
    <div>
      <PageHeader title="Раздачи" back="/more" sub={config.data?.sub || 'Jackett → qBittorrent'} />
      <div className="px-4 pb-3">
        <Segmented
          label="Раздел"
          value={tab}
          onChange={setTab}
          options={[
            { value: 'search', label: 'Поиск' },
            { value: 'list', label: 'Загрузки' },
          ]}
        />
      </div>
      {tab === 'search' ? <SearchTab initialQuery={initialQuery} albumRef={albumRef} onStarted={() => setTab('list')} /> : <ListTab />}
    </div>
  );
}

// --- search ------------------------------------------------------------------------------------------

function SearchTab({ initialQuery, albumRef, onStarted }: { initialQuery: string; albumRef?: string; onStarted: () => void }) {
  const [q, setQ] = useState(initialQuery);
  const [query, setQuery] = useState(initialQuery);
  const [picked, setPicked] = useState<TorrentReleaseOut | null>(null);
  const res = useAsync((s) => api.torrentSearch(query, s), [query], !!query);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const v = q.trim();
    if (!v) return;
    setQuery(v);
    navigate(`/torrents?q=${encodeURIComponent(v)}${albumRef ? `&album=${encodeURIComponent(albumRef)}` : ''}`, { replace: true });
  };

  return (
    <>
      <form onSubmit={submit} className="px-4 pb-2">
        <div className="relative">
          <Icon name="search" size={18} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-dim" />
          <input className="input pl-10" type="search" placeholder="Исполнитель и альбом" aria-label="Поиск раздач" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {albumRef && <p className="px-1 pt-1.5 text-xs text-muted">Теги возьмутся из альбома каталога, с которого начат поиск</p>}
      </form>

      {!query ? (
        <Empty icon="search" title="Найдите альбом или дискографию" text="Например: «Кино Группа крови»" />
      ) : res.loading && !res.data ? (
        <Loading text="Ищем на трекере…" />
      ) : res.error ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : (
        <div className="pb-4">
          {res.data?.errors.map((e) => (
            <div key={e} className="px-4 pb-2">
              <Notice kind="error">{e}</Notice>
            </div>
          ))}
          {res.data && !res.data.releases.length && !res.data.errors.length && <Empty icon="search" title="Ничего не нашлось" />}
          <ul>
            {res.data?.releases.map((r) => (
              <ReleaseRow key={r.link} r={r} onPick={() => setPicked(r)} />
            ))}
          </ul>
        </div>
      )}

      {picked && <FilesSheet release={picked} albumRef={albumRef} onClose={() => setPicked(null)} onStarted={onStarted} />}
    </>
  );
}

function ReleaseRow({ r, onPick }: { r: TorrentReleaseOut; onPick: () => void }) {
  const badges = [
    r.codec,
    r.bitrate_kbps ? `${r.bitrate_kbps} kbps` : null,
    r.hires ? 'Hi-Res' : null,
    r.layout === 'image' ? 'образ + .cue' : null,
    r.discography ? 'дискография' : null,
    r.magnet ? 'magnet' : null,
  ].filter(Boolean);
  return (
    <li className="border-b border-line/50">
      <button type="button" onClick={onPick} className={cx('flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-fg/5', r.importable === false && 'opacity-60')}>
        <div className="min-w-0 flex-1">
          <div className="line-clamp-3 text-sm font-medium leading-snug">{r.title}</div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">
            {badges.map((b) => (
              <span key={b} className={cx('rounded px-1.5 py-0.5', r.lossless && b === r.codec ? 'bg-accent/15 text-accent' : 'bg-fg/8')}>
                {b}
              </span>
            ))}
            {r.tracker && <span className="text-dim">{r.tracker}</span>}
            {r.size ? <span>{fmtSize(r.size)}</span> : null}
            {r.published && <span>{new Date(r.published).toLocaleDateString('ru-RU')}</span>}
          </div>
          {r.importable === false && <div className="mt-1 text-xs text-warn">Библиотека такую раздачу пока не примет</div>}
        </div>
        <div className="shrink-0 text-right text-xs">
          <div className={cx('font-semibold', (r.seeders ?? 0) > 0 ? 'text-accent' : 'text-danger')}>↑ {r.seeders ?? '—'}</div>
          <div className="text-dim">↓ {r.leechers ?? '—'}</div>
        </div>
      </button>
    </li>
  );
}

function FilesSheet({ release, albumRef, onClose, onStarted }: { release: TorrentReleaseOut; albumRef?: string; onClose: () => void; onStarted: () => void }) {
  const res = useAsync((s) => api.torrentFiles(release.link, s, release), [release.link]);
  const playing = useListen((s) => s.track?.ref ?? null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);

  const pending = !!res.data?.pending;
  // a magnet's file list comes from peers: ask again until it does (the Hub waits ~8 s per request)
  useEffect(() => {
    if (!pending || res.loading) return;
    const t = window.setTimeout(() => res.reload(true), 2000);
    return () => window.clearTimeout(t);
  }, [pending, res]);

  const audio = useMemo(() => (res.data?.files ?? []).filter((f) => f.kind === 'audio'), [res.data]);
  const importable = useMemo(() => audio.filter((f) => f.importable), [audio]);
  // the release's tracks as a queue: a tap plays from that track on, qBittorrent downloads each in order
  const playable = useMemo<ListenTrack[]>(
    () => audio.filter((f) => f.ref).map((f) => ({ ref: f.ref!, title: f.title || f.path.split('/').pop()!, artist: f.artist || '', album: res.data?.name })),
    [audio, res.data],
  );
  const play = (ref: string) => {
    const i = playable.findIndex((t) => t.ref === ref);
    if (i >= 0) listen(playable[i], playable, 'torrent');
  };
  useEffect(() => setChosen(new Set(importable.map((f) => f.path))), [importable]);

  const size = importable.filter((f) => chosen.has(f.path)).reduce((s, f) => s + f.size, 0);
  const toggle = (f: TorrentFileOut, on: boolean) =>
    setChosen((prev) => {
      const next = new Set(prev);
      if (on) next.add(f.path);
      else next.delete(f.path);
      return next;
    });

  const start = async () => {
    setBusy(true);
    const all = chosen.size === importable.length;
    const r = await attempt(() =>
      api.startTorrent({ link: release.link, title: release.title, details_url: release.details_url, album_ref: albumRef ?? null, files: all ? null : [...chosen] }),
    );
    setBusy(false);
    if (!r) return;
    toast('Раздача качается: после скачивания треки появятся в библиотеке', 'success');
    onClose();
    onStarted();
  };

  const footer = (
    <Button variant="primary" icon="download" block busy={busy} disabled={!chosen.size} onClick={start}>
      {chosen.size ? `Скачать ${chosen.size === importable.length ? 'всё' : `${chosen.size} из ${importable.length}`} · ${fmtSize(size)}` : 'Выберите файлы'}
    </Button>
  );

  return (
    <Sheet open onClose={onClose} title="Файлы раздачи" footer={importable.length ? footer : undefined}>
      <p className="pb-2 text-sm text-muted">{release.title}</p>
      {playable.length > 0 && (
        <p className="pb-2 text-xs text-muted">▶ — послушать до скачивания: qBittorrent качает трек по порядку, звук идёт по мере загрузки.</p>
      )}
      {release.details_url && (
        <a href={release.details_url} target="_blank" rel="noopener noreferrer" className="mb-3 inline-flex items-center gap-1 text-sm text-accent hover:underline">
          Страница раздачи <Icon name="external" size={14} />
        </a>
      )}
      {pending ? (
        <div className="flex flex-col items-center gap-3 py-8 text-center text-sm text-muted">
          <Spinner />
          <div>
            Получаем список файлов от пиров…
            <br />
            {release.seeders === 0 && !res.data?.seeds
              ? 'У раздачи нет сидов: список может так и не прийти.'
              : `Сидов в сети: ${res.data?.seeds ?? release.seeders ?? '—'}. Обычно это секунды или минуты.`}
          </div>
        </div>
      ) : res.loading && !res.data ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : res.error ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : (
        <>
          {!importable.length && (
            <Notice kind="warn">
              {audio.some((f) => f.image)
                ? 'Здесь образ диска + .cue: нарезка на треки пока не поддерживается.'
                : 'В раздаче нет файлов, которые принимает библиотека (mp3, m4a, flac, ogg, opus, wav).'}
            </Notice>
          )}
          {importable.length > 1 && (
            <div className="flex gap-2 pb-2">
              <Button small onClick={() => setChosen(new Set(importable.map((f) => f.path)))}>
                Все
              </Button>
              <Button small onClick={() => setChosen(new Set())}>
                Ни одного
              </Button>
            </div>
          )}
          <ul>
            {audio.map((f) => (
              <li key={f.path} className="flex items-center gap-1 border-b border-line/40">
                <Checkbox checked={chosen.has(f.path)} disabled={!f.importable} onChange={(on) => toggle(f, on)} label={f.path} />
                {f.ref ? (
                  <IconButton icon={playing === f.ref ? 'volume' : 'play'} label="Слушать" size={18} onClick={() => play(f.ref!)} className={playing === f.ref ? '!text-accent' : ''} />
                ) : (
                  <span className="w-2" />
                )}
                <div className="min-w-0 flex-1 py-2">
                  <div className={cx('truncate text-sm', !f.importable && 'text-dim')}>{f.path.split('/').pop()}</div>
                  <div className="truncate text-xs text-dim">
                    {f.path.split('/').slice(1, -1).join(' / ') || res.data?.name} · {fmtSize(f.size)}
                    {f.image ? ' · образ' : !f.importable ? ' · формат не поддерживается' : ''}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </Sheet>
  );
}

// --- downloads ---------------------------------------------------------------------------------------

const STATUS: Record<TorrentStatus, string> = {
  downloading: 'Качается',
  importing: 'Импорт',
  done: 'В библиотеке',
  failed: 'Ошибка',
  cancelled: 'Отменено',
};

function ListTab() {
  const res = useAsync((s) => api.torrents(s), []);
  useHubEvent('torrent', (t) =>
    res.setData((prev) => {
      if (!prev) return prev;
      const i = prev.findIndex((x) => x.id === t.id);
      return i < 0 ? [t, ...prev] : prev.map((x) => (x.id === t.id ? t : x));
    }),
  );
  useHubEvent('reconnect', () => res.reload());

  if (res.loading && !res.data) return <Loading />;
  if (res.error) return <ErrorState text={res.error} onRetry={() => res.reload()} />;
  if (!res.data?.length) return <Empty icon="download" title="Загрузок нет" text="Найдите раздачу во вкладке «Поиск»" />;
  return (
    <ul className="pb-4">
      {res.data.map((t) => (
        <TorrentRow key={t.id} t={t} onRemoved={(id) => res.setData((prev) => prev?.filter((x) => x.id !== id))} />
      ))}
    </ul>
  );
}

function TorrentRow({ t, onRemoved }: { t: TorrentOut; onRemoved: (id: number) => void }) {
  const [busy, setBusy] = useState(false);
  const active = t.status === 'downloading' || t.status === 'importing';
  const seeding = t.status === 'done';

  const remove = async () => {
    const ok = await confirmDialog({
      title: active ? 'Остановить загрузку?' : seeding ? 'Перестать раздавать?' : 'Убрать из qBittorrent?',
      body: seeding ? 'Торрент и его файлы в staging/torrents удалятся. Треки в библиотеке останутся.' : 'Скачанные файлы раздачи удалятся.',
      okLabel: active ? 'Остановить' : 'Удалить',
    });
    if (!ok) return;
    setBusy(true);
    try {
      await api.deleteTorrent(t.id);
      if (!active) onRemoved(t.id);
    } catch (e) {
      toast(errorMessage(e), 'error');
    } finally {
      setBusy(false);
    }
  };

  const speed = t.speed ? `${fmtSize(t.speed)}/с` : null;
  const eta = t.eta_s != null ? (t.eta_s < 3600 ? `${Math.max(1, Math.round(t.eta_s / 60))} мин` : `${Math.round(t.eta_s / 360) / 10} ч`) : null;
  return (
    <li className="border-b border-line/50 px-4 py-3">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="line-clamp-2 text-sm font-medium leading-snug">{t.title}</div>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted">
            <span className={cx('font-medium', t.status === 'failed' ? 'text-danger' : t.status === 'done' ? 'text-accent' : '')}>{STATUS[t.status]}</span>
            <span>{fmtSize(t.size)}</span>
            {t.status === 'downloading' && (
              <>
                <span>{Math.floor(t.progress * 100)}%</span>
                {speed && <span>{speed}</span>}
                {eta && <span>≈ {eta}</span>}
                <span>сидов: {t.seeds ?? 0}</span>
              </>
            )}
            {t.status === 'done' && <span>импортировано {t.imported} из {t.files}</span>}
          </div>
          {t.status === 'downloading' && <ProgressBar value={t.progress} className="mt-2" />}
          {t.error && <div className="mt-1 text-xs text-danger">{t.error}</div>}
          {t.warnings.length > 0 && <div className="mt-1 line-clamp-2 text-xs text-warn">{t.warnings.join('; ')}</div>}
          {t.album_id && (
            <Link to={`/library/album/${t.album_id}`} className="mt-1 inline-block text-xs text-accent hover:underline">
              Открыть альбом
            </Link>
          )}
        </div>
        {t.status !== 'cancelled' && t.status !== 'importing' && (
          <Button small busy={busy} onClick={remove} aria-label="Удалить">
            <Icon name={active ? 'x' : 'trash'} size={16} />
          </Button>
        )}
      </div>
    </li>
  );
}

export function torrentTab(v: string | null): Tab | undefined {
  return v === 'search' || v === 'list' ? v : undefined;
}

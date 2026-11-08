import { useState, type FormEvent, type ReactNode } from 'react';
import { api } from '../api/client';
import { Icon } from '../components/Icon';
import { Button, Empty, ErrorState, PageHeader, SkeletonRows } from '../components/ui';
import { fmtLength, tracksWord } from '../lib/format';
import { STANDALONE } from '../lib/embedded';
import { useAsync } from '../lib/hooks';
import { Link, enc } from '../lib/router';
import { attempt } from '../lib/ui';

export function PlaylistsPage() {
  const res = useAsync((s) => api.playlists(s), []);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    const p = await attempt(() => api.createPlaylist(name.trim()), 'Плейлист создан');
    setBusy(false);
    if (p) {
      setName('');
      res.setData((prev) => [p, ...(prev ?? [])]);
    }
  };

  return (
    <div>
      <PageHeader title="Плейлисты" back={STANDALONE ? '/library' : '/more'} />
      <form onSubmit={create} className="flex gap-2 px-4 pb-3">
        <input className="input flex-1" placeholder="Название нового плейлиста" aria-label="Название нового плейлиста" value={name} onChange={(e) => setName(e.target.value)} />
        <Button type="submit" variant="primary" icon="plus" busy={busy} disabled={!name.trim()}>
          Создать
        </Button>
      </form>
      <p className="px-4 pb-2 text-xs text-muted">
        {STANDALONE
          ? 'Плейлисты хранятся в Navidrome: те же, что в приложении HomeMusic.'
          : 'Плейлисты хранятся в Navidrome: слушайте и скачивайте их для офлайна в плеере (Tempus).'}
      </p>
      {res.loading ? (
        <SkeletonRows />
      ) : res.error ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : !res.data?.length ? (
        <Empty icon="playlist" title="Плейлистов пока нет" text="Создайте плейлист здесь или добавьте в него альбом со страницы альбома." />
      ) : (
        <ul>
          {res.data.map((p) => (
            <li key={p.id}>
              <PlaylistRow to={STANDALONE ? `/playlist/${enc(p.id)}` : undefined}>
              <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-md bg-gradient-to-br from-[#1f6f43] to-[#1a1a1a] text-fg">
                <Icon name="playlist" size={22} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate">{p.name}</span>
                <span className="block text-xs text-muted">{[tracksWord(p.song_count), p.duration_s ? fmtLength(p.duration_s) : null].filter(Boolean).join(' · ')}</span>
              </span>
              {STANDALONE && <Icon name="chevron" size={18} className="text-dim" />}
              </PlaylistRow>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** A playlist opens (its songs, «Слушать») in a browser; inside the app the app's own library has it. */
function PlaylistRow({ to, children }: { to?: string; children: ReactNode }) {
  const cls = 'flex min-h-16 items-center gap-3 px-4 py-2';
  return to ? (
    <Link to={to} className={cls + ' hover:bg-fg/5'}>
      {children}
    </Link>
  ) : (
    <div className={cls}>{children}</div>
  );
}

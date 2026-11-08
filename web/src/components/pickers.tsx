import { useState } from 'react';
import { api, libraryCoverUrl } from '../api/client';
import type { LibAlbumOut, PlaylistOut } from '../api/types';
import { useAsync, useDebounced } from '../lib/hooks';
import { tracksWord } from '../lib/format';
import { attempt } from '../lib/ui';
import { Icon } from './Icon';
import { Button, Cover, Empty, ErrorState, Field, Loading, Sheet } from './ui';

export type PlaylistPick = { kind: 'existing'; playlist: PlaylistOut } | { kind: 'new'; name: string };

export function PlaylistPicker({
  open,
  onClose,
  onPick,
  title = 'В плейлист',
  defaultName = '',
  busy,
}: {
  open: boolean;
  onClose: () => void;
  onPick: (p: PlaylistPick) => void;
  title?: string;
  defaultName?: string;
  busy?: boolean;
}) {
  const [name, setName] = useState(defaultName);
  const lists = useAsync((s) => api.playlists(s), [], open);
  return (
    <Sheet open={open} onClose={onClose} title={title}>
      <form
        className="flex items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) onPick({ kind: 'new', name: name.trim() });
        }}
      >
        <Field label="Новый плейлист" className="flex-1">
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Название" />
        </Field>
        <Button type="submit" variant="primary" icon="plus" disabled={!name.trim()} busy={busy}>
          Создать
        </Button>
      </form>
      <div className="label mt-5">Существующие</div>
      {lists.loading ? (
        <Loading />
      ) : lists.error ? (
        <ErrorState text={lists.error} onRetry={() => lists.reload()} />
      ) : !lists.data?.length ? (
        <p className="py-6 text-center text-sm text-muted">Плейлистов пока нет</p>
      ) : (
        <ul className="-mx-4">
          {lists.data.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                disabled={busy}
                onClick={() => onPick({ kind: 'existing', playlist: p })}
                className="flex min-h-14 w-full items-center gap-3 px-4 text-left hover:bg-fg/5 disabled:opacity-50"
              >
                <span className="flex h-10 w-10 items-center justify-center rounded-md bg-raised text-muted">
                  <Icon name="playlist" size={20} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{p.name}</span>
                  <span className="block text-xs text-muted">{tracksWord(p.song_count)}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Sheet>
  );
}

export function AlbumPicker({
  open,
  onClose,
  onPick,
  title,
  excludeIds = [],
  allowCreate = true,
  busy,
}: {
  open: boolean;
  onClose: () => void;
  onPick: (a: LibAlbumOut) => void;
  title: string;
  excludeIds?: number[];
  allowCreate?: boolean;
  busy?: boolean;
}) {
  const [q, setQ] = useState('');
  const dq = useDebounced(q.trim(), 350);
  const albums = useAsync((s) => api.libAlbums(dq, 0, 30, s), [dq], open);
  const [creating, setCreating] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [newArtist, setNewArtist] = useState('');
  const [createBusy, setCreateBusy] = useState(false);

  const create = async () => {
    setCreateBusy(true);
    const a = await attempt(() => api.createAlbum({ title: newTitle.trim(), album_artist: newArtist.trim() || 'Various Artists', release_type: 'compilation' }));
    setCreateBusy(false);
    if (a) onPick(a);
  };

  const list = (albums.data ?? []).filter((a) => !excludeIds.includes(a.id));
  return (
    <Sheet open={open} onClose={onClose} title={title}>
      <div className="relative">
        <Icon name="search" size={18} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-dim" />
        <input className="input pl-10" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск по библиотеке" aria-label="Поиск альбома" />
      </div>

      {allowCreate &&
        (creating ? (
          <div className="mt-3 space-y-3 rounded-xl bg-raised p-3">
            <Field label="Название нового альбома">
              <input className="input" value={newTitle} onChange={(e) => setNewTitle(e.target.value)} autoFocus />
            </Field>
            <Field label="Исполнитель альбома">
              <input className="input" value={newArtist} onChange={(e) => setNewArtist(e.target.value)} placeholder="Various Artists" />
            </Field>
            <div className="flex justify-end gap-2">
              <Button small variant="ghost" onClick={() => setCreating(false)}>
                Отмена
              </Button>
              <Button small variant="primary" disabled={!newTitle.trim()} busy={createBusy || busy} onClick={create}>
                Создать и выбрать
              </Button>
            </div>
          </div>
        ) : (
          <Button small icon="plus" className="mt-3" onClick={() => setCreating(true)}>
            Новый альбом
          </Button>
        ))}

      <div className="-mx-4 mt-3">
        {albums.loading ? (
          <Loading />
        ) : albums.error ? (
          <ErrorState text={albums.error} onRetry={() => albums.reload()} />
        ) : list.length === 0 ? (
          <Empty icon="disc" title="Альбомы не найдены" />
        ) : (
          <ul>
            {list.map((a) => (
              <li key={a.id}>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => onPick(a)}
                  className="flex min-h-16 w-full items-center gap-3 px-4 py-2 text-left hover:bg-fg/5 disabled:opacity-50"
                >
                  <Cover src={a.has_cover ? libraryCoverUrl(a.id, 100) : null} size={48} icon="disc" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">{a.title}</span>
                    <span className="block truncate text-xs text-muted">
                      {[a.album_artist, a.year, tracksWord(a.track_count)].filter(Boolean).join(' · ')}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Sheet>
  );
}

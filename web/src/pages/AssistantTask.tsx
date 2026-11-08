import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { api } from '../api/client';
import type { AssistantRowIn, AssistantRowOut, AssistantTaskOut, AssistantTargetIn, TrackOut } from '../api/types';
import { Icon } from '../components/Icon';
import { PlaylistPicker } from '../components/pickers';
import { Button, Checkbox, Cover, ErrorState, ExplicitBadge, Field, Loading, Notice, PageHeader, Radio, Spinner, cx } from '../components/ui';
import { useHubEvent } from '../lib/events';
import { fmtDuration, fmtWhen, inputKindLabel, todayRu, tracksWord } from '../lib/format';
import { useAsync, useInterval } from '../lib/hooks';
import { Link } from '../lib/router';
import { attempt, toast } from '../lib/ui';
import { TaskStatus } from './Assistant';

const WORKING = ['queued', 'extracting', 'resolving'];

export function AssistantTaskPage({ id }: { id: number }) {
  const res = useAsync((s) => api.assistantTask(id, s), [id]);
  const status = res.data?.status;

  useHubEvent('assistant', (e) => {
    if (e.id === id && e.status !== status) res.reload(true);
  });
  useHubEvent('reconnect', () => res.reload(true));
  // Polling fallback in case SSE is down (proxies, sleeping phone).
  useInterval(() => res.reload(true), status && WORKING.includes(status) ? 4000 : null);

  const header = <PageHeader title="Ассистент" sub={res.data ? `${inputKindLabel(res.data.input_kind)} · ${fmtWhen(res.data.created_at)}` : undefined} back="/assistant" right={res.data && <span className="pr-3"><TaskStatus status={res.data.status} /></span>} />;

  if (res.loading)
    return (
      <>
        {header}
        <Loading />
      </>
    );
  if (res.error || !res.data)
    return (
      <>
        {header}
        <ErrorState text={res.error ?? 'Задача не найдена'} onRetry={() => res.reload()} />
      </>
    );

  const task = res.data;
  return (
    <div>
      {header}
      {WORKING.includes(task.status) ? (
        <Working status={task.status} />
      ) : task.status === 'failed' ? (
        <div className="px-4">
          <Notice kind="error">{task.error || 'Не удалось обработать запрос'}</Notice>
          <div className="mt-4">
            <Link to="/assistant" className="inline-flex min-h-11 items-center rounded-full bg-raised px-5">
              Новая задача
            </Link>
          </div>
        </div>
      ) : task.status === 'awaiting_confirmation' ? (
        <ConfirmCard key={task.confirm_token ?? task.id} task={task} onDone={(t) => res.setData(t)} />
      ) : (
        <Confirmed task={task} />
      )}
    </div>
  );
}

function Working({ status }: { status: string }) {
  const steps = [
    ['queued', 'В очереди'],
    ['extracting', 'Распознаю треки'],
    ['resolving', 'Ищу в каталоге и библиотеке'],
  ];
  const cur = steps.findIndex(([s]) => s === status);
  return (
    <div className="px-6 py-10">
      <ol className="space-y-4">
        {steps.map(([s, label], i) => (
          <li key={s} className={cx('flex items-center gap-3', i > cur && 'text-dim')}>
            <span className={cx('flex h-8 w-8 items-center justify-center rounded-full', i < cur ? 'bg-accent text-on-accent' : i === cur ? 'bg-info/20 text-info' : 'bg-raised')}>
              {i < cur ? <Icon name="check" size={16} stroke={3} /> : i === cur ? <Spinner size={16} /> : i + 1}
            </span>
            {label}
          </li>
        ))}
      </ol>
      <p className="mt-8 text-sm text-muted">Обычно это занимает 10–30 секунд. Можно закрыть экран — придёт уведомление, когда список будет готов.</p>
    </div>
  );
}

function Confirmed({ task }: { task: AssistantTaskOut }) {
  return (
    <div className="px-4 py-6">
      <div className="card flex flex-col items-center gap-3 p-6 text-center">
        <span className="flex h-14 w-14 items-center justify-center rounded-full bg-accent text-on-accent">
          <Icon name="check" size={28} stroke={3} />
        </span>
        <p className="text-lg font-semibold">{task.status === 'done' ? 'Готово' : 'Подтверждено'}</p>
        <p className="text-sm text-muted">
          {task.status === 'done' ? 'Все треки обработаны.' : 'Треки скачиваются.'}
          {task.playlist_id ? ' Они появятся в плейлисте по мере загрузки.' : ''}
        </p>
        <Link to="/downloads" className="mt-1 inline-flex min-h-11 items-center gap-2 rounded-full bg-accent px-5 font-semibold text-on-accent">
          <Icon name="download" size={18} /> Открыть загрузки
        </Link>
      </div>
      {task.rows.length > 0 && (
        <ul className="mt-4 divide-y divide-line/60">
          {task.rows.map((r) => (
            <li key={r.index} className="flex items-center gap-3 py-2">
              <Cover src={r.best?.cover_url} size={40} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm">{r.best?.title ?? r.title_raw}</span>
                <span className="block truncate text-xs text-muted">{r.best?.artist ?? r.artist_raw}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// --- confirmation card ---------------------------------------------------------------------------

interface RowSel {
  selected: boolean;
  /** index into options (status=confirm), 'manual', or null = suggested best. */
  option: number | 'manual' | null;
  artist: string;
  title: string;
}

function initialSel(r: AssistantRowOut): RowSel {
  const base = { artist: r.artist_raw, title: r.title_raw.replace(/\s*(…|\.\.\.)\s*$/, '') };
  switch (r.status) {
    case 'owned':
    case 'auto':
      return { ...base, selected: true, option: null };
    case 'confirm':
      // Low confidence: nothing is downloaded until the user picks a variant.
      return { ...base, selected: false, option: null };
    default:
      return { ...base, selected: false, option: 'manual' };
  }
}

function chosenTrack(r: AssistantRowOut, s: RowSel): TrackOut | null {
  if (s.option === 'manual') return null;
  if (typeof s.option === 'number') return r.options[s.option] ?? null;
  return r.best ?? null;
}

function ConfirmCard({ task, onDone }: { task: AssistantTaskOut; onDone: (t: AssistantTaskOut) => void }) {
  const [sel, setSel] = useState<Record<number, RowSel>>(() => Object.fromEntries(task.rows.map((r) => [r.index, initialSel(r)])));
  const [targetKind, setTargetKind] = useState<AssistantTargetIn['kind']>('new');
  const [name, setName] = useState(task.suggested_playlist_name || `Со скриншота ${todayRu()}`);
  const [existing, setExisting] = useState<{ id: string; name: string } | null>(null);
  const [picker, setPicker] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (task.suggested_playlist_name) setName(task.suggested_playlist_name);
  }, [task.suggested_playlist_name]);

  const update = (index: number, patch: Partial<RowSel>) => setSel((prev) => ({ ...prev, [index]: { ...prev[index], ...patch } }));

  const stats = useMemo(() => {
    let download = 0;
    let owned = 0;
    let unresolved = 0;
    let manualBad = 0;
    for (const r of task.rows) {
      const s = sel[r.index];
      if (!s?.selected) continue;
      if (r.status === 'confirm' && s.option === null) {
        unresolved++;
        continue;
      }
      if (s.option === 'manual') {
        if (!s.artist.trim() || !s.title.trim()) manualBad++;
        else download++;
        continue;
      }
      const t = chosenTrack(r, s);
      if (r.status === 'owned' || t?.owned) owned++;
      else download++;
    }
    return { download, owned, unresolved, manualBad, total: download + owned };
  }, [sel, task.rows]);

  const targetOk = targetKind === 'library' || (targetKind === 'new' ? !!name.trim() : !!existing);
  const canConfirm = stats.total > 0 && stats.unresolved === 0 && stats.manualBad === 0 && targetOk && !!task.confirm_token;

  const confirm = async () => {
    const rows: AssistantRowIn[] = task.rows.map((r) => {
      const s = sel[r.index];
      if (!s.selected) return { index: r.index, selected: false };
      if (s.option === 'manual') return { index: r.index, selected: true, choice: { kind: 'manual', artist: s.artist.trim(), title: s.title.trim() } };
      if (typeof s.option === 'number') {
        const t = r.options[s.option];
        return {
          index: r.index,
          selected: true,
          choice: t.owned && t.library_track_id ? { kind: 'library', track_id: t.library_track_id, ref: t.ref } : { kind: 'catalog', ref: t.ref },
        };
      }
      return { index: r.index, selected: true, choice: null };
    });
    const target: AssistantTargetIn =
      targetKind === 'new' ? { kind: 'new', name: name.trim() } : targetKind === 'existing' ? { kind: 'existing', playlist_id: existing!.id } : { kind: 'library' };
    setBusy(true);
    const r = await attempt(() => api.assistantConfirm(task.id, { confirm_token: task.confirm_token!, rows, target }));
    setBusy(false);
    if (r) {
      toast(`Подтверждено: ${tracksWord(stats.total)}`, 'success', { label: 'Загрузки', to: '/downloads' });
      onDone(r);
    }
  };

  const allOn = task.rows.every((r) => sel[r.index]?.selected || r.status === 'confirm' || r.status === 'not_found');

  return (
    <div className="pb-40">
      <div className="flex items-center justify-between px-4 pb-1">
        <p className="min-w-0 truncate text-sm text-muted">
          Найдено строк: {task.rows.length}
          {typeof task.context?.playlist_name === 'string' && task.context.playlist_name ? ` · «${task.context.playlist_name as string}»` : ''}
        </p>
        <Button
          small
          variant="ghost"
          className="shrink-0 whitespace-nowrap"
          onClick={() =>
            setSel((prev) => {
              const next = { ...prev };
              for (const r of task.rows) if (r.status === 'auto' || r.status === 'owned') next[r.index] = { ...next[r.index], selected: !allOn };
              return next;
            })
          }
        >
          {allOn ? 'Снять все' : 'Отметить все'}
        </Button>
      </div>

      {task.rows.length === 0 && <Notice>На изображении не найдено треков.</Notice>}

      <ul className="divide-y divide-line/60">
        {task.rows.map((r) => (
          <ConfirmRow key={r.index} row={r} sel={sel[r.index]} onChange={(p) => update(r.index, p)} />
        ))}
      </ul>

      <section className="px-4 pt-5" aria-label="Куда добавить">
        <h2 className="mb-2 text-lg font-bold">Куда добавить</h2>
        <div className="card space-y-1 p-2">
          <TargetOption checked={targetKind === 'new'} onClick={() => setTargetKind('new')} label="Новый плейлист" />
          {targetKind === 'new' && (
            <div className="px-2 pb-2">
              <Field label="Название плейлиста">
                <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
              </Field>
            </div>
          )}
          <TargetOption
            checked={targetKind === 'existing'}
            onClick={() => {
              setTargetKind('existing');
              if (!existing) setPicker(true);
            }}
            label="Существующий плейлист"
            sub={targetKind === 'existing' && existing ? existing.name : undefined}
            right={
              targetKind === 'existing' && existing ? (
                <Button small variant="ghost" onClick={() => setPicker(true)}>
                  Изменить
                </Button>
              ) : undefined
            }
          />
          <TargetOption checked={targetKind === 'library'} onClick={() => setTargetKind('library')} label="Только в библиотеку" />
        </div>
      </section>

      <div className="fixed inset-x-0 bottom-[calc(var(--tabbar-h)+var(--player-h,0px)+env(safe-area-inset-bottom))] z-30 mx-auto max-w-2xl border-t border-line bg-surface/95 px-4 py-3 backdrop-blur">
        <div className="mb-2 text-center text-xs text-muted">
          {stats.unresolved > 0
            ? `Выберите вариант в ${stats.unresolved} ${stats.unresolved === 1 ? 'строке' : 'строках'} или снимите отметку`
            : stats.manualBad > 0
              ? 'Заполните исполнителя и название у строк, введённых вручную'
              : `Скачать: ${stats.download} · уже есть: ${stats.owned}`}
        </div>
        <Button variant="primary" block icon="check" busy={busy} disabled={!canConfirm} onClick={confirm}>
          Подтвердить {stats.total ? tracksWord(stats.total) : ''}
        </Button>
      </div>

      <PlaylistPicker
        open={picker}
        onClose={() => setPicker(false)}
        title="Выберите плейлист"
        onPick={(p) => {
          if (p.kind === 'existing') {
            setExisting({ id: p.playlist.id, name: p.playlist.name });
            setTargetKind('existing');
          } else {
            setName(p.name);
            setTargetKind('new');
          }
          setPicker(false);
        }}
      />
    </div>
  );
}

function TargetOption({ checked, onClick, label, sub, right }: { checked: boolean; onClick: () => void; label: string; sub?: string; right?: ReactNode }) {
  return (
    <div className="flex items-center">
      <button type="button" onClick={onClick} aria-pressed={checked} className="flex min-h-12 flex-1 items-center gap-3 rounded-lg px-2 text-left hover:bg-fg/5">
        <Radio checked={checked} />
        <span className="min-w-0">
          <span className="block">{label}</span>
          {sub && <span className="block truncate text-xs text-accent">{sub}</span>}
        </span>
      </button>
      {right}
    </div>
  );
}

function TrackLine({ t, dim }: { t: TrackOut; dim?: boolean }) {
  return (
    <span className={cx('flex min-w-0 flex-1 items-center gap-3', dim && 'opacity-60')}>
      <Cover src={t.cover_small_url || t.cover_url} size={44} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[15px]">{t.title}</span>
        <span className="flex items-center gap-1.5 text-[13px] text-muted">
          {t.explicit && <ExplicitBadge />}
          <span className="truncate">{[t.artist, t.album_title, fmtDuration(t.duration_s)].filter(Boolean).join(' · ')}</span>
        </span>
      </span>
      {t.owned && <span className="shrink-0 text-xs text-accent">есть</span>}
    </span>
  );
}

const ROW_LABEL: Record<string, [string, string]> = {
  owned: ['уже есть', 'bg-fg/8 text-muted'],
  auto: ['найдено', 'bg-accent/15 text-accent'],
  confirm: ['выберите вариант', 'bg-warn/15 text-warn'],
  not_found: ['не найдено', 'bg-danger/15 text-danger'],
};

function ConfirmRow({ row, sel, onChange }: { row: AssistantRowOut; sel: RowSel; onChange: (p: Partial<RowSel>) => void }) {
  const [label, color] = ROW_LABEL[row.status] ?? [row.status, 'bg-fg/8 text-muted'];
  const raw = `${row.artist_raw} — ${row.title_raw}${row.truncated && !/…$/.test(row.title_raw) ? '…' : ''}`;
  const owned = row.status === 'owned';

  return (
    <li className={cx('py-2 pr-3', !sel.selected && 'bg-black/20')}>
      <div className="flex items-start">
        <Checkbox checked={sel.selected} onChange={(v) => onChange({ selected: v })} label={`Включить «${row.title_raw}»`} />
        <div className="min-w-0 flex-1 pt-1">
          <div className="mb-1 flex items-center gap-2">
            <span className={cx('shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium', color)}>{label}</span>
            <span className="truncate text-xs text-dim" title="Как на скриншоте">
              {raw}
              {row.duration ? ` · ${row.duration}` : ''}
            </span>
          </div>

          {(row.status === 'auto' || owned) && row.best && (
            <div className={cx(!sel.selected && 'opacity-50')}>
              <TrackLine t={row.best} dim={owned} />
            </div>
          )}

          {row.status === 'confirm' && (
            <div className="space-y-0.5" role="radiogroup" aria-label={`Варианты для «${row.title_raw}»`}>
              {row.options.map((o, i) => (
                <button
                  key={o.ref}
                  type="button"
                  role="radio"
                  aria-checked={sel.option === i}
                  onClick={() => onChange({ option: i, selected: true })}
                  className={cx('flex w-full items-center gap-3 rounded-lg py-1.5 pl-1 pr-2 text-left', sel.option === i ? 'bg-accent/10' : 'hover:bg-fg/5')}
                >
                  <Radio checked={sel.option === i} />
                  <TrackLine t={o} />
                </button>
              ))}
              <button
                type="button"
                role="radio"
                aria-checked={sel.option === 'manual'}
                onClick={() => onChange({ option: 'manual', selected: true })}
                className={cx('flex min-h-11 w-full items-center gap-3 rounded-lg pl-1 pr-2 text-left text-sm', sel.option === 'manual' ? 'bg-accent/10' : 'hover:bg-fg/5')}
              >
                <Radio checked={sel.option === 'manual'} />
                Другой трек — ввести вручную
              </button>
            </div>
          )}

          {(row.status === 'not_found' || (row.status === 'confirm' && sel.option === 'manual')) && (
            <div className="mt-1 grid grid-cols-2 gap-2">
              <input
                className="input !py-2"
                aria-label="Исполнитель"
                placeholder="Исполнитель"
                value={sel.artist}
                onChange={(e) => onChange({ artist: e.target.value, selected: true, option: 'manual' })}
              />
              <input
                className="input !py-2"
                aria-label="Название"
                placeholder="Название"
                value={sel.title}
                onChange={(e) => onChange({ title: e.target.value, selected: true, option: 'manual' })}
              />
            </div>
          )}
        </div>
      </div>
    </li>
  );
}

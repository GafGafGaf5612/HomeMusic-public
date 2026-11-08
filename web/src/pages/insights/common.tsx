import { useState, type ReactNode } from 'react';
import { Segmented, cx } from '../../components/ui';
import { PERIODS, periodOf, setQuery, type PeriodKey } from '../../lib/insights';
import { useRoute } from '../../lib/router';

/** A number with its caption. */
export function Tile({ value, label, hint }: { value: ReactNode; label: string; hint?: string }) {
  return (
    <div className="min-w-0 rounded-xl bg-surface px-3 py-2.5" title={hint}>
      <div className="truncate text-lg font-bold tabular-nums">{value}</div>
      <div className="truncate text-[11px] text-muted">{label}</div>
    </div>
  );
}

export function Tiles({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-3 gap-2 px-4 md:grid-cols-6">{children}</div>;
}

/** The period of the screen, kept in the address (`p`). */
export function PeriodPicker() {
  const p = periodOf(useRoute().query);
  return (
    <div className="px-4 pb-2">
      <Segmented<PeriodKey> label="Период" value={p} options={PERIODS} onChange={(v) => setQuery({ p: v === '30' ? null : v })} />
    </div>
  );
}

/** A block of the screen: a title and a surface. */
export function Block({ title, right, children, className }: { title: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx('min-w-0 px-4 pt-5', className)}>
      <div className="flex items-center justify-between gap-2 pb-2">
        <h2 className="text-base font-bold">{title}</h2>
        {right}
      </div>
      <div className="rounded-xl bg-surface p-3">{children}</div>
    </section>
  );
}

/** Two columns on a desktop where they fit. */
export function Columns({ children }: { children: ReactNode }) {
  return <div className="grid min-w-0 lg:grid-cols-2">{children}</div>;
}

export interface Column<T> {
  label: string;
  cell: (row: T) => ReactNode;
  align?: 'left' | 'right';
}

/** A wide table: scrolls inside its own container, the first column stays in place. */
export function ScrollTable<T>({ rows, cols, rowKey, onRow, empty = 'Нет данных' }: {
  rows: T[];
  cols: Column<T>[];
  rowKey: (row: T) => string | number;
  onRow?: (row: T) => void;
  empty?: string;
}) {
  if (!rows.length) return <p className="text-sm text-muted">{empty}</p>;
  return (
    <div className="-mx-3 overflow-x-auto overscroll-x-contain">
      <table className="w-full border-separate border-spacing-0 text-sm">
        <thead>
          <tr>
            {cols.map((c, i) => (
              <th
                key={c.label}
                className={cx(
                  'whitespace-nowrap border-b border-line px-3 py-1.5 text-[11px] font-semibold text-muted',
                  c.align === 'right' ? 'text-right' : 'text-left',
                  i === 0 && 'sticky left-0 z-10 bg-surface',
                )}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={rowKey(r)} onClick={onRow ? () => onRow(r) : undefined} className={cx(onRow && 'cursor-pointer hover:bg-fg/5')}>
              {cols.map((c, i) => (
                <td
                  key={c.label}
                  className={cx(
                    'whitespace-nowrap border-b border-line/50 px-3 py-2 tabular-nums',
                    c.align === 'right' ? 'text-right' : 'text-left',
                    i === 0 && 'sticky left-0 z-10 max-w-[45vw] truncate bg-surface md:max-w-xs',
                  )}
                >
                  {c.cell(r)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The first `n` items and «Показать все». */
export function ShowAll<T>({ items, n = 10, render }: { items: T[]; n?: number; render: (item: T, i: number) => ReactNode }) {
  const [all, setAll] = useState(false);
  if (!items.length) return <p className="text-sm text-muted">Нет данных</p>;
  return (
    <>
      <ul className="divide-y divide-line/50">{(all ? items : items.slice(0, n)).map(render)}</ul>
      {items.length > n && (
        <button type="button" className="mt-1 min-h-10 text-sm font-semibold text-accent" onClick={() => setAll(!all)}>
          {all ? 'Свернуть' : `Показать все (${items.length})`}
        </button>
      )}
    </>
  );
}

/** A small row of a list: a title, a subtitle and a value on the right. */
export function Line({ title, sub, value, onClick }: { title: ReactNode; sub?: ReactNode; value?: ReactNode; onClick?: () => void }) {
  const body = (
    <>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm">{title}</span>
        {sub && <span className="block truncate text-xs text-muted">{sub}</span>}
      </span>
      {value != null && <span className="shrink-0 text-right text-sm tabular-nums">{value}</span>}
    </>
  );
  return (
    <li>
      {onClick ? (
        <button type="button" onClick={onClick} className="flex min-h-11 w-full items-center gap-3 py-1.5 text-left hover:bg-fg/5">
          {body}
        </button>
      ) : (
        <div className="flex min-h-11 items-center gap-3 py-1.5">{body}</div>
      )}
    </li>
  );
}

/** A colored square of a facet (or a series). */
export function Swatch({ color, className }: { color: string; className?: string }) {
  return <span className={cx('inline-block h-2.5 w-2.5 shrink-0 rounded-sm align-middle', className)} style={{ background: color }} />;
}

/** «Артист — Название» of a name block. */
export const trackName = (t: { artist?: string | null; title?: string | null }) =>
  [t.artist, t.title].filter(Boolean).join(' — ') || 'Неизвестный трек';

/** Filter chips: a single choice kept in the address. */
export function Chips<T extends string>({ value, options, onChange, label }: { value: T; options: Array<{ value: T; label: string }>; onChange: (v: T) => void; label: string }) {
  return (
    <div role="tablist" aria-label={label} className="flex flex-wrap gap-1.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="tab"
          aria-selected={value === o.value}
          onClick={() => onChange(o.value)}
          className={cx('min-h-8 rounded-full px-3 text-[13px] transition', value === o.value ? 'bg-accent font-semibold text-on-accent' : 'bg-raised hover:bg-hover')}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

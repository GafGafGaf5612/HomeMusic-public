import type { ReactNode } from 'react';
import { fmtDec } from '../../lib/format';

/** The color of a weight: positive — accent, negative — danger, saturation by |s| / max. The number is always text. */
function tint(s: number, max: number): string {
  const a = Math.min(1, Math.abs(s) / Math.max(max, 1e-9));
  const c = s >= 0 ? 'var(--color-accent)' : 'var(--color-danger)';
  return `color-mix(in srgb, ${c} ${Math.round(10 + a * 45)}%, transparent)`;
}

/** A table cell with a weight `s` (or `A_artist`); `null` — no weight («—»). */
export function WeightCell({ s, max, children }: { s: number | null | undefined; max: number; children?: ReactNode }) {
  if (s == null) return <span className="block px-2 py-1 text-center text-dim">—</span>;
  return (
    <span className="block rounded px-2 py-1 text-center tabular-nums" style={{ background: s ? tint(s, max) : undefined }}>
      {fmtDec(s)}
      {children}
    </span>
  );
}

export interface DivergingRow {
  id: string;
  label: ReactNode;
  value: number;
  extra?: ReactNode;
  onClick?: () => void;
}

/** A diverging bar: negative to the left of the zero line, positive to the right; the number beside it. */
export function DivergingBar({ rows }: { rows: DivergingRow[] }) {
  const max = Math.max(1e-9, ...rows.map((r) => Math.abs(r.value)));
  const hasNeg = rows.some((r) => r.value < 0);
  const zero = hasNeg ? 35 : 0; // % of the bar's width left of zero
  return (
    <ul className="space-y-1.5">
      {rows.map((r) => {
        const w = (Math.abs(r.value) / max) * (r.value < 0 ? zero : 100 - zero);
        const body = (
          <>
            <div className="flex items-baseline justify-between gap-2 text-sm">
              <span className="min-w-0 truncate">{r.label}</span>
              <span className={r.value < 0 ? 'shrink-0 tabular-nums text-danger' : 'shrink-0 tabular-nums'}>{fmtDec(r.value)}</span>
            </div>
            <div className="relative mt-0.5 h-2 rounded-full bg-raised">
              {hasNeg && <span className="absolute inset-y-0 w-px bg-line" style={{ left: `${zero}%` }} />}
              <span
                className="absolute inset-y-0 rounded-full"
                style={{
                  left: r.value < 0 ? `${zero - w}%` : `${zero}%`,
                  width: `${w}%`,
                  background: r.value < 0 ? 'var(--color-danger)' : 'var(--color-accent)',
                }}
              />
            </div>
            {r.extra && <div className="mt-0.5 text-[11px] text-muted">{r.extra}</div>}
          </>
        );
        return (
          <li key={r.id}>
            {r.onClick ? (
              <button type="button" onClick={r.onClick} className="block w-full rounded-md px-1 py-0.5 text-left hover:bg-fg/5">
                {body}
              </button>
            ) : (
              <div className="px-1 py-0.5">{body}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

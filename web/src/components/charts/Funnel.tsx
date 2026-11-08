import type { ReactNode } from 'react';
import { cx } from '../ui';
import { fmtNum } from '../../lib/format';

export interface Step {
  label: string;
  value: number | null;
  note?: ReactNode;
  /** false: the step is not a part of the previous one (no share) */
  share?: boolean;
}

/** Funnel steps: vertical on a phone, horizontal on a desktop; each with its number and its share of the previous. */
export function Funnel({ steps }: { steps: Step[] }) {
  const max = Math.max(1, ...steps.map((s) => s.value ?? 0));
  return (
    <ol className="grid gap-2 md:grid-flow-col md:auto-cols-fr">
      {steps.map((s, i) => {
        const prev = i > 0 ? steps[i - 1].value : null;
        const share = s.share !== false && s.value != null && prev ? Math.round((s.value / prev) * 100) : null;
        return (
          <li key={s.label} className="min-w-0 rounded-lg bg-raised px-3 py-2">
            <div className="flex items-baseline justify-between gap-2">
              <span className="truncate text-xs text-muted">{s.label}</span>
              {share != null && <span className="shrink-0 text-[11px] text-dim">{share} %</span>}
            </div>
            <div className="text-lg font-bold">{s.value == null ? '—' : fmtNum(s.value)}</div>
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-bg">
              <div className={cx('h-full rounded-full bg-accent')} style={{ width: `${((s.value ?? 0) / max) * 100}%` }} />
            </div>
            {s.note && <div className="mt-1 text-[11px] text-muted">{s.note}</div>}
          </li>
        );
      })}
    </ol>
  );
}

import { useState } from 'react';
import { fmtDay, fmtNum } from '../../lib/format';

export interface Series {
  id: string;
  label: string;
  color: string;
}

export interface DayBar {
  date: string;
  values: Record<string, number>;
}

const W = 360;
const H = 120;
const PAD_B = 18;

/** Stacked columns by day; a tap on a column shows its numbers under the chart. */
export function BarsByDay({ days, series, unit = 'прослушиваний' }: { days: DayBar[]; series: Series[]; unit?: string }) {
  const [sel, setSel] = useState<number | null>(null);
  const totals = days.map((d) => series.reduce((a, s) => a + (d.values[s.id] ?? 0), 0));
  const max = Math.max(1, ...totals);
  const n = Math.max(1, days.length);
  const step = W / n;
  const bw = Math.max(1, step * 0.72);
  const ticks = [0, Math.floor((n - 1) / 2), n - 1].filter((v, i, a) => a.indexOf(v) === i && v < days.length);
  const picked = sel != null ? days[sel] : null;
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full" role="img" aria-label={`По дням, ${unit}`}>
        <line x1={0} x2={W} y1={H - PAD_B} y2={H - PAD_B} stroke="var(--color-line)" />
        {days.map((d, i) => {
          let y = H - PAD_B;
          const x = i * step + (step - bw) / 2;
          return (
            <g key={d.date} opacity={sel == null || sel === i ? 1 : 0.45}>
              {series.map((s) => {
                const v = d.values[s.id] ?? 0;
                if (!v) return null;
                const h = (v / max) * (H - PAD_B - 6);
                y -= h;
                return <rect key={s.id} x={x} y={y} width={bw} height={h} fill={s.color} />;
              })}
              <rect x={i * step} y={0} width={step} height={H} fill="transparent" onClick={() => setSel(sel === i ? null : i)} style={{ cursor: 'pointer' }} />
            </g>
          );
        })}
        {ticks.map((i) => (
          <text key={i} x={i * step + step / 2} y={H - 4} fontSize={11} fill="var(--color-muted)" textAnchor={i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}>
            {fmtDay(days[i].date)}
          </text>
        ))}
        <text x={W} y={11} fontSize={11} fill="var(--color-muted)" textAnchor="end">
          макс. {fmtNum(max)}
        </text>
      </svg>
      <div className="mt-1 min-h-5 text-xs text-muted">
        {picked ? (
          <span>
            <b className="text-fg">{fmtDay(picked.date)}</b>: {fmtNum(totals[sel!])} {unit}
            {series.filter((s) => picked.values[s.id]).map((s) => ` · ${s.label} ${fmtNum(picked.values[s.id])}`).join('')}
          </span>
        ) : (
          <Legend series={series} />
        )}
      </div>
    </div>
  );
}

export function Legend({ series }: { series: Series[] }) {
  return (
    <span className="flex flex-wrap gap-x-3 gap-y-1">
      {series.map((s) => (
        <span key={s.id} className="inline-flex items-center gap-1">
          <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: s.color }} />
          {s.label}
        </span>
      ))}
    </span>
  );
}

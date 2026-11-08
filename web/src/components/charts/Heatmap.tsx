import { useState } from 'react';
import { WEEKDAYS_SHORT, fmtNum } from '../../lib/format';

const CELL = 14;
const LEFT = 22;
const TOP = 14;

/** 7 × 24 (local weekday, Monday first × hour): the accent's opacity is the count. A tap shows the number. */
export function Heatmap({ rows }: { rows: number[][] }) {
  const [sel, setSel] = useState<[number, number] | null>(null);
  const max = Math.max(1, ...rows.flat());
  const W = LEFT + 24 * CELL;
  const H = TOP + 7 * CELL;
  const busiest = rows.flatMap((r, d) => r.map((v, h) => ({ v, d, h }))).sort((a, b) => b.v - a.v)[0];
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full max-w-2xl" role="img" aria-label="Когда слушает: день недели × час">
        {[0, 6, 12, 18, 23].map((h) => (
          <text key={h} x={LEFT + h * CELL + CELL / 2} y={10} fontSize={10} fill="var(--color-muted)" textAnchor="middle">
            {h}
          </text>
        ))}
        {rows.map((r, d) => (
          <g key={d}>
            <text x={0} y={TOP + d * CELL + CELL * 0.68} fontSize={10} fill="var(--color-muted)">
              {WEEKDAYS_SHORT[d]}
            </text>
            {r.map((v, h) => (
              <rect
                key={h}
                x={LEFT + h * CELL + 1}
                y={TOP + d * CELL + 1}
                width={CELL - 2}
                height={CELL - 2}
                rx={3}
                fill={v ? 'var(--color-accent)' : 'var(--color-raised)'}
                fillOpacity={v ? 0.15 + 0.85 * (v / max) : 1}
                stroke={sel && sel[0] === d && sel[1] === h ? 'var(--color-fg)' : 'none'}
                onClick={() => setSel([d, h])}
                style={{ cursor: 'pointer' }}
              />
            ))}
          </g>
        ))}
      </svg>
      <p className="mt-1 text-xs text-muted">
        {sel
          ? `${WEEKDAYS_SHORT[sel[0]]}, ${sel[1]}:00–${sel[1] + 1}:00 — ${fmtNum(rows[sel[0]][sel[1]])} прослушиваний`
          : busiest && busiest.v
            ? `Чаще всего: ${WEEKDAYS_SHORT[busiest.d]}, ${busiest.h}:00 (${fmtNum(busiest.v)}). Местное время.`
            : 'Прослушиваний нет.'}
      </p>
    </div>
  );
}

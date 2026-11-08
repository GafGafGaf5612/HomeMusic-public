import { fmtNum } from '../../lib/format';

export interface Share {
  id: string;
  label: string;
  value: number;
  color: string;
}

/** A horizontal 100 % bar of shares with a legend that carries the numbers. */
export function ShareBar({ parts, empty = 'Нет данных' }: { parts: Share[]; empty?: string }) {
  const shown = parts.filter((p) => p.value > 0);
  const total = shown.reduce((a, p) => a + p.value, 0);
  if (!total) return <p className="text-sm text-muted">{empty}</p>;
  return (
    <div>
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-raised" role="img" aria-label="Доли">
        {shown.map((p) => (
          <span key={p.id} style={{ width: `${(p.value / total) * 100}%`, background: p.color }} className="h-full" />
        ))}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
        {shown.map((p) => (
          <li key={p.id} className="inline-flex items-center gap-1">
            <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: p.color }} />
            <span className="text-fg">{p.label}</span> {fmtNum(p.value)} · {Math.round((p.value / total) * 100)} %
          </li>
        ))}
      </ul>
    </div>
  );
}

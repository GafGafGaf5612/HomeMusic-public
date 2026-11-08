import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { Button } from '../ui';

export interface Dot {
  id: string;
  x: number;
  y: number;
  /** radius in viewBox units (sizes don't change with the zoom) */
  r: number;
  color: string;
  shape: 'circle' | 'diamond' | 'square';
  /** outline only (a new song of the mix) */
  hollow?: boolean;
  /** a seed: an extra ring */
  ring?: boolean;
  /** an explicit 👎: a cross */
  cross?: boolean;
  /** s < 0: a red outline */
  negative?: boolean;
  /** a number at the dot (the wave's order) */
  label?: string;
  /** dimmed (does not match the search) */
  dim?: boolean;
}

export interface Anchor {
  id: string;
  x: number;
  y: number;
  label: string;
  color: string;
}

const W = 400;
const H = 320;
const PAD = 30;
const PAD_TOP = 44; // room for the zoom buttons
const HIT = 12; // px on screen
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 12;

/** The taste map: SVG with its own pan/zoom (drag, wheel, buttons); one pointer handler for the whole map, a tap
 * picks the nearest dot within 12 px. Dots are drawn from big to small. y grows upwards, as in the data. */
export function ScatterMap({
  dots,
  anchors = [],
  path = [],
  selected,
  onPick,
  label,
}: {
  dots: Dot[];
  anchors?: Anchor[];
  path?: Array<{ x: number; y: number }>;
  selected?: string | null;
  onPick: (id: string | null) => void;
  label: string;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [view, setView] = useState({ k: 1, tx: 0, ty: 0 });
  const drag = useRef<{ x: number; y: number; tx: number; ty: number; moved: boolean; id: number } | null>(null);

  // data bounds → the viewBox, the same scale on both axes
  const base = useMemo(() => {
    const xs = [...dots.map((d) => d.x), ...anchors.map((a) => a.x)];
    const ys = [...dots.map((d) => d.y), ...anchors.map((a) => a.y)];
    if (!xs.length) return { s: 1, cx: 0, cy: 0 };
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const s = Math.min((W - 2 * PAD) / Math.max(x1 - x0, 1e-9), (H - PAD - PAD_TOP) / Math.max(y1 - y0, 1e-9));
    return { s, cx: (x0 + x1) / 2, cy: (y0 + y1) / 2 };
  }, [dots, anchors]);

  const toView = (x: number, y: number): [number, number] => [
    (W / 2 + (x - base.cx) * base.s) * view.k + view.tx,
    ((H + PAD_TOP - PAD) / 2 - (y - base.cy) * base.s) * view.k + view.ty,
  ];

  /** viewBox units per screen pixel */
  const unit = () => {
    const w = svgRef.current?.getBoundingClientRect().width || W;
    return W / w;
  };

  const zoomAt = (f: number, vx = W / 2, vy = H / 2) =>
    setView((v) => {
      const k = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, v.k * f));
      const g = k / v.k;
      return { k, tx: vx - (vx - v.tx) * g, ty: vy - (vy - v.ty) * g };
    });

  // the wheel needs a non-passive listener to keep the page from scrolling
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      const u = W / r.width;
      zoomAt(e.deltaY < 0 ? 1.2 : 1 / 1.2, (e.clientX - r.left) * u, (e.clientY - r.top) * u);
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, []);

  const order = useMemo(() => [...dots].sort((a, b) => b.r - a.r), [dots]);

  const pick = (clientX: number, clientY: number) => {
    const r = svgRef.current!.getBoundingClientRect();
    const u = W / r.width;
    const vx = (clientX - r.left) * u;
    const vy = (clientY - r.top) * u;
    let best: string | null = null;
    let bestD = Infinity;
    for (const d of dots) {
      const [px, py] = toView(d.x, d.y);
      const dist = (px - vx) ** 2 + (py - vy) ** 2;
      if (dist < bestD && dist <= (HIT * u + d.r) ** 2) {
        bestD = dist;
        best = d.id;
      }
    }
    onPick(best);
  };

  const down = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (drag.current) return;
    drag.current = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty, moved: false, id: e.pointerId };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const move = (e: ReactPointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (!d.moved && dx * dx + dy * dy < 25) return;
    d.moved = true;
    const u = unit();
    setView((v) => ({ ...v, tx: d.tx + dx * u, ty: d.ty + dy * u }));
  };
  const up = (e: ReactPointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    drag.current = null;
    if (!d.moved) pick(e.clientX, e.clientY);
  };

  const line = path.map((p) => toView(p.x, p.y));

  return (
    <div className="relative overflow-hidden rounded-xl bg-raised">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="block h-auto w-full cursor-grab select-none active:cursor-grabbing"
        style={{ touchAction: 'none' }}
        role="img"
        aria-label={label}
        onPointerDown={down}
        onPointerMove={move}
        onPointerUp={up}
        onPointerCancel={() => (drag.current = null)}
      >
        {line.length > 1 && (
          <polyline points={line.map(([x, y]) => `${x},${y}`).join(' ')} fill="none" stroke="var(--color-muted)" strokeWidth={1} strokeOpacity={0.7} />
        )}
        {order.map((d) => {
          const [x, y] = toView(d.x, d.y);
          if (x < -20 || x > W + 20 || y < -20 || y > H + 20) return null;
          const stroke = d.negative ? 'var(--color-danger)' : d.color;
          const fill = d.hollow || d.negative ? 'none' : d.color;
          const op = d.dim ? 0.18 : 0.9;
          return (
            <g key={d.id} opacity={op}>
              {d.shape === 'circle' && <circle cx={x} cy={y} r={d.r} fill={fill} fillOpacity={0.75} stroke={stroke} strokeWidth={d.negative ? 1.6 : 1} />}
              {d.shape === 'diamond' && (
                <path d={`M${x} ${y - d.r}L${x + d.r} ${y}L${x} ${y + d.r}L${x - d.r} ${y}Z`} fill={fill} stroke={stroke} strokeWidth={1.4} />
              )}
              {d.shape === 'square' && <rect x={x - d.r} y={y - d.r} width={d.r * 2} height={d.r * 2} rx={1.5} fill={fill} stroke={stroke} strokeWidth={1.4} />}
              {d.ring && <circle cx={x} cy={y} r={d.r + 3} fill="none" stroke="var(--color-fg)" strokeWidth={1.2} />}
              {d.cross && (
                <path d={`M${x - d.r - 2} ${y - d.r - 2}L${x + d.r + 2} ${y + d.r + 2}M${x + d.r + 2} ${y - d.r - 2}L${x - d.r - 2} ${y + d.r + 2}`} stroke="var(--color-danger)" strokeWidth={1.8} />
              )}
              {d.label && (
                <text x={x + d.r + 2} y={y - d.r - 1} fontSize={9} fill="var(--color-fg)">
                  {d.label}
                </text>
              )}
              {selected === d.id && <circle cx={x} cy={y} r={d.r + 6} fill="none" stroke="var(--color-fg)" strokeWidth={2} strokeDasharray="3 2" />}
            </g>
          );
        })}
        {anchors.map((a) => {
          const [x, y] = toView(a.x, a.y);
          const pts = Array.from({ length: 10 }, (_, i) => {
            const rr = i % 2 ? 3.5 : 8;
            const ang = -Math.PI / 2 + (i * Math.PI) / 5;
            return `${x + rr * Math.cos(ang)},${y + rr * Math.sin(ang)}`;
          }).join(' ');
          return (
            <g key={a.id} pointerEvents="none">
              <polygon points={pts} fill={a.color} stroke="var(--color-bg)" strokeWidth={1} />
              <text x={x} y={y + 18} fontSize={10} fontWeight={600} fill="var(--color-fg)" textAnchor={x < W * 0.3 ? 'start' : x > W * 0.7 ? 'end' : 'middle'} stroke="var(--color-raised)" strokeWidth={3} paintOrder="stroke">
                {a.label}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="absolute right-1.5 top-1.5 flex gap-1">
        <Button small onClick={() => zoomAt(1.4)} aria-label="Приблизить" className="!min-h-8 !px-3">
          +
        </Button>
        <Button small onClick={() => zoomAt(1 / 1.4)} aria-label="Отдалить" className="!min-h-8 !px-3">
          −
        </Button>
        <Button small onClick={() => setView({ k: 1, tx: 0, ty: 0 })} className="!min-h-8 !px-3">
          Сбросить
        </Button>
      </div>
    </div>
  );
}

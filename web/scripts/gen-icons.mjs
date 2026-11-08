// Generates the PWA icons (PNG) with Node built-ins only: an analytic rasterizer + zlib PNG encoder.
// Usage: node scripts/gen-icons.mjs  → public/icons/*.png and icon.svg
import { mkdirSync, writeFileSync } from 'node:fs';
import { deflateSync } from 'node:zlib';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const out = join(dirname(fileURLToPath(import.meta.url)), '..', 'public', 'icons');
mkdirSync(out, { recursive: true });

const BG = [11, 11, 11];
const GREEN = [30, 215, 96];
const DARK = [11, 11, 11];

// --- PNG encoder ---------------------------------------------------------------------------------
const crcTable = new Uint32Array(256).map((_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function crc32(buf) {
  let c = 0xffffffff;
  for (const b of buf) c = crcTable[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
}
function png(size, rgba) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // RGBA
  const raw = Buffer.alloc((size * 4 + 1) * size);
  for (let y = 0; y < size; y++) {
    raw[y * (size * 4 + 1)] = 0;
    rgba.copy(raw, y * (size * 4 + 1) + 1, y * size * 4, (y + 1) * size * 4);
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

// --- shapes in unit coordinates (0..1) --------------------------------------------------------------
const inRoundRect = (x, y, x0, y0, x1, y1, r) => {
  if (x < x0 || x > x1 || y < y0 || y > y1) return false;
  const cx = Math.min(Math.max(x, x0 + r), x1 - r);
  const cy = Math.min(Math.max(y, y0 + r), y1 - r);
  return (x - cx) ** 2 + (y - cy) ** 2 <= r * r;
};
const inCircle = (x, y, cx, cy, r) => (x - cx) ** 2 + (y - cy) ** 2 <= r * r;
const inEllipse = (x, y, cx, cy, rx, ry, rot) => {
  const c = Math.cos(rot), s = Math.sin(rot);
  const dx = x - cx, dy = y - cy;
  const u = dx * c + dy * s, v = -dx * s + dy * c;
  return (u / rx) ** 2 + (v / ry) ** 2 <= 1;
};
// Beamed double note, centred, scaled by k around (0.5, 0.5).
function inNote(x, y, k) {
  const X = (x - 0.5) / k + 0.5, Y = (y - 0.5) / k + 0.5;
  const head1 = inEllipse(X, Y, 0.385, 0.66, 0.085, 0.062, -0.45);
  const head2 = inEllipse(X, Y, 0.645, 0.6, 0.085, 0.062, -0.45);
  const stem1 = X >= 0.445 && X <= 0.48 && Y >= 0.33 && Y <= 0.65;
  const stem2 = X >= 0.705 && X <= 0.74 && Y >= 0.27 && Y <= 0.59;
  // beam: parallelogram between stems' tops
  const t = (X - 0.445) / (0.74 - 0.445);
  const top = 0.33 + (0.27 - 0.33) * t;
  const beam = t >= 0 && t <= 1 && Y >= top && Y <= top + 0.085;
  return head1 || head2 || stem1 || stem2 || beam;
}

/** kind: 'any' (rounded square), 'maskable' (full bleed, content in the safe zone), 'badge' (white note, transparent). */
function render(size, kind) {
  const SS = 4; // supersampling
  const buf = Buffer.alloc(size * size * 4);
  for (let py = 0; py < size; py++) {
    for (let px = 0; px < size; px++) {
      let r = 0, g = 0, b = 0, a = 0;
      for (let sy = 0; sy < SS; sy++) {
        for (let sx = 0; sx < SS; sx++) {
          const x = (px + (sx + 0.5) / SS) / size;
          const y = (py + (sy + 0.5) / SS) / size;
          let col = null;
          if (kind === 'badge') {
            if (inNote(x, y, 1.25)) col = [255, 255, 255];
          } else {
            const bg = kind === 'maskable' ? true : inRoundRect(x, y, 0, 0, 1, 1, 0.22);
            if (bg) {
              col = BG;
              const discR = kind === 'maskable' ? 0.3 : 0.36;
              if (inCircle(x, y, 0.5, 0.5, discR)) col = inNote(x, y, kind === 'maskable' ? 0.62 : 0.75) ? DARK : GREEN;
            }
          }
          if (col) {
            r += col[0];
            g += col[1];
            b += col[2];
            a += 255;
          }
        }
      }
      const n = SS * SS;
      const i = (py * size + px) * 4;
      const cov = a / 255;
      buf[i] = cov ? Math.round(r / cov) : 0;
      buf[i + 1] = cov ? Math.round(g / cov) : 0;
      buf[i + 2] = cov ? Math.round(b / cov) : 0;
      buf[i + 3] = Math.round(a / n);
    }
  }
  return png(size, buf);
}

const targets = [
  ['icon-192.png', 192, 'any'],
  ['icon-512.png', 512, 'any'],
  ['maskable-192.png', 192, 'maskable'],
  ['maskable-512.png', 512, 'maskable'],
  ['badge-96.png', 96, 'badge'],
  ['apple-touch-icon.png', 180, 'maskable'],
];
for (const [name, size, kind] of targets) {
  writeFileSync(join(out, name), render(size, kind));
  console.log('wrote', name);
}

writeFileSync(
  join(out, 'icon.svg'),
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <rect width="100" height="100" rx="22" fill="#0b0b0b"/>
  <circle cx="50" cy="50" r="36" fill="#1ed760"/>
  <g fill="#0b0b0b" transform="translate(50 50) scale(.75) translate(-50 -50)">
    <ellipse cx="38.5" cy="66" rx="8.5" ry="6.2" transform="rotate(-25.8 38.5 66)"/>
    <ellipse cx="64.5" cy="60" rx="8.5" ry="6.2" transform="rotate(-25.8 64.5 60)"/>
    <rect x="44.5" y="33" width="3.5" height="32"/>
    <rect x="70.5" y="27" width="3.5" height="32"/>
    <polygon points="44.5,33 74,27 74,35.5 44.5,41.5"/>
  </g>
</svg>
`,
);
console.log('wrote icon.svg');

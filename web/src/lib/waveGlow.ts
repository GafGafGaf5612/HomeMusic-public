/** The living blob of «Моя волна» (`components/WaveGlow.tsx`): one fragment shader (GLSL ES 1.0, the same source runs
 * in the Android app, `WaveGlowShader.kt`), the smoothed state that feeds it, and the colors taken from a cover.
 *
 * Every input moves by exponential smoothing `x += (target - x) * (1 - exp(-dt * rate))` — nothing jumps. No rings, no
 * beats: the loudness is a smoothed RMS only and just makes the blob breathe. */

export type RGB = [number, number, number];

export const DEFAULT_COLORS: [RGB, RGB, RGB] = [[1, 0.8, 0.1], [0.95, 0.15, 0.75], [0.9, 0.2, 0.1]];

/** `T` is the already integrated phase (the CPU adds `dt * (.03 + .045 * P)`), so a change of P never jumps the shape.
 * `K` — the blob's center in the card (0..1, y up), `Z` — zoom (bigger = smaller blob). */
export const FRAGMENT_SHADER = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec2 R; uniform float T,P,E,S,W,Z; uniform vec2 K; uniform vec3 A,B,C,BG;
float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
float n(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(h(i),h(i+vec2(1,0)),f.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),f.x),f.y);}
float fb(vec2 p){float v=0.,a=.5;for(int i=0;i<5;i++){v+=a*n(p);p=p*2.03+vec2(1.7,9.2);a*=.5;}return v;}
void main(){
  vec2 u=(gl_FragCoord.xy-K*R)/R.y*Z;
  float t=T;
  vec2 q=vec2(fb(u*1.4+t+S),fb(u*1.4-t+3.1));
  vec2 r=vec2(fb(u*2.+q*1.7+vec2(1.7,9.2)+t*1.3),fb(u*2.+q*1.7+vec2(8.3,2.8)-t));
  float d=length(u),a=atan(u.y,u.x);
  float rad=.19+.12*P+.035*E+.025*W+.10*(fb(vec2(cos(a),sin(a))*1.3+t*1.4+S)-.5);
  float e=d-rad-.13*(r.y-.5);
  float b=1.-smoothstep(-.14,.19+.06*(1.-P),e);
  float m=clamp(d/rad+.7*(r.x-.5),0.,1.4);
  vec3 c=mix(A,B,smoothstep(.1,.75,m)); c=mix(c,C,smoothstep(.65,1.15,m));
  c=mix(c,vec3(1.,.72,.62),.22*W*(1.-smoothstep(.2,.9,m)));
  float fl=(1.-smoothstep(0.,.03,abs(r.x-.52)))*b*(.03+.30*P);
  c+=fl*.4*mix(vec3(1),A,.4);
  c*=.88+.12*P;
  gl_FragColor=vec4(mix(BG,c,b),1);
}`;

export const VERTEX_SHADER = 'attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}';

const smooth = (x: number, target: number, rate: number, dt: number) => x + (target - x) * (1 - Math.exp(-dt * rate));

export interface GlowInput {
  playing: boolean;
  /** instantaneous RMS 0..1 from the analyser (0 when there is none) */
  rms: number;
  colors: readonly [RGB, RGB, RGB];
  /** slow motion for `prefers-reduced-motion`: time ×0.3, no loudness */
  reduced: boolean;
}

/** The smoothed values behind the shader's uniforms. */
export class GlowState {
  t = 0; p = 0; e = 0; s = 0; w = 0;
  colors: [RGB, RGB, RGB] = [[...DEFAULT_COLORS[0]], [...DEFAULT_COLORS[1]], [...DEFAULT_COLORS[2]]];
  private sTarget = 0;
  private wHold = 0;

  /** A ♥ of the current wave track: a warm glow that rises in ~1.5 s and fades. */
  like() { this.wHold = 1.5; }
  /** A skip: the shape slowly flows to a new one. */
  skip() { this.sTarget += 2.1; }

  step(dt: number, inp: GlowInput) {
    dt = Math.min(dt, 0.1); // a hidden tab or a stall must not make a jump
    this.p = smooth(this.p, inp.playing ? 1 : 0, 0.7, dt);
    const rms = inp.reduced ? 0 : inp.rms;
    this.e = smooth(this.e, rms, rms > this.e ? 1.2 : 0.6, dt);
    this.t += dt * (0.03 + 0.045 * this.p) * (inp.reduced ? 0.3 : 1);
    this.s = smooth(this.s, this.sTarget, 0.4, dt);
    if (this.wHold > 0) {
      this.wHold -= dt;
      this.w = smooth(this.w, 1, 1.6, dt);
    } else this.w = smooth(this.w, 0, 0.8, dt);
    for (let i = 0; i < 3; i++) for (let k = 0; k < 3; k++) this.colors[i][k] = smooth(this.colors[i][k], inp.colors[i][k], 0.5, dt);
  }
}

// --- colors from a cover ------------------------------------------------------------------------------------

function hsl2rgb(h: number, s: number, l: number): RGB {
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) => {
    const k = (n + h / 30) % 12;
    return l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
  };
  return [f(0), f(8), f(4)];
}

function rgb2hs(r: number, g: number, b: number): { h: number; s: number; v: number } {
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  let h = 0;
  if (d > 0) {
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60;
    if (h < 0) h += 360;
  }
  return { h, s: max === 0 ? 0 : d / max, v: max };
}

/** Three vivid colors from pixels (RGBA bytes of a small image): the strongest hues by saturation × brightness, at
 * least ~40° apart; the saturation and lightness are pulled up so a dull cover doesn't make a muddy blob. A cover with
 * almost no color gives null (the defaults stay). */
export function paletteFromPixels(px: ArrayLike<number>): [RGB, RGB, RGB] | null {
  const BINS = 12;
  const weight = new Array<number>(BINS).fill(0);
  let total = 0;
  for (let i = 0; i + 3 < px.length; i += 4) {
    if (px[i + 3] < 128) continue;
    const { h, s, v } = rgb2hs(px[i] / 255, px[i + 1] / 255, px[i + 2] / 255);
    const w = s * s * v;
    weight[Math.floor(h / (360 / BINS)) % BINS] += w;
    total += w;
  }
  if (total < 1) return null; // gray cover
  const order = weight.map((w, i) => ({ w, h: (i + 0.5) * (360 / BINS) })).sort((a, b) => b.w - a.w);
  const hues = [order[0].h];
  for (const o of order.slice(1)) {
    if (hues.length >= 3 || o.w < order[0].w * 0.25) break;
    if (hues.every((h) => Math.min(Math.abs(h - o.h), 360 - Math.abs(h - o.h)) >= 40)) hues.push(o.h);
  }
  while (hues.length < 3) hues.push((hues[0] + (hues.length === 1 ? 45 : -45) + 360) % 360);
  return [
    hsl2rgb(hues[0], 0.92, 0.58),
    hsl2rgb(hues[1], 0.85, 0.52),
    hsl2rgb(hues[2], 0.85, 0.46),
  ];
}

const paletteCache = new Map<string, [RGB, RGB, RGB] | null>();

/** The glow palette of a cover (null — can't tell: the defaults). A cross-origin cover without CORS can't be read. */
export function coverPalette(url: string): Promise<[RGB, RGB, RGB] | null> {
  if (paletteCache.has(url)) return Promise.resolve(paletteCache.get(url) ?? null);
  return new Promise((resolve) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.decoding = 'async';
    const done = (r: [RGB, RGB, RGB] | null) => {
      if (paletteCache.size > 50) paletteCache.clear();
      paletteCache.set(url, r);
      resolve(r);
    };
    img.onload = () => {
      try {
        const c = document.createElement('canvas');
        c.width = c.height = 24;
        const g = c.getContext('2d', { willReadFrequently: true });
        if (!g) return done(null);
        g.drawImage(img, 0, 0, 24, 24);
        done(paletteFromPixels(g.getImageData(0, 0, 24, 24).data));
      } catch {
        done(null);
      }
    };
    img.onerror = () => done(null);
    img.src = url;
  });
}

/** The theme's card color as 0..1 floats (the blob fades into it). */
export function parseCssColor(css: string): RGB {
  const m = css.match(/rgba?\(([^)]+)\)/);
  if (m) {
    const [r, g, b] = m[1].split(/[ ,/]+/).map(Number);
    return [r / 255, g / 255, b / 255];
  }
  return [0.13, 0.13, 0.13];
}

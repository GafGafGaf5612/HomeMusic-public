import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { getListenState, listenAudio } from '../lib/listen';
import { onPlayEvent } from '../lib/plays';
import { attachLoudness, loudnessEnabled, readRms } from '../lib/waveAudio';
import {
  DEFAULT_COLORS, FRAGMENT_SHADER, GlowState, VERTEX_SHADER, coverPalette, parseCssColor,
  type RGB,
} from '../lib/waveGlow';

interface Props {
  /** the wave is playing: the blob brightens and moves a bit faster (eased over ~3 s) */
  playing: boolean;
  /** cover of the current track: its colors tint the blob (the defaults without one) */
  cover?: string | null;
  /** where the blob sits in its box, 0..1 (y up), and its zoom (bigger means a smaller blob) */
  center?: [number, number];
  zoom?: number;
  className?: string;
  style?: CSSProperties;
}

const reducedMotion = () => !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

const css = (c: RGB, a: number) =>
  `rgba(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)},${a})`;

/** The living soft blob of «Моя волна» (`WaveHero`, desktop only): a fragment shader drawn at half resolution and stretched, stopped while the
 * card is off screen or the page is hidden. Without WebGL (or after a lost context) a still soft radial gradient. */
export function WaveGlow({ playing, cover, center = [0.5, 0.5], zoom = 0.9, className, style }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const live = useRef({ playing, colors: DEFAULT_COLORS as readonly [RGB, RGB, RGB], center, zoom });
  const state = useRef(new GlowState());
  const [fallback, setFallback] = useState(false);
  live.current.playing = playing;
  live.current.center = center;
  live.current.zoom = zoom;

  // colors of the current cover
  useEffect(() => {
    live.current.colors = DEFAULT_COLORS;
    if (!cover) return;
    let stale = false;
    void coverPalette(cover).then((p) => { if (!stale && p) live.current.colors = p; });
    return () => { stale = true; };
  }, [cover]);

  // hearts and skips of the wave's tracks
  useEffect(() => onPlayEvent((e) => {
    if (!getListenState().context.startsWith('wave:')) return;
    if (e.kind === 'like') state.current.like();
    else if (e.kind === 'skip') state.current.skip();
  }), []);

  useEffect(() => {
    const cv = canvas.current, box = wrap.current;
    if (!cv || !box) return;
    const gl = cv.getContext('webgl', { antialias: false, alpha: false, powerPreference: 'low-power' });
    if (!gl) { setFallback(true); return; }

    const compile = (type: number, src: string) => {
      const s = gl.createShader(type);
      if (!s) return null;
      gl.shaderSource(s, src);
      gl.compileShader(s);
      return gl.getShaderParameter(s, gl.COMPILE_STATUS) ? s : null;
    };
    const vs = compile(gl.VERTEX_SHADER, VERTEX_SHADER), fs = compile(gl.FRAGMENT_SHADER, FRAGMENT_SHADER);
    const prog = gl.createProgram();
    if (!vs || !fs || !prog) { setFallback(true); return; }
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) { setFallback(true); return; }
    gl.useProgram(prog);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, 'p');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    const U = (n: string) => gl.getUniformLocation(prog, n);
    const u = { R: U('R'), T: U('T'), P: U('P'), E: U('E'), S: U('S'), W: U('W'), Z: U('Z'), K: U('K'), A: U('A'), B: U('B'), C: U('C'), BG: U('BG') };

    let w = 0, h = 0;
    const resize = () => {
      const r = box.getBoundingClientRect();
      // half resolution (at most ~1 px per CSS px): the blob is soft, the browser stretches it
      const k = Math.min(window.devicePixelRatio || 1, 2) * 0.5;
      w = Math.max(2, Math.round(r.width * k));
      h = Math.max(2, Math.round(r.height * k));
      if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
      gl.viewport(0, 0, w, h);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(box);

    // The blob glows right on the page, so the shader fades into the page's own background.
    const pageColor = () => parseCssColor(getComputedStyle(document.body).backgroundColor);
    let bg: RGB = pageColor();
    const themeObserver = new MutationObserver(() => { bg = pageColor(); });
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style'] });

    let onScreen = true, raf = 0, last = 0, drawn = 0;
    const reduced = reducedMotion();
    const frame = (now: number) => {
      raf = 0;
      if (!onScreen || document.hidden) return;
      raf = requestAnimationFrame(frame);
      if (now - drawn < 32) return; // ~30 fps is plenty for a shape this slow
      const dt = last ? (now - last) / 1000 : 0;
      last = now;
      drawn = now;

      const l = live.current;
      if (l.playing && loudnessEnabled()) {
        const a = listenAudio();
        if (a) void attachLoudness(a);
      }
      const s = state.current;
      s.step(dt, { playing: l.playing, rms: l.playing ? readRms() : 0, colors: l.colors, reduced });
      gl.uniform2f(u.R, w, h);
      gl.uniform1f(u.T, s.t);
      gl.uniform1f(u.P, s.p);
      gl.uniform1f(u.E, s.e);
      gl.uniform1f(u.S, s.s);
      gl.uniform1f(u.W, s.w);
      gl.uniform1f(u.Z, l.zoom);
      gl.uniform2f(u.K, l.center[0], l.center[1]);
      gl.uniform3fv(u.A, s.colors[0]);
      gl.uniform3fv(u.B, s.colors[1]);
      gl.uniform3fv(u.C, s.colors[2]);
      gl.uniform3fv(u.BG, bg);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    };
    const kick = () => {
      if (!raf && onScreen && !document.hidden) { last = 0; raf = requestAnimationFrame(frame); }
    };
    const io = new IntersectionObserver((entries) => {
      onScreen = entries.some((e) => e.isIntersecting);
      if (onScreen) kick();
    });
    io.observe(box);
    document.addEventListener('visibilitychange', kick);
    const lost = (e: Event) => { e.preventDefault(); setFallback(true); };
    cv.addEventListener('webglcontextlost', lost);
    kick();

    return () => {
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect(); io.disconnect(); themeObserver.disconnect();
      document.removeEventListener('visibilitychange', kick);
      cv.removeEventListener('webglcontextlost', lost);
      // Free the context only once the canvas is really gone (React strict mode re-runs the effect on the same canvas).
      setTimeout(() => { if (!cv.isConnected) gl.getExtension('WEBGL_lose_context')?.loseContext(); }, 0);
    };
  }, []);

  const [a, b] = DEFAULT_COLORS;
  const still = `radial-gradient(circle at ${center[0] * 100}% ${(1 - center[1]) * 100}%, ${css(a, 0.55)} 0, ${css(b, 0.35)} 28%, transparent 58%)`;
  return (
    <div ref={wrap} className={className} aria-hidden="true" style={fallback ? { ...style, background: still } : style}>
      {!fallback && <canvas ref={canvas} className="block h-full w-full" />}
    </div>
  );
}

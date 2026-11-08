// Dev-only mock of the admin insights API (INSIGHTS.md 9.4): synthetic data from a seeded generator — 3 users,
// ~80 tracks with weights of both signs, 3 facets, mixes with a funnel, wave sessions of every kind, 30 days of events.
// User 3 has no taste map (`available=false`).

type Json = Record<string, unknown>;

/** Hub-style naive UTC time `sec` seconds ago. */
const ago = (sec: number) => new Date(Date.now() - sec * 1000).toISOString().slice(0, 19);
const DAY = 86400;

function rng(seed: number) {
  let x = seed >>> 0 || 1;
  return () => {
    x ^= x << 13;
    x ^= x >>> 17;
    x ^= x << 5;
    return ((x >>> 0) % 100000) / 100000;
  };
}

const USERS = [
  { id: 1, username: 'owner', is_admin: true },
  { id: 2, username: 'anna', is_admin: false },
  { id: 3, username: 'boris', is_admin: false },
];

const ARTISTS = ['Северный Ветер', 'Луна и Пар', 'Город Огней', 'Тихий Порт', 'Бумажный Змей', 'Кварц', 'Синие Окна', 'Полдень', 'Мята', 'Эхо Квартала'];
const WORDS = ['Дорога', 'Свет', 'Ночь', 'Вокзал', 'Снег', 'Море', 'Сигнал', 'Апрель', 'Окно', 'Пламя', 'Ветер', 'Кофе', 'Мост', 'Тень', 'Город', 'Рассвет'];

interface Tr {
  key: string;
  ref: string | null;
  nd_song_id: string | null;
  artist: string;
  title: string;
  owned: boolean;
  facet: number; // 0..2, or -1: outside the facets
  x: number;
  y: number;
  sound: boolean;
}

const TRACKS: Tr[] = (() => {
  const r = rng(7);
  const out: Tr[] = [];
  for (let i = 0; i < 82; i++) {
    const owned = r() < 0.7;
    const facet = r() < 0.12 ? -1 : i % 3;
    const cx = [-1.2, 1.1, 0.1][Math.max(0, facet)];
    const cy = [0.6, 0.5, -1.1][Math.max(0, facet)];
    out.push({
      key: owned ? `nd:mk${1000 + i}` : `dz:${700000 + i}`,
      ref: owned ? null : `dz:${700000 + i}`,
      nd_song_id: owned ? `mk${1000 + i}` : null,
      artist: ARTISTS[(i * 7) % ARTISTS.length],
      title: `${WORDS[i % WORDS.length]} ${['', 'II', 'на двоих', '(акустика)', 'до утра'][i % 5]}`.trim(),
      owned,
      facet,
      x: cx + (r() - 0.5) * 1.6,
      y: cy + (r() - 0.5) * 1.6,
      sound: r() > 0.06,
    });
  }
  return out;
})();

const name = (t: Tr) => ({ key: t.key, ref: t.ref, nd_song_id: t.nd_song_id, artist: t.artist, title: t.title, owned: t.owned });

const FACETS = [
  { id: 'f1', weight: 0.46, artists: ['Северный Ветер', 'Тихий Порт'] },
  { id: 'f2', weight: 0.31, artists: ['Луна и Пар', 'Кварц'] },
  { id: 'f3', weight: 0.23, artists: ['Город Огней', 'Полдень'] },
];

/** A user's weight of every track (deterministic): positive mostly, some negative, some none. */
function weights(uid: number) {
  const r = rng(uid * 101);
  return TRACKS.map((t, i) => {
    const v = r();
    const s = v < 0.12 ? -(0.3 + r() * 3) : v < 0.25 ? 0 : 0.2 + r() * (uid === 1 ? 5 : 3);
    const p = s > 0 ? s + r() * 0.8 : r() * 0.5;
    const n = s > 0 ? p - s : p - s;
    return {
      t,
      s: Math.round(s * 1000) / 1000,
      p: Math.round(p * 1000) / 1000,
      n: Math.round(Math.max(0, n) * 1000) / 1000,
      liked: s > 2.5 && i % 2 === 0,
      disliked: s < -2.5,
      downloaded: t.owned && i % 4 === 0,
      plays: Math.max(0, Math.round(s * 2 + r() * 3)),
      early: s < 0 ? 1 + Math.round(r() * 3) : Math.round(r()),
      seed: s > 3 && i % 3 === 0,
    };
  });
}

type W = ReturnType<typeof weights>[number];

const sat = (x: number) => (x <= 2 ? x : 2 + 2 * Math.log(1 + (x - 2) / 2));

function row(w: W) {
  return {
    ...name(w.t), s: w.s, p: w.p, sat_p: Math.round(sat(w.p) * 1000) / 1000, n: w.n, liked: w.liked, disliked: w.disliked,
    downloaded: w.downloaded, album_liked: false, heard30: w.plays > 0, familiar: w.plays >= 5 || w.liked, seed_eligible: w.seed,
    last_heard: w.plays ? ago(((w.t.key.length * 13) % 25) * DAY + 3600) : null, last_positive: w.s > 0 ? ago(3 * DAY) : null,
    positive_age: w.s > 0 ? 4.2 : null, plays: w.plays, early_skips: w.early, facet_id: w.t.facet >= 0 ? FACETS[w.t.facet].id : null,
  };
}

const SOURCES = ['wave', 'mix', 'album', 'playlist', 'search', 'liked', 'unknown'];

function period(url: URL) {
  const to = url.searchParams.get('to') ?? new Date().toISOString().slice(0, 10);
  const from = url.searchParams.get('from') ?? new Date(Date.now() - 29 * DAY * 1000).toISOString().slice(0, 10);
  return { from, to, timezone: 'Europe/Moscow' };
}

function days(p: { from: string; to: string }): string[] {
  const out: string[] = [];
  const end = new Date(p.to + 'T00:00:00Z');
  const start = new Date(Math.max(new Date(p.from + 'T00:00:00Z').getTime(), end.getTime() - 89 * DAY * 1000));
  for (let d = start; d <= end; d = new Date(d.getTime() + DAY * 1000)) out.push(d.toISOString().slice(0, 10));
  return out;
}

const GEN = [
  { generator: 'favorites', title: 'Любимое' },
  { generator: 'ytm_radio', title: 'Похожее: радио YouTube Music' },
  { generator: 'lastfm_similar', title: 'Похожее: Last.fm' },
  { generator: 'similar_artists', title: 'Незнакомое: похожие исполнители' },
];

function listening(uid: number, url: URL): Json {
  const p = period(url);
  const r = rng(uid * 17 + days(p).length);
  const scale = uid === 1 ? 1 : uid === 2 ? 0.5 : 0.15;
  const byDay = days(p).map((date) => {
    const by: Record<string, number> = {};
    for (const s of SOURCES) {
      const n = Math.round(r() * (s === 'wave' ? 8 : s === 'mix' ? 5 : 3) * scale);
      if (n) by[s] = n;
    }
    const heard = Object.values(by).reduce((a, b) => a + b, 0);
    return { date, heard, listened_ms: heard * 205000, by_source: by };
  });
  const heard = byDay.reduce((a, d) => a + d.heard, 0);
  const bySource = SOURCES.map((s) => {
    const h = byDay.reduce((a, d) => a + (d.by_source[s] ?? 0), 0);
    return { source: s, launches: Math.ceil(h / 6), starts: Math.round(h * 1.5), heard: h, finished: Math.round(h * 0.7), early_skips: Math.round(h * 0.35), listened_ms: h * 205000 };
  }).filter((s) => s.heard);
  const ws = weights(uid).filter((w) => w.plays);
  const genStats = (k: number) => GEN.map((g, i) => ({ ...g, offered: 40 - i * 7 + k, started: 18 - i * 3, heard: 12 - i * 2, finished: 9 - i * 2, early_skips: 4 + i, liked: Math.max(0, 3 - i) }));
  return {
    user_id: uid,
    period: p,
    totals: {
      counted_listens: heard, heard_events: Math.round(heard * 1.06), starts: Math.round(heard * 1.6), finished: Math.round(heard * 0.72),
      early_skips: Math.round(heard * 0.38), late_skips: Math.round(heard * 0.15), listened_ms: heard * 205000, unique_tracks: Math.round(heard * 0.5),
      unique_artists: Math.round(heard * 0.14), new_tracks: Math.round(heard * 0.18), new_artists: Math.round(heard * 0.03),
      active_days: byDay.filter((d) => d.heard).length, likes: Math.round(heard * 0.04), unlikes: 1, dislikes_track: 2, dislikes_artist: uid === 1 ? 1 : 0, downloads: 9,
    },
    by_day: byDay,
    by_source: bySource,
    heatmap: Array.from({ length: 7 }, (_, d) => Array.from({ length: 24 }, (_, h) => Math.round(((h >= 7 && h <= 9) || (h >= 19 && h <= 23) ? 6 : h < 6 ? 0 : 1.5) * r() * scale * (d >= 5 ? 1.4 : 1)))),
    clients: [
      { client: 'app/1.14.0', devices: 1, starts: Math.round(heard * 1.3), heard: Math.round(heard * 0.85), last_at: ago(3600) },
      { client: 'pwa', devices: 2, starts: Math.round(heard * 0.3), heard: Math.round(heard * 0.15), last_at: ago(3 * DAY) },
    ],
    top_tracks: ws.slice(0, 20).map((w) => ({ ...name(w.t), heard: w.plays + 2, listened_ms: (w.plays + 2) * 210000 })),
    top_artists: ARTISTS.slice(0, 8).map((a, i) => ({ artist: a, heard: 40 - i * 4, tracks: 11 - i })),
    discoveries: ws.slice(5, 30).map((w, i) => ({ ...name(w.t), first_heard_at: ago((i + 1) * DAY * 0.9), source: i % 3 ? 'wave' : 'mix' })),
    recommendations: {
      mix: {
        built: 30, launched_mixes: 21, launches: 33, started: 520, heard: 340, finished: 250, early_skips: 110, likes: 9, dislikes: 1,
        familiar: { started: 200, heard: 160 }, new: { started: 320, heard: 180 }, by_generator: genStats(0), adopted: 7,
      },
      wave: {
        launches: 12, restarts: 2, by_origin: { home_card: 5, track_menu: 4, radio: 2, not_recorded: 1 }, by_seed_kind: { own: 5, track: 4, library_artist: 2, unknown: 1 },
        started: 210, heard: 150, finished: 101, early_skips: 40, likes: 6, dislikes: 1, by_generator: genStats(5), adopted: 5,
      },
    },
    quality: { events_without_context: 12, events_without_playback: 300, scrobbles: 20, legacy_finishes: 15, inferred_launches: 9, inferred_playlist_mixes: 2, reconstructed_waves: 1, first_event_at: ago(200 * DAY) },
  };
}

function overview(url: URL): Json {
  const rows = USERS.map((u) => {
    const l = listening(u.id, url) as { totals: Record<string, number>; recommendations: { mix: Json; wave: Json } };
    const t = l.totals;
    return {
      user: { ...u, excluded: false }, counted_listens: t.counted_listens, heard_events: t.heard_events, listened_ms: t.listened_ms, active_days: t.active_days,
      mixes_built: 30, mix_launches: u.id === 3 ? 2 : 33 / u.id, wave_launches: u.id === 3 ? 0 : 12, wave_restarts: u.id === 1 ? 2 : 0,
      likes: t.likes, dislikes: t.dislikes_track + t.dislikes_artist, new_tracks: t.new_tracks, new_artists: t.new_artists,
      last_activity_at: ago(u.id * 3600), app_version: u.id === 3 ? '1.12.0' : '1.14.0', last_seen_at: ago(u.id * 1800),
    };
  });
  const keys = ['counted_listens', 'heard_events', 'listened_ms', 'mixes_built', 'mix_launches', 'wave_launches', 'wave_restarts', 'likes', 'dislikes', 'new_tracks'];
  const totals: Record<string, number> = { users: rows.length };
  for (const k of keys) totals[k] = rows.reduce((a, r) => a + Number((r as Json)[k]), 0);
  return { period: period(url), users: rows, totals };
}

function taste(uid: number, url: URL): Json {
  const v1 = url.searchParams.get('model') === 'v1';
  const ws = weights(uid);
  const byArtist = new Map<string, number>();
  for (const w of ws) byArtist.set(w.t.artist, (byArtist.get(w.t.artist) ?? 0) + w.s);
  return {
    user_id: uid, model: v1 ? 'v1' : 'v2', model_note: v1 ? 'Локальный v1: без обогащения каталогом, только для сравнения.' : null, now: ago(60), timezone: 'Europe/Moscow',
    evidence: ws.reduce((a, w) => a + Math.max(0, w.s), 0), maturity: v1 ? null : uid === 3 ? 0.35 : 0.82, active_units: v1 ? null : 41.5, active_days: v1 ? null : 63,
    counts: {
      total: ws.length, positive: ws.filter((w) => w.s > 0).length, strong: ws.filter((w) => w.s >= 1).length, negative: ws.filter((w) => w.s < 0).length,
      liked: ws.filter((w) => w.liked).length, downloaded: ws.filter((w) => w.downloaded).length, album_liked: 0, explicit_dislikes: ws.filter((w) => w.disliked).length, disliked_artists: 1,
    },
    facets: v1 || uid === 3 ? null : FACETS.map((f, i) => ({
      ...f, size: ws.filter((w) => w.t.facet === i && w.s > 0).length,
      top_members: ws.filter((w) => w.t.facet === i).sort((a, b) => b.s - a.s).slice(0, 5).map((w) => ({ ...name(w.t), s: w.s })),
    })),
    embedder: v1 ? 'v1' : uid === 3 ? 'down' : { space_id: 'mock-space', model: 'muq-mulan' },
    seeds_today: ws.filter((w) => w.seed).slice(0, 6).map((w) => ({ ...name(w.t), s: w.s, facet_id: w.t.facet >= 0 && uid !== 3 ? FACETS[w.t.facet].id : null })),
    onboarding_reserve: uid === 3 ? 5 : 1, onboarding_artists: [{ ref: 'dz:9001', name: 'Синие Окна' }],
    seeds_note: 'Сиды без холодного старта и сети: что микс взял на самом деле — на его странице.',
    top_artists: [...byArtist.entries()].sort((a, b) => b[1] - a[1]).map(([a, s], i) => ({ key: a.toLowerCase(), name: a, score: Math.round(s * 100) / 100, listens: 30 - i * 2, liked: i === 0, disliked: s < 0 })),
    limitations: uid === 3 ? ['facets: embedder down'] : [],
  };
}

function tracks(uid: number, url: URL): Json {
  const q = url.searchParams;
  const flt = q.get('filter') ?? 'all';
  const sort = q.get('sort') ?? 's';
  const needle = (q.get('q') ?? '').toLowerCase();
  const keep: Record<string, (w: W) => boolean> = {
    all: () => true, positive: (w) => w.s > 0, seed: (w) => w.seed, negative: (w) => w.s < 0, liked: (w) => w.liked, disliked: (w) => w.disliked, downloaded: (w) => w.downloaded,
  };
  let rows = weights(uid).filter((w) => (w.s !== 0 || w.liked || w.downloaded) && (keep[flt] ?? keep.all)(w) && (!needle || `${w.t.artist} ${w.t.title}`.toLowerCase().includes(needle))).map(row);
  const by: Record<string, (r: ReturnType<typeof row>) => number> = { s: (r) => r.s, p: (r) => r.p, n: (r) => r.n, plays: (r) => r.plays, last_heard: (r) => (r.last_heard ? Date.parse(r.last_heard) : 0) };
  rows = sort === 'title' ? rows.sort((a, b) => a.title.localeCompare(b.title)) : rows.sort((a, b) => (by[sort] ?? by.s)(b) - (by[sort] ?? by.s)(a));
  const limit = Number(q.get('limit') ?? 50);
  const offset = Number(q.get('offset') ?? 0);
  return { model: q.get('model') === 'v1' ? 'v1' : 'v2', now: ago(60), embedder: null, total: rows.length, offset, rows: rows.slice(offset, offset + limit) };
}

function track(uid: number, url: URL): Json | null {
  const key = url.searchParams.get('key');
  const w = weights(uid).find((x) => x.t.key === key);
  if (!w) return null;
  const contributions: Json[] = [];
  let left = w.p;
  for (let i = 0; left > 0.01 && i < 8; i++) {
    const wt = Math.round(0.95 ** (i * 3) * 100) / 100;
    const v = Math.min(left, 2 * wt);
    contributions.push({ kind: i === 3 && w.liked ? 'like' : 'finish', ts: ago((i * 3 + 1) * DAY), event_id: `e${i}`, side: 'P', base: i === 3 && w.liked ? 1 : 2, age: i * 3, w: wt, value: Math.round(v * 1000) / 1000, context: i % 2 ? 'wave:mockw1' : 'mix:3', client: 'app/1.14.0', position_s: 214, duration_s: 214, note: null });
    left -= v;
  }
  if (w.n > 0) contributions.splice(1, 0, { kind: 'skip', ts: ago(2 * DAY), event_id: 'en', side: 'N', base: -1, age: 2, w: 0.88, value: w.n, context: 'mix:3', client: 'app/1.14.0', position_s: 12, duration_s: 200, note: null });
  contributions.push({ kind: 'start', ts: ago(40 * DAY), event_id: 'es', side: '0', base: 0, age: 40, w: 0.4, value: 0, context: 'album', client: 'pwa', position_s: 0, duration_s: null, note: 'start — вес 0' });
  contributions.push({ kind: 'finish', ts: ago(1200 * DAY), event_id: 'eo', side: '0', base: 2, age: 900, w: 0, value: 0, context: null, client: 'navidrome', position_s: null, duration_s: null, note: 'старше 3 лет' });
  const { artist: _artistName, ...rest } = row(w);
  return {
    ...rest, model: url.searchParams.get('model') === 'v1' ? 'v1' : 'v2', now: ago(60), starts: w.plays + w.early + 1, finishes: Math.max(0, w.plays - 1), late_skips: 1, contributions,
    formula: `s = sat(P) − N = sat(${w.p.toFixed(3)}) − ${w.n.toFixed(3)} = ${sat(w.p).toFixed(3)} − ${w.n.toFixed(3)} = ${w.s.toFixed(3)}`,
    artist: { key: w.t.artist.toLowerCase(), name: w.t.artist, score: 6.1, positive_sum: 7.2, negative_sum: 1.1, tracks_part: 5.6, likes_part: 0.5, tracks: 8, this_track: w.s, listens: 32, liked: false, disliked: false, formula: 'A = sat(Σ max(s,0)) − 0.5·sat(Σ max(−s,0)) + ♥исполнителя·w + 0.5·Σ ♥альбома·w' },
    rest: { recent_until: ago(-2 * DAY), favorites_after: ago(-6 * DAY), stash_after: ago(-59 * DAY) },
    facet_cos: uid === 3 ? null : { f1: w.t.facet === 0 ? 0.71 : 0.12, f2: w.t.facet === 1 ? 0.66 : 0.2, f3: w.t.facet === 2 ? 0.58 : 0.05 },
    mixes: [{ mix_id: 3, created_at: ago(DAY), state: 'active', position: 2, generator: 'ytm_radio', generator_title: 'Похожее: радио YouTube Music', score: 2.41, outcome: 'finished', early_skip_at: null, listened_ms: 214000 }],
    waves: [{ session_id: 'mockw2', first_handed_at: ago(2 * DAY), position: 4, generator: 'lastfm_similar', score: 1.8, value: 0.6, outcome: 'early_skip', early_skip_at: 40, listened_ms: 40000 }],
  };
}

function map(uid: number, url: URL): Json {
  const q = url.searchParams;
  const basis = q.get('basis') === 'user' ? 'user' : 'global';
  const empty = { space_id: null, basis, explained: [], facets: [], points: [], missing: [], truncated: false, mix_id: null, wave_id: null, layout_note: '' };
  if (q.get('model') === 'v1') return { available: false, reason: 'model_v1', ...empty };
  if (uid === 3) return { available: false, reason: 'embedder_down', ...empty };
  const ws = weights(uid);
  const shift = basis === 'user' ? 1.3 : 1;
  const anchors = [[0, 1], [0.866, -0.5], [-0.866, -0.5]];
  const mixKeys = new Set(TRACKS.filter((_, i) => i % 5 === 1).map((t) => t.key));
  const waveOrder = TRACKS.filter((_, i) => i % 7 === 2).slice(0, 9).map((t) => t.key);
  const outcomes = ['finished', 'early_skip', 'heard', 'not_played', 'liked'];
  const points = [];
  const missing = [];
  for (const w of ws) {
    const layers: string[] = [];
    if (w.s !== 0 || w.liked || w.downloaded) layers.push('profile');
    if (mixKeys.has(w.t.key)) layers.push('mix');
    const wp = waveOrder.indexOf(w.t.key);
    if (wp >= 0) layers.push('wave');
    if (!layers.length) continue;
    if (!w.t.sound) {
      missing.push({ key: w.t.key, artist: w.t.artist, title: w.t.title, layers });
      continue;
    }
    const cos = FACETS.map((_, i) => (i === w.t.facet ? 0.4 + Math.abs((w.t.x * 7) % 0.3) : Math.abs((w.t.y * (i + 3) * 5) % 0.35) - 0.05));
    const u = cos.map((c) => Math.max(0, c) ** 2);
    const su = u.reduce((a, b) => a + b, 0);
    const rr = Math.min(1, Math.max(...cos) / 0.6);
    const fx = su ? (u.reduce((a, b, i) => a + b * anchors[i][0], 0) / su) * rr : 0;
    const fy = su ? (u.reduce((a, b, i) => a + b * anchors[i][1], 0) / su) * rr : 0;
    const inProfile = layers.includes('profile');
    points.push({
      key: w.t.key, artist: w.t.artist, title: w.t.title, layers, x: w.t.x * shift, y: w.t.y, fx, fy,
      s: inProfile ? w.s : null, p: inProfile ? w.p : null, n: inProfile ? w.n : null, facet_id: w.t.facet >= 0 ? FACETS[w.t.facet].id : null, facet_cos: cos,
      seed: w.seed, liked: w.liked, disliked: w.disliked, negative: w.s < 0 || w.disliked, familiar: w.plays >= 5,
      generator: mixKeys.has(w.t.key) || wp >= 0 ? 'ytm_radio' : null, generator_title: mixKeys.has(w.t.key) || wp >= 0 ? 'Похожее: радио YouTube Music' : null,
      mix_position: mixKeys.has(w.t.key) ? [...mixKeys].indexOf(w.t.key) : null, wave_position: wp >= 0 ? wp : null,
      outcome: mixKeys.has(w.t.key) || wp >= 0 ? outcomes[(w.t.title.length + wp + 5) % outcomes.length] : null,
    });
  }
  const center = (i: number) => {
    const m = TRACKS.filter((t) => t.facet === i);
    return [(m.reduce((a, t) => a + t.x, 0) / m.length) * shift, m.reduce((a, t) => a + t.y, 0) / m.length];
  };
  return {
    available: true, reason: null, space_id: 'mock-space', basis, explained: basis === 'user' ? [0.14, 0.09] : [0.11, 0.07],
    facets: FACETS.map((f, i) => ({ ...f, x: center(i)[0], y: center(i)[1], fx: anchors[i][0], fy: anchors[i][1], color_index: i })),
    points, missing, truncated: false, mix_id: Number(q.get('mix') ?? 3), wave_id: q.get('wave') ?? 'mockw1',
    layout_note: 'x, y — главные оси звука; fx, fy — раскладка «Грани»',
  };
}

function matrix(url: URL): Json {
  const q = url.searchParams;
  const by = q.get('by') === 'artist' ? 'artist' : 'track';
  const all = USERS.map((u) => ({ u, ws: weights(u.id) }));
  let rows: Array<Json & { cells: Record<string, Json>; sum_pos: number; min: number; max: number; users_positive: number; key: string; artist: string; title: string | null }>;
  if (by === 'artist') {
    rows = ARTISTS.map((a) => {
      const cells: Record<string, Json> = {};
      for (const { u, ws } of all) {
        const s = Math.round(ws.filter((w) => w.t.artist === a).reduce((x, w) => x + w.s, 0) * 100) / 100;
        if (s) cells[String(u.id)] = { s, p: null, n: null, liked: s > 8, disliked: s < -2, heard: Math.round(Math.abs(s) * 3) };
      }
      return { key: a.toLowerCase(), artist: a, title: null, owned: null, cells, sum_pos: 0, min: 0, max: 0, users_positive: 0, users_negative: 0 };
    });
  } else {
    rows = TRACKS.map((t, i) => {
      const cells: Record<string, Json> = {};
      for (const { u, ws } of all) {
        const w = ws[i];
        if (w.s !== 0 || w.liked) cells[String(u.id)] = { s: w.s, p: w.p, n: w.n, liked: w.liked, disliked: w.disliked, heard: w.plays };
      }
      return { key: t.key, artist: t.artist, title: t.title, owned: t.owned, cells, sum_pos: 0, min: 0, max: 0, users_positive: 0, users_negative: 0 };
    });
  }
  for (const r of rows) {
    const v = Object.values(r.cells).map((c) => Number(c.s));
    r.sum_pos = Math.round(v.filter((x) => x > 0).reduce((a, b) => a + b, 0) * 1000) / 1000;
    r.min = v.length ? Math.min(...v) : 0;
    r.max = v.length ? Math.max(...v) : 0;
    r.users_positive = v.filter((x) => x > 0).length;
    r.users_negative = v.filter((x) => x < 0).length;
  }
  const flt = q.get('filter') ?? 'all';
  const needle = (q.get('q') ?? '').toLowerCase();
  const keep: Record<string, (r: (typeof rows)[number]) => boolean> = {
    all: (r) => Object.keys(r.cells).length > 0, shared_liked: (r) => r.users_positive >= 2, conflict: (r) => r.max >= 1 && r.min <= -1,
    single: (r) => Object.values(r.cells).filter((c) => c.s !== 0).length === 1, disliked: (r) => Object.values(r.cells).some((c) => c.disliked),
  };
  rows = rows.filter((r) => (keep[flt] ?? keep.all)(r) && (!needle || `${r.artist} ${r.title ?? ''}`.toLowerCase().includes(needle)));
  const sort = q.get('sort') ?? 'sum';
  if (sort === 'title') rows.sort((a, b) => (a.title ?? a.artist).localeCompare(b.title ?? b.artist));
  else if (sort === 'spread') rows.sort((a, b) => b.max - b.min - (a.max - a.min));
  else if (sort.startsWith('user:')) rows.sort((a, b) => Number(b.cells[sort.slice(5)]?.s ?? -99) - Number(a.cells[sort.slice(5)]?.s ?? -99));
  else rows.sort((a, b) => b.sum_pos - a.sum_pos);
  const limit = Number(q.get('limit') ?? 50);
  const offset = Number(q.get('offset') ?? 0);
  return { model: q.get('model') === 'v1' ? 'v1' : 'v2', by, now: ago(60), users: USERS.map((u) => ({ id: u.id, username: u.username })), rows: rows.slice(offset, offset + limit), total: rows.length, offset };
}

function mixRow(id: number, uid: number) {
  const age = (3 - id) * DAY + 4 * 3600;
  return {
    id, created_at: ago(age), closed_at: id === 3 ? null : ago(age - DAY), status: id === 3 ? 'active' : 'closed', title: `Микс дня · ${['', 'вторник', 'среда', 'четверг'][id]}`,
    mode: 'normal', mode_title: 'Обычный', taste_model: id === 1 ? null : 'v2', size: 50, downloaded: 12, launches: id === 1 ? 0 : 3, started: id === 1 ? 0 : 31,
    heard: id === 1 ? 0 : 22, finished: id === 1 ? 0 : 17, early_skips: id === 1 ? 0 : 9, likes: 2, dislikes: 0,
    top_generators: GEN.slice(0, 3).map((g, i) => ({ ...g, songs: 18 - i * 5 })), user_id: uid,
  };
}

function mixDetail(id: number): Json | null {
  if (id < 1 || id > 3) return null;
  const { user_id: _u, ...base } = mixRow(id, 1);
  const old = id === 1; // a mix from before 2026-10-31: no rejected / score parts
  const outcomes = ['finished', 'heard', 'early_skip', 'not_played', 'started', 'liked', 'finished'];
  const items = TRACKS.slice(0, 64).map((t, i) => {
    const state = i < 50 ? 'active' : i < 60 ? 'reserve' : 'failed';
    const gens = 1.2 - i * 0.012;
    const sound = (t.facet >= 0 ? 0.5 : 0.1) * 0.82;
    const pen = i % 9 === 0 ? -0.4 : 0;
    return {
      ...name(t), position: state === 'active' ? i : i - 50, state, familiar: i % 5 < 2, generator: GEN[i % 4].generator, generator_title: GEN[i % 4].title,
      gens: { [GEN[i % 4].generator]: i % 10 }, score: gens + sound + 0.1 + pen,
      score_parts: old ? null : { generators: gens, sound, popularity: 0.1, track_penalty: pen, artist_penalty: 0, maturity: 0.82, total: gens + sound + 0.1 + pen },
      sim: t.facet >= 0 ? 0.5 : 0.1, rank: 1000 - i, profile: i % 5 < 2 ? { key: t.key, s: 2.1, positive: 2.4, negative: 0.3, artist_score: 3, familiar: true } : null,
      downloaded_for_mix: !t.owned && state === 'active', cleanup: id < 3 && !t.owned && state === 'active' ? (i % 3 ? 'trashed' : 'kept: ♥') : null,
      outcome: state === 'active' && !old ? outcomes[i % outcomes.length] : 'not_played', early_skip_at: i % outcomes.length === 2 ? 14 : null, listened_ms: 120000, heard_outside: i === 7,
    };
  });
  return {
    ...base, description: 'Знакомое и новое по вашему вкусу', nd_playlist_id: 'pl-mock',
    funnel: {
      generators: [
        ...GEN.map((g, i): Json => ({ ...g, tracks: 50 - i * 8, used: 18 - i * 5, seconds: 1.2 + i * 0.4, error: null, off: false })),
        { generator: 'yandex_similar', title: 'Незнакомое: похожие Яндекса', tracks: null, used: 0, seconds: 0.3, error: 'timeout', off: false },
      ],
      candidates: 640, rejected: old ? null : { disliked: 3, version: 21, duration: 9, recent: 40, negative_score: 0 }, kept: 567, sound_scored: 300,
      active: 50, familiar: 20, new: 30, reserve: 10, failed: 4, launches: base.launches, started: base.started, heard: base.heard, finished: base.finished, early_skips: base.early_skips, liked: 2, disliked: 0,
    },
    seeds: TRACKS.filter((t) => t.facet >= 0).slice(0, 8).map((t) => ({ ...name(t), facet_id: FACETS[t.facet].id })),
    seed_labels: ['Северный Ветер', 'Луна и Пар'], facets: FACETS.map((f, i) => ({ ...f, size: 12 - i * 3 })), maturity: 0.82, embedder: { scored: 300 }, build_seconds: 14.2,
    items,
    launch_list: old ? [] : [
      { id: 'l1', started_at: ago(20 * 3600), attribution: 'context', origin: 'mix_page', client: 'pwa', started: 12, heard: 9, finished: 7, early_skips: 3, likes: 1, dislikes: 0 },
      { id: 'l2', started_at: ago(10 * 3600), attribution: 'playlist_id', origin: 'nd_playlist', client: 'app/1.14.0', started: 15, heard: 10, finished: 8, early_skips: 4, likes: 1, dislikes: 0 },
      { id: 'l3', started_at: ago(5 * 3600), attribution: 'inferred_playlist', origin: null, client: 'app/1.12.0', started: 4, heard: 3, finished: 2, early_skips: 2, likes: 0, dislikes: 0 },
    ],
    artists: 31,
  };
}

const WAVES = [
  { session_id: 'mockw1', created_at: ago(1 * DAY), origin: 'home_card', client: 'app/1.14.0', restart_of: null, restart: false, seed_kind: 'own', seed: null, reconstructed: false },
  { session_id: 'mockw2', created_at: ago(2 * DAY), origin: 'track_menu', client: 'app/1.14.0', restart_of: null, restart: false, seed_kind: 'track', seed: { title: 'Вокзал', artist: 'Тихий Порт', ref: 'dz:700003', nd_song_id: null, artist_ref: null, nd_artist_id: null }, reconstructed: false },
  { session_id: 'mockw3', created_at: ago(2 * DAY - 1800), origin: 'restart', client: 'app/1.14.0', restart_of: 'mockw2', restart: true, seed_kind: 'track', seed: { title: 'Вокзал', artist: 'Тихий Порт', ref: 'dz:700003', nd_song_id: null, artist_ref: null, nd_artist_id: null }, reconstructed: false },
  { session_id: 'mockw4', created_at: ago(4 * DAY), origin: 'radio', client: 'app/1.14.0', restart_of: null, restart: false, seed_kind: 'library_artist', seed: { title: null, artist: 'Кварц', ref: null, nd_song_id: null, artist_ref: null, nd_artist_id: 'ar-mock' }, reconstructed: false },
  { session_id: 'mockw5', created_at: ago(9 * DAY), origin: null, client: 'app/1.11.0', restart_of: null, restart: false, seed_kind: 'unknown', seed: null, reconstructed: true },
];

function waveRow(w: (typeof WAVES)[number], i: number) {
  return {
    ...w, last_activity_at: ago(Date.now() / 1000 - Date.parse(w.created_at + 'Z') / 1000 - 2400), mode: w.reconstructed ? null : 'normal', taste_model: w.reconstructed ? null : 'v2',
    next_calls: w.reconstructed ? null : 6 - i, handed: w.reconstructed ? null : 18 - i * 3, tracks_started: 14 - i * 2, started: 18 - i * 3, heard: 12 - i * 2, finished: 9 - i,
    early_skips: 4, late_skips: 1, likes: i === 0 ? 1 : 0, dislikes: 0, listened_ms: (12 - i * 2) * 200000, downloads: w.reconstructed ? null : 1, cleared: i === 3,
  };
}

function waves(url: URL): Json {
  const origin = url.searchParams.get('origin');
  const kind = url.searchParams.get('seed_kind');
  const rows = WAVES.map(waveRow).filter((w) => (!origin || (w.origin ?? 'not_recorded') === origin) && (!kind || w.seed_kind === kind));
  const users = rows.filter((w) => !w.restart);
  const count = (f: (w: (typeof rows)[number]) => string) => users.reduce<Record<string, number>>((a, w) => ((a[f(w)] = (a[f(w)] ?? 0) + 1), a), {});
  return { total: rows.length, offset: 0, rows, launches: users.length, restarts: rows.length - users.length, by_origin: count((w) => w.origin ?? 'not_recorded'), by_seed_kind: count((w) => w.seed_kind) };
}

function waveDetail(sid: string): Json | null {
  const i = WAVES.findIndex((w) => w.session_id === sid);
  if (i < 0) return null;
  const w = waveRow(WAVES[i], i);
  const outcomes = ['finished', 'early_skip', 'heard', 'liked', 'not_played', 'finished', 'started'];
  const items = w.reconstructed ? [] : TRACKS.filter((_, j) => j % 7 === 2).slice(0, 9).map((t, j) => ({
    ...name(t), position: j, first_handed_at: ago(Date.now() / 1000 - Date.parse(w.created_at + 'Z') / 1000 - j * 200), last_handed_at: ago(Date.now() / 1000 - Date.parse(w.created_at + 'Z') / 1000 - j * 200),
    handed_count: j === 4 ? 2 : 1, generator: GEN[j % 4].generator, generator_title: GEN[j % 4].title, gens: { [GEN[j % 4].generator]: j }, familiar: j % 3 === 0,
    score: 1.5 - j * 0.1, sim: 0.4 + (j % 3) * 0.1, value: j ? 0.6 : null, outcome: outcomes[j % outcomes.length], early_skip_at: j % outcomes.length === 1 ? 12 : null, listened_ms: 150000,
  }));
  return {
    ...w,
    gens: w.reconstructed ? {} : { favorites: { tracks: 20, seconds: 0.2, title: 'Любимое' }, ytm_radio: { tracks: 35, seconds: 2.1, title: 'Похожее: радио YouTube Music' }, lastfm_similar: { tracks: 0, seconds: 8, error: 'timeout', title: 'Похожее: Last.fm' } },
    seeds_used: w.reconstructed ? null : ['nd:mk1000'],
    items,
    events: TRACKS.slice(2, 8).map((t, j) => ({ ts: ago(Date.now() / 1000 - Date.parse(w.created_at + 'Z') / 1000 - j * 220), kind: ['start', 'finish', 'start', 'skip', 'like', 'start'][j], track: name(t), position_s: j === 3 ? 12 : j === 1 ? 214 : 0, client: w.client })),
  };
}

/** Answers `/api/admin/insights/…`, `/api/admin/users/{id}/(listening|taste…|mixes|waves)`, `/api/admin/taste/matrix`,
 * `/api/admin/mixes/{id}`, `/api/admin/waves/{sid}`; null — not an insights path. */
export function insightsMock(p: string, url: URL): { status: number; body: unknown } | null {
  const ok = (body: unknown) => ({ status: 200, body });
  const nf = (detail = 'Не найдено') => ({ status: 404, body: { detail } });
  if (p === '/api/admin/insights/overview') return ok(overview(url));
  if (p === '/api/admin/taste/matrix') return ok(matrix(url));
  const u = p.match(/^\/api\/admin\/users\/(\d+)\/(listening|taste|taste\/tracks|taste\/track|taste\/map|mixes|waves)$/);
  if (u) {
    const uid = Number(u[1]);
    if (!USERS.some((x) => x.id === uid)) return nf('Нет такого пользователя');
    switch (u[2]) {
      case 'listening': return ok(listening(uid, url));
      case 'taste': return ok(taste(uid, url));
      case 'taste/tracks': return ok(tracks(uid, url));
      case 'taste/track': { const t = track(uid, url); return t ? ok(t) : nf('Нет такого трека'); }
      case 'taste/map': return ok(map(uid, url));
      case 'mixes': return ok([3, 2, 1].map((id) => { const { user_id: _u, ...r } = mixRow(id, uid); return r; }));
      case 'waves': return ok(uid === 3 ? { total: 0, offset: 0, rows: [], launches: 0, restarts: 0, by_origin: {}, by_seed_kind: {} } : waves(url));
    }
  }
  const m = p.match(/^\/api\/admin\/mixes\/(\d+)$/);
  if (m) { const d = mixDetail(Number(m[1])); return d ? ok(d) : nf(); }
  const w = p.match(/^\/api\/admin\/waves\/([^/]+)$/);
  if (w) { const d = waveDetail(decodeURIComponent(w[1])); return d ? ok(d) : nf(); }
  return null;
}

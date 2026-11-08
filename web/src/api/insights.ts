// Admin insights (INSIGHTS.md 7–8, hub/API.md «Admin insights»): read-only, admin only. Times are naive UTC.
import { request, withQuery } from './client';

export type TasteModel = 'v1' | 'v2';

/** Name fields of a track: local only, else «Неизвестный трек». */
export interface InsightsName {
  key: string | null;
  ref: string | null;
  nd_song_id: string | null;
  artist: string;
  title: string;
  owned: boolean;
}

/** What became of one offered track (the strongest, by that mix's/session's own launches). */
export type Outcome = 'not_played' | 'started' | 'heard' | 'finished' | 'early_skip' | 'liked' | 'disliked';
export interface OutcomeFields {
  outcome: Outcome;
  early_skip_at: number | null;
  listened_ms: number;
}

export interface Period {
  from: string;
  to: string;
  timezone: string;
}

export interface InsightsUser {
  id: number;
  username: string;
  is_admin: boolean;
  excluded: boolean;
}

export interface OverviewRow {
  user: InsightsUser;
  counted_listens: number;
  heard_events: number;
  listened_ms: number;
  active_days: number;
  mixes_built: number;
  mix_launches: number;
  wave_launches: number;
  wave_restarts: number;
  likes: number;
  dislikes: number;
  new_tracks: number;
  new_artists: number;
  last_activity_at: string | null;
  app_version: string | null;
  last_seen_at: string | null;
}

export interface OverviewOut {
  period: Period;
  users: OverviewRow[];
  totals: Record<string, number>;
}

export interface ListeningTotals {
  counted_listens: number;
  heard_events: number;
  starts: number;
  finished: number;
  early_skips: number;
  late_skips: number;
  listened_ms: number;
  unique_tracks: number;
  unique_artists: number;
  new_tracks: number;
  new_artists: number;
  active_days: number;
  likes: number;
  unlikes: number;
  dislikes_track: number;
  dislikes_artist: number;
  downloads: number;
}

export interface GeneratorStats {
  generator: string;
  title: string;
  offered: number;
  started: number;
  heard: number;
  finished: number;
  early_skips: number;
  liked: number;
}

export interface ListeningOut {
  user_id: number;
  period: Period;
  totals: ListeningTotals;
  by_day: Array<{ date: string; heard: number; listened_ms: number; by_source: Record<string, number> }>;
  by_source: Array<{ source: string; launches: number; starts: number; heard: number; finished: number; early_skips: number; listened_ms: number }>;
  heatmap: number[][];
  clients: Array<{ client: string; devices: number; starts: number; heard: number; last_at: string | null }>;
  top_tracks: Array<InsightsName & { heard: number; listened_ms: number }>;
  top_artists: Array<{ artist: string; heard: number; tracks: number }>;
  discoveries: Array<InsightsName & { first_heard_at: string; source: string }>;
  recommendations: {
    mix: {
      built: number;
      launched_mixes: number;
      launches: number;
      started: number;
      heard: number;
      finished: number;
      early_skips: number;
      likes: number;
      dislikes: number;
      familiar: { started: number; heard: number };
      new: { started: number; heard: number };
      by_generator: GeneratorStats[];
      adopted: number;
    };
    wave: {
      launches: number;
      restarts: number;
      by_origin: Record<string, number>;
      by_seed_kind: Record<string, number>;
      started: number;
      heard: number;
      finished: number;
      early_skips: number;
      likes: number;
      dislikes: number;
      by_generator: GeneratorStats[];
      adopted: number;
    };
  };
  quality: {
    events_without_context: number;
    events_without_playback: number;
    scrobbles: number;
    legacy_finishes: number;
    inferred_launches: number;
    inferred_playlist_mixes?: number;
    reconstructed_waves: number;
    first_event_at: string | null;
  };
}

/** `"off"` / `"down"` / `"timeout"` / `"v1"`, or the snapshot it answered with. */
export type EmbedderState = string | { space_id?: string; model?: string } | null;

export interface TasteFacet {
  id: string;
  weight: number;
  artists: string[];
  size: number;
  top_members: Array<InsightsName & { key: string; s: number }>;
}

export interface TasteOut {
  user_id: number;
  model: TasteModel;
  model_note: string | null;
  now: string;
  timezone: string;
  evidence: number;
  maturity: number | null;
  active_units: number | null;
  active_days: number | null;
  counts: Record<string, number>;
  facets: TasteFacet[] | null;
  embedder: EmbedderState;
  seeds_today: Array<InsightsName & { key: string; s: number; facet_id: string | null }>;
  onboarding_reserve: number;
  onboarding_artists: Array<{ ref: string; name: string }>;
  seeds_note: string;
  top_artists: Array<{ key: string; name: string; score: number; listens: number; liked: boolean; disliked: boolean }>;
  limitations: string[];
}

export interface TasteTrackRow extends InsightsName {
  s: number;
  p: number;
  sat_p: number;
  n: number;
  liked: boolean;
  disliked: boolean;
  downloaded: boolean;
  album_liked: boolean;
  heard30: boolean;
  familiar: boolean;
  seed_eligible: boolean;
  last_heard: string | null;
  last_positive: string | null;
  positive_age: number | null;
  plays: number;
  early_skips: number;
  facet_id: string | null;
}

export interface TasteTracksOut {
  model: TasteModel;
  now: string;
  embedder: EmbedderState;
  total: number;
  offset: number;
  rows: TasteTrackRow[];
}

export interface Contribution {
  kind: string;
  ts: string | null;
  event_id: string | null;
  side: 'P' | 'N' | '0';
  base: number;
  age: number | null;
  w: number;
  value: number;
  context: string | null;
  client: string | null;
  position_s: number | null;
  duration_s: number | null;
  note: string | null;
}

/** The row of the track, but `artist` is the artist's block (the API's field overrides the name). */
export interface TasteTrackOut extends Omit<TasteTrackRow, 'artist'> {
  model: TasteModel;
  now: string;
  starts: number;
  finishes: number;
  late_skips: number;
  contributions: Contribution[];
  formula: string;
  artist: {
    key: string;
    name: string;
    score: number;
    positive_sum: number;
    negative_sum: number;
    tracks_part: number;
    likes_part: number;
    tracks: number;
    this_track: number;
    listens: number;
    liked: boolean;
    disliked: boolean;
    formula: string;
  } | null;
  rest: { recent_until: string | null; favorites_after: string | null; stash_after: string | null };
  facet_cos: Record<string, number> | null;
  mixes: Array<OutcomeFields & { mix_id: number; created_at: string; state: string; position: number; generator: string | null; generator_title: string | null; score: number | null }>;
  waves: Array<OutcomeFields & { session_id: string; first_handed_at: string; position: number; generator: string | null; score: number | null; value: number | null }>;
}

export interface MapFacet {
  id: string;
  weight: number;
  artists: string[];
  x: number;
  y: number;
  fx: number;
  fy: number;
  color_index: number;
}

export type MapLayer = 'profile' | 'mix' | 'wave';

export interface MapPoint {
  key: string;
  artist: string;
  title: string;
  layers: MapLayer[];
  x: number;
  y: number;
  fx: number;
  fy: number;
  s: number | null;
  p: number | null;
  n: number | null;
  facet_id: string | null;
  facet_cos: number[];
  seed: boolean;
  liked: boolean;
  disliked: boolean;
  negative: boolean;
  familiar: boolean | null;
  generator: string | null;
  generator_title: string | null;
  mix_position: number | null;
  wave_position: number | null;
  outcome: Outcome | null;
}

export interface MapOut {
  available: boolean;
  reason: string | null;
  space_id: string | null;
  basis: 'global' | 'user';
  explained: number[];
  facets: MapFacet[];
  points: MapPoint[];
  missing: Array<{ key: string; artist: string; title: string; layers: string[] }>;
  truncated: boolean;
  mix_id: number | null;
  wave_id: string | null;
  layout_note: string;
}

export interface MatrixCell {
  s: number;
  p: number | null;
  n: number | null;
  liked: boolean;
  disliked: boolean;
  heard: number;
}

export interface MatrixOut {
  model: TasteModel;
  by: 'track' | 'artist';
  now: string;
  users: Array<{ id: number; username: string }>;
  rows: Array<{
    key: string;
    artist: string;
    title: string | null;
    owned: boolean | null;
    cells: Record<string, MatrixCell>;
    sum_pos: number;
    min: number;
    max: number;
    users_positive: number;
    users_negative: number;
  }>;
  total: number;
  offset: number;
}

export interface MixRow {
  id: number;
  created_at: string;
  closed_at: string | null;
  status: string;
  title: string;
  mode: string;
  mode_title: string;
  taste_model: string | null;
  size: number;
  downloaded: number;
  launches: number;
  started: number;
  heard: number;
  finished: number;
  early_skips: number;
  likes: number;
  dislikes: number;
  top_generators: Array<{ generator: string; title: string; songs: number }>;
}

export interface ScoreParts {
  generators: number;
  sound: number;
  popularity: number;
  track_penalty: number;
  artist_penalty: number;
  maturity: number | null;
  total: number;
}

export interface MixItem extends InsightsName, OutcomeFields {
  position: number;
  state: 'active' | 'reserve' | 'failed' | string;
  familiar: boolean;
  generator: string | null;
  generator_title: string | null;
  gens: Record<string, number> | null;
  score: number | null;
  score_parts: ScoreParts | null;
  sim: number | null;
  rank: number | null;
  profile: { key: string; s: number; positive: number | null; negative: number | null; artist_score: number | null; familiar: boolean } | null;
  downloaded_for_mix: boolean;
  cleanup: string | null;
  heard_outside: boolean;
}

export interface MixOut extends MixRow {
  description: string;
  nd_playlist_id: string | null;
  funnel: {
    generators: Array<{ generator: string; title: string; tracks: number | null; used: number | null; seconds: number | null; error: string | null; off: boolean }> | null;
    candidates: number | null;
    rejected: Record<string, number> | null;
    kept: number | null;
    sound_scored: number | null;
    active: number;
    familiar: number;
    new: number;
    reserve: number;
    failed: number;
    launches: number;
    started: number;
    heard: number;
    finished: number;
    early_skips: number;
    liked: number;
    disliked: number;
  };
  seeds: Array<InsightsName & { key: string; facet_id: string | null }>;
  seed_labels: string[] | null;
  facets: Array<{ id: string; weight: number; artists: string[]; size: number }> | null;
  maturity: number | null;
  embedder: unknown;
  build_seconds: number | null;
  items: MixItem[];
  launch_list: Array<{ id: string; started_at: string; attribution: string | null; origin: string | null; client: string | null; started: number; heard: number; finished: number; early_skips: number; likes: number; dislikes: number }>;
  artists: number;
}

export interface WaveRow {
  session_id: string;
  created_at: string;
  last_activity_at: string;
  origin: string | null;
  client: string | null;
  restart_of: string | null;
  restart: boolean;
  seed_kind: string;
  seed: { title: string | null; artist: string | null; ref: string | null; nd_song_id: string | null; artist_ref: string | null; nd_artist_id: string | null } | null;
  mode: string | null;
  taste_model: string | null;
  next_calls: number | null;
  handed: number | null;
  tracks_started: number;
  started: number;
  heard: number;
  finished: number;
  early_skips: number;
  late_skips: number;
  likes: number;
  dislikes: number;
  listened_ms: number;
  downloads: number | null;
  cleared: boolean;
  reconstructed: boolean;
}

export interface WavesOut {
  total: number;
  offset: number;
  rows: WaveRow[];
  launches: number;
  restarts: number;
  by_origin: Record<string, number>;
  by_seed_kind: Record<string, number>;
}

export interface WaveOut extends WaveRow {
  gens: Record<string, { tracks?: number; seconds?: number; error?: string | null; off?: boolean; title: string }>;
  seeds_used: string[] | null;
  items: Array<InsightsName & OutcomeFields & {
    position: number;
    first_handed_at: string;
    last_handed_at: string;
    handed_count: number;
    generator: string | null;
    generator_title: string | null;
    gens: Record<string, number> | null;
    familiar: boolean | null;
    score: number | null;
    sim: number | null;
    value: number | null;
  }>;
  events: Array<{ ts: string; kind: string; track: InsightsName; position_s: number | null; client: string | null }>;
}

export interface Range {
  from?: string;
  to?: string;
}

const get = <T,>(path: string, signal?: AbortSignal) => request<T>('GET', path, { signal });

export const insightsApi = {
  overview: (r: Range, signal?: AbortSignal) => get<OverviewOut>(withQuery('/api/admin/insights/overview', { ...r }), signal),
  listening: (id: number, r: Range, signal?: AbortSignal) =>
    get<ListeningOut>(withQuery(`/api/admin/users/${id}/listening`, { ...r }), signal),
  taste: (id: number, model: TasteModel | undefined, signal?: AbortSignal) =>
    get<TasteOut>(withQuery(`/api/admin/users/${id}/taste`, { model }), signal),
  tracks: (id: number, q: { model?: TasteModel; filter?: string; q?: string; sort?: string; limit?: number; offset?: number }, signal?: AbortSignal) =>
    get<TasteTracksOut>(withQuery(`/api/admin/users/${id}/taste/tracks`, q), signal),
  track: (id: number, key: string, model: TasteModel | undefined, signal?: AbortSignal) =>
    get<TasteTrackOut>(withQuery(`/api/admin/users/${id}/taste/track`, { key, model }), signal),
  map: (id: number, q: { model?: TasteModel; basis?: string; mix?: string; wave?: string }, signal?: AbortSignal) =>
    get<MapOut>(withQuery(`/api/admin/users/${id}/taste/map`, q), signal),
  matrix: (q: { by?: string; filter?: string; q?: string; sort?: string; limit?: number; offset?: number; model?: TasteModel }, signal?: AbortSignal) =>
    get<MatrixOut>(withQuery('/api/admin/taste/matrix', q), signal),
  mixes: (id: number, signal?: AbortSignal) => get<MixRow[]>(withQuery(`/api/admin/users/${id}/mixes`, { limit: 30 }), signal),
  mix: (id: number, signal?: AbortSignal) => get<MixOut>(`/api/admin/mixes/${id}`, signal),
  waves: (id: number, q: Range & { origin?: string; seed_kind?: string; limit?: number; offset?: number }, signal?: AbortSignal) =>
    get<WavesOut>(withQuery(`/api/admin/users/${id}/waves`, { ...q }), signal),
  wave: (sid: string, signal?: AbortSignal) => get<WaveOut>(`/api/admin/waves/${encodeURIComponent(sid)}`, signal),
};

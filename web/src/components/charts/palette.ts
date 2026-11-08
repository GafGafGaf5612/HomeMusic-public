// Colors of the admin charts: CSS tokens only (both themes, index.css).

/** The color of a facet by its rank (`color_index`, 0-based); five tokens, then they repeat. */
export const facetColor = (index: number | null | undefined): string =>
  index == null || index < 0 ? 'var(--color-dim)' : `var(--color-facet-${(index % 5) + 1})`;

/** A series color by its place: the facet tokens, the last series is neutral. */
export const seriesColor = (i: number, n: number): string => (i === n - 1 && n > 5 ? 'var(--color-dim)' : facetColor(i));

/** Listening sources grouped for the bars by day (INSIGHTS.md 9.3: Волна / Микс / Свои списки / Поиск / Другое). */
export const SOURCE_GROUPS: Array<{ id: string; label: string; color: string; sources: string[] }> = [
  { id: 'wave', label: 'Волна', color: 'var(--color-facet-1)', sources: ['wave'] },
  { id: 'mix', label: 'Микс', color: 'var(--color-facet-2)', sources: ['mix'] },
  { id: 'own', label: 'Свои списки', color: 'var(--color-facet-3)', sources: ['playlist', 'album', 'artist', 'liked', 'library', 'queue'] },
  { id: 'search', label: 'Поиск', color: 'var(--color-facet-4)', sources: ['search'] },
  { id: 'other', label: 'Другое', color: 'var(--color-dim)', sources: [] },
];

export function sourceGroup(source: string): string {
  return SOURCE_GROUPS.find((g) => g.sources.includes(source))?.id ?? 'other';
}

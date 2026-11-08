/** Inside the HomeMusic Android app (WebView): the app has its own navigation, so the PWA tab bar is not drawn.
 * The app opens `/?embedded=1#/route` and adds `HomeMusicAndroid/<version>` to the User-Agent. */
export const EMBEDDED =
  new URLSearchParams(window.location.search).has('embedded') ||
  'HomeMusicAndroid' in window ||
  navigator.userAgent.includes('HomeMusicAndroid/');

/** A plain browser: the PWA is the whole service on its own — «Главная», the Navidrome library («Понравившиеся»,
 * playlists, artists), the full player with its queue, its own tab bar. Inside the app these screens are the app's
 * native ones (Tempus), so the PWA there is only the Hub: catalog, downloads, assistant, own files. Screens and
 * actions of the standalone service check this flag; routes of it are not opened inside the app (`App.tsx`). */
export const STANDALONE = !EMBEDDED;

if (EMBEDDED) document.documentElement.classList.add('hm-embedded');

/** The app's theme (light/dark and its background color). The app sends it on every page load and theme change
 * (`HubWebHolder.applyTheme`); the last one is kept so the next start paints right away. */
export function applyAppTheme(light: boolean, bg?: string) {
  const root = document.documentElement;
  root.classList.toggle('hm-light', light);
  if (bg) root.style.setProperty('--hm-bg', bg);
  else root.style.removeProperty('--hm-bg');
  try {
    localStorage.setItem('hm-app-theme', JSON.stringify({ light, bg }));
  } catch {
    /* storage unavailable: the app sends the theme again on the next load */
  }
}

if (EMBEDDED) {
  try {
    const saved = JSON.parse(localStorage.getItem('hm-app-theme') ?? 'null') as { light?: boolean; bg?: string } | null;
    if (saved) applyAppTheme(!!saved.light, saved.bg);
  } catch {
    /* nothing saved yet */
  }
  (window as unknown as { hmApplyTheme?: typeof applyAppTheme }).hmApplyTheme = applyAppTheme;
}

// Inline stroke icons (24x24, currentColor) — no icon font, no extra requests.
const PATHS = {
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14Zm9 16-4.35-4.35',
  download: 'M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5M5 19h14',
  library: 'M5 4v16M10 4v16M15 5l4 14.5',
  wave: 'M3 12c2.25-4.5 3.75-4.5 6 0s3.75 4.5 6 0 3.75-4.5 6 0',
  minus: 'M5 12h14',
  shuffleSmart: 'M3 7h3.5c2 0 3.2 1 4.3 2.6l2.4 4.8C14.3 16 15.5 17 17.5 17H21m0 0-3-3m3 3-3 3M3 17h3.5c1.3 0 2.2-.4 3-1.2M18.5 2.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z',
  sparkle: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Zm6 12 .8 2.2L21 18l-2.2.8L18 21l-.8-2.2L15 18l2.2-.8L18 15Z',
  more: 'M5 12h.01M12 12h.01M19 12h.01',
  check: 'M5 12.5 10 17.5 19 7',
  x: 'M6 6l12 12M18 6 6 18',
  back: 'M15 5l-7 7 7 7',
  chevron: 'M9 5l7 7-7 7',
  down: 'M5 9l7 7 7-7',
  home: 'M4 11 12 4l8 7v8.5a.5.5 0 0 1-.5.5H15v-6H9v6H4.5a.5.5 0 0 1-.5-.5V11Z',
  shuffle: 'M3 7h3.5c2 0 3.2 1 4.3 2.6l2.4 4.8C14.3 16 15.5 17 17.5 17H21m0 0-3-3m3 3-3 3M3 17h3.5c1.3 0 2.2-.4 3-1.2M21 7h-3.5c-1.3 0-2.2.4-3 1.2M21 7l-3-3m3 3-3 3',
  repeat: 'M17 3l3 3-3 3M4 11V9.5A3.5 3.5 0 0 1 7.5 6H20M7 21l-3-3 3-3m13-2v1.5a3.5 3.5 0 0 1-3.5 3.5H4',
  repeat1: 'M17 3l3 3-3 3M4 11V9.5A3.5 3.5 0 0 1 7.5 6H20M7 21l-3-3 3-3m13-2v1.5a3.5 3.5 0 0 1-3.5 3.5H4M11.5 10.5 13 9.5v5',
  queue: 'M4 6h12M4 12h12M4 18h8m6-3v6l4-3-4-3Z',
  radio: 'M4.9 19.1a10 10 0 0 1 0-14.2m14.2 0a10 10 0 0 1 0 14.2M7.8 16.2a6 6 0 0 1 0-8.4m8.4 0a6 6 0 0 1 0 8.4M12 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z',
  plus: 'M12 5v14M5 12h14',
  upload: 'M12 20V9m0 0-4.5 4.5M12 9l4.5 4.5M5 5h14',
  note: 'M9 18V6l10-2v12M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm10-2a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
  volume: 'M4 9v6h4l5 4V5L8 9H4Zm12-1a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14',
  play: 'M7 5v14l12-7L7 5Z',
  pause: 'M8 5v14M16 5v14',
  next: 'M6 5v14l9-7-9-7Zm12 0v14',
  prev: 'M18 5v14l-9-7 9-7ZM6 5v14',
  ban: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM5.6 5.6l12.8 12.8',
  dislike: 'M7 4h9.5a2 2 0 0 1 2 1.6l1.3 6.5a2 2 0 0 1-2 2.4H14l.8 3.6a1.9 1.9 0 0 1-3.5 1.3L7 13.5M7 4H4v9.5h3V4Z',
  heart: 'M12 20s-7.5-4.6-9.3-9.4C1.6 7.6 3.6 4.5 6.8 4.5c2.1 0 3.6 1.2 5.2 3 1.6-1.8 3.1-3 5.2-3 3.2 0 5.2 3.1 4.1 6.1C19.5 15.4 12 20 12 20Z',
  star: 'M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8L3.5 9.7l5.9-.8L12 3.5Z',
  playlist: 'M4 6h12M4 11h12M4 16h7M17 14v6m-3-3h6',
  edit: 'M4 20h4L19 9l-4-4L4 16v4Zm9-13 4 4',
  trash: 'M5 7h14M10 7V4h4v3m-7 0 1 13h8l1-13',
  history: 'M4 12a8 8 0 1 0 2.3-5.7M4 4v5h5M12 8v4l3 2',
  bell: 'M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15L6 16Zm4 4h4',
  logout: 'M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10',
  link: 'M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1',
  image: 'M4 5h16v14H4V5Zm0 11 4.5-4.5 4 4L15 13l5 5M15 9.5h.01',
  refresh: 'M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7',
  alert: 'M12 4 2.5 20h19L12 4Zm0 6v4m0 3h.01',
  disc: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm0 6.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5Z',
  external: 'M14 4h6v6M20 4l-9 9M18 14v6H4V6h6',
  move: 'M5 12h14m0 0-4-4m4 4-4 4M5 5v14',
  merge: 'M6 4v5a5 5 0 0 0 5 5h7m0 0-3-3m3 3-3 3M6 20v-4',
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-7 8a7 7 0 0 1 14 0',
  install: 'M12 3v12m0 0-4-4m4 4 4-4M4 17v3h16v-3',
  lock: 'M7 11V8a5 5 0 0 1 10 0v3M5 11h14v9H5v-9Z',
  clear: 'M4 7h16M7 12h10M10 17h4',
  paste: 'M9 4h6v3H9V4ZM7 5H5v15h14V5h-2',
  send: 'M4 12 20 4l-5 16-3.5-6.5L4 12Zm7.5 1.5L20 4',
  transfer: 'M4 8h13m0 0-4-4m4 4-4 4M20 16H7m0 0 4-4m-4 4 4 4',
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({
  name,
  size = 24,
  className = '',
  stroke = 2,
  filled = false,
}: {
  name: IconName;
  size?: number;
  className?: string;
  stroke?: number;
  /** solid shape (♥ that is on) */
  filled?: boolean;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={filled ? 'currentColor' : 'none'}
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      <path d={PATHS[name]} />
    </svg>
  );
}

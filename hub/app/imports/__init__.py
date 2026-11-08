"""Moving a user's collection over from another music service (the «Перенести музыку» screen, `/api/imports`).

- `sources/` — the service side: sign-in and reading the collection into a `Snapshot` (Yandex Music; Spotify and
  YouTube Music plug in the same way: a module that returns a `Snapshot`);
- `resolve.py` — matching rows against the library and the catalog, cached for everyone in `source_map`;
- `apply.py` — ♥, playlists in order, downloads below the user's own work in the queue;
- `core.py` — the import's jobs and the user's decisions.
"""

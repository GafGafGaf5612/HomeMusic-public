"""On-disk library layout (PLAN.md 6.3): safe path components and file naming."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_WHITESPACE = re.compile(r"\s+")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sanitize_component(s: str | None, max_len: int = 120) -> str:
    """Make a single path component safe on Windows/Linux/macOS file systems.

    Replaces `<>:"/\\|?*` and control chars with `_`, collapses whitespace, strips leading and trailing
    dots/spaces, avoids reserved device names and never returns an empty string. Unicode (Cyrillic) is kept.
    """
    s = _INVALID_CHARS.sub("_", s or "")
    s = _WHITESPACE.sub(" ", s).strip()
    if len(s) > max_len:
        s = s[:max_len]
    # leading dots would make a hidden file/dir (Navidrome skips those), trailing ones are invalid on Windows
    s = s.rstrip(". ").lstrip(". ")
    if s in {"", ".", ".."}:
        return "_"
    # "CON", "con.txt", "COM1 " are all reserved on Windows
    if s.split(".", 1)[0].rstrip(" ").upper() in _RESERVED:
        s = "_" + s
    return s


def track_filename(disc_no: int | None, track_no: int | None, title: str, ext: str) -> str:
    """`{Disc}-{NN} - {Title}.{ext}`; disc defaults to 1, missing track number to 0."""
    ext = ext.lstrip(".").lower() or "bin"
    stem = f"{disc_no or 1}-{(track_no or 0):02d} - {sanitize_component(title, max_len=150)}"
    return f"{stem}.{ext}"


def album_dirname(year: int | None, album: str) -> str:
    """`{Year} - {Album}`, or just `{Album}` when the year is unknown."""
    name = f"{year} - {album}" if year else album
    return sanitize_component(name)


def track_relpath(
    album_artist: str,
    year: int | None,
    album: str,
    disc_no: int | None,
    track_no: int | None,
    title: str,
    ext: str,
) -> PurePosixPath:
    """Path relative to `library/`: `{AlbumArtist}/{Year} - {Album}/{Disc}-{NN} - {Title}.{ext}`."""
    return PurePosixPath(
        sanitize_component(album_artist),
        album_dirname(year, album),
        track_filename(disc_no, track_no, title, ext),
    )


def _split_ext(name: str) -> tuple[str, str]:
    p = PurePosixPath(name)
    suffix = p.suffix
    if suffix and len(suffix) <= 6 and p.stem:
        return name[: -len(suffix)], suffix
    return name, ""


def unique_path(dir: Path, name: str) -> Path:
    """`dir/name`, or `dir/stem (2).ext`, `dir/stem (3).ext`, ... if the name is already taken."""
    candidate = dir / name
    if not candidate.exists():
        return candidate
    stem, ext = _split_ext(name)
    n = 2
    while True:
        candidate = dir / f"{stem} ({n}){ext}"
        if not candidate.exists():
            return candidate
        n += 1


def version_suffixed(name: str, suffix: str | int) -> str:
    """Name for a new version placed next to a locked file (PLAN.md 8.6).

    `version_suffixed("01-03 - Title.mp3", 2)` -> `"01-03 - Title [v2].mp3"`;
    a string suffix is used as is: `version_suffixed("a.mp3", "clean")` -> `"a [clean].mp3"`.
    """
    label = f"v{suffix}" if isinstance(suffix, int) else sanitize_component(str(suffix).strip("[] "), max_len=40)
    stem, ext = _split_ext(name)
    return f"{stem} [{label}]{ext}"


def next_version_path(path: Path) -> Path:
    """First free `stem [vN].ext` (N >= 2) next to `path`."""
    n = 2
    while True:
        candidate = path.with_name(version_suffixed(path.name, n))
        if not candidate.exists():
            return candidate
        n += 1

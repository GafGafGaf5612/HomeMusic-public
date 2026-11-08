"""Audio tag writing/reading with mutagen (PLAN.md 8.4).

All functions here are synchronous (file I/O + CPU); async callers wrap them in `asyncio.to_thread`.

Tags are kept in a canonical Vorbis-comment-like form (`{"TITLE": ["..."], ...}`) and mapped to/from
ID3v2.4 (mp3, wav, aiff), MP4 atoms (m4a) and Vorbis comments (flac, ogg vorbis, ogg opus).
`HOMEMUSIC_TRACK_ID` / `HOMEMUSIC_ALBUM_ID` are what Navidrome uses as persistent IDs (navidrome.toml).
"""

from __future__ import annotations

import base64
import datetime as dt
import re
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

import mutagen
from mutagen.aiff import AIFF
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TSRC, TXXX
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from mutagen.oggflac import OggFLAC
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field, field_validator

ReleaseType = Literal["album", "single", "ep", "compilation"]
_RELEASE_TYPES = {"album", "single", "ep", "compilation"}

# TagData field -> custom tag name (TXXX desc / MP4 freeform name / Vorbis key)
CUSTOM_FIELDS: dict[str, str] = {
    "track_uid": "HOMEMUSIC_TRACK_ID",
    "album_uid": "HOMEMUSIC_ALBUM_ID",
    "source": "HOMEMUSIC_SOURCE",
    "source_url": "HOMEMUSIC_SOURCE_URL",
    "source_date": "HOMEMUSIC_SOURCE_DATE",
    "acquired": "HOMEMUSIC_ACQUIRED",
    "deezer_track_id": "DEEZER_TRACK_ID",
    "deezer_album_id": "DEEZER_ALBUM_ID",
}

_ID3_TEXT = {"TITLE": TIT2, "ARTIST": TPE1, "ALBUMARTIST": TPE2, "ALBUM": TALB, "DATE": TDRC, "GENRE": TCON,
             "ISRC": TSRC}
_ID3_TXXX_ALIASES = {"RELEASETYPE": "MusicBrainz Album Type"}
_MP4_ATOMS = {"TITLE": "©nam", "ARTIST": "©ART", "ALBUMARTIST": "aART", "ALBUM": "©alb", "DATE": "©day",
              "GENRE": "©gen"}
_MP4_FREEFORM_ALIASES = {"RELEASETYPE": "MusicBrainz Album Type"}
_MP4_FREEFORM = "----:com.apple.iTunes:"
_DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
_YEAR_RE = re.compile(r"^(\d{4})")


class UnsupportedFormat(ValueError):
    """mutagen can't recognise the file or the container has no tag support here."""


class TagData(BaseModel):
    title: str
    artist: str  # display string, e.g. "Artist A feat. Artist B"
    artists: list[str] = []  # individual artists (multi-value ARTISTS tag)
    album: str | None = None
    album_artist: str | None = None
    track_no: int | None = None
    track_total: int | None = None
    disc_no: int | None = None
    disc_total: int | None = None
    date: str | None = None  # "YYYY" or "YYYY-MM-DD"
    genre: str | None = None
    isrc: str | None = None
    explicit: bool | None = None
    release_type: ReleaseType | None = None
    track_uid: str | None = None
    album_uid: str | None = None
    source: str | None = None
    source_url: str | None = None
    source_date: dt.date | None = None
    acquired: dt.datetime | None = None
    deezer_track_id: int | None = None
    deezer_album_id: int | None = None
    cover: bytes | None = Field(default=None, repr=False)

    @field_validator("date", mode="before")
    @classmethod
    def _date_str(cls, v: Any) -> Any:
        if isinstance(v, int):
            return str(v)
        if isinstance(v, dt.date):
            return v.isoformat()
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return None
            if not _DATE_RE.match(v):
                raise ValueError("date must be 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD'")
        return v

    @field_validator("release_type", mode="before")
    @classmethod
    def _release_type(cls, v: Any) -> Any:
        return v.strip().lower() if isinstance(v, str) else v


# --------------------------------------------------------------------------- canonical form


def _to_canonical(t: TagData) -> dict[str, list[str]]:
    """TagData -> {"VORBISKEY": [values]} (cover excluded)."""
    d: dict[str, list[str]] = {}

    def put(key: str, value: Any) -> None:
        if value is None or value == "":
            return
        d[key] = [str(value)]

    put("TITLE", t.title)
    put("ARTIST", t.artist)
    if t.artists:
        d["ARTISTS"] = [a for a in t.artists if a]
    put("ALBUMARTIST", t.album_artist)
    put("ALBUM", t.album)
    put("TRACKNUMBER", t.track_no)
    put("TRACKTOTAL", t.track_total)
    put("DISCNUMBER", t.disc_no)
    put("DISCTOTAL", t.disc_total)
    put("DATE", t.date)
    put("GENRE", t.genre)
    put("ISRC", t.isrc)
    put("RELEASETYPE", t.release_type)
    if t.explicit is not None:
        d["ITUNESADVISORY"] = ["1" if t.explicit else "2"]
    for field, key in CUSTOM_FIELDS.items():
        v = getattr(t, field)
        if isinstance(v, dt.datetime):
            v = v.isoformat(timespec="seconds")
        elif isinstance(v, dt.date):
            v = v.isoformat()
        put(key, v)
    return d


def _num_pair(s: str | None) -> tuple[int | None, int | None]:
    """'3/12' -> (3, 12); '3' -> (3, None); junk -> (None, None)."""
    if not s:
        return None, None
    parts = str(s).split("/", 1)

    def to_int(x: str) -> int | None:
        x = x.strip()
        return int(x) if x.isdigit() and int(x) > 0 else None

    return to_int(parts[0]), to_int(parts[1]) if len(parts) > 1 else None


def _advisory(v: str | None) -> bool | None:
    if v is None:
        return None
    v = str(v).strip()
    if v in {"1", "4"}:
        return True
    if v == "2":
        return False
    return None


def _from_canonical(c: dict[str, list[str]]) -> dict[str, Any]:
    """{"VORBISKEY": [values]} -> dict of TagData field values (only the ones present, not validated)."""

    def first(key: str) -> str | None:
        vals = c.get(key)
        return vals[0].strip() if vals and vals[0] is not None and str(vals[0]).strip() else None

    out: dict[str, Any] = {}
    for field, key in (("title", "TITLE"), ("album", "ALBUM"), ("album_artist", "ALBUMARTIST"),
                       ("genre", "GENRE"), ("isrc", "ISRC")):
        if (v := first(key)) is not None:
            out[field] = v

    artists = [a.strip() for a in c.get("ARTISTS", []) if a and a.strip()]
    artist_vals = [a.strip() for a in c.get("ARTIST", []) if a and a.strip()]
    if artist_vals:
        out["artist"] = artist_vals[0] if len(artist_vals) == 1 else ", ".join(artist_vals)
        if not artists and len(artist_vals) > 1:
            artists = artist_vals
    if artists:
        out["artists"] = artists

    track_no, track_total = _num_pair(first("TRACKNUMBER"))
    disc_no, disc_total = _num_pair(first("DISCNUMBER"))
    track_total = _num_pair(first("TRACKTOTAL") or first("TOTALTRACKS"))[0] or track_total
    disc_total = _num_pair(first("DISCTOTAL") or first("TOTALDISCS"))[0] or disc_total
    for field, v in (("track_no", track_no), ("track_total", track_total), ("disc_no", disc_no),
                     ("disc_total", disc_total)):
        if v is not None:
            out[field] = v

    if (d := first("DATE") or first("YEAR")) is not None:
        d = d.replace("/", "-")[:10]
        if _DATE_RE.match(d):
            out["date"] = d
        elif m := _YEAR_RE.match(d):
            out["date"] = m.group(1)

    rt = first("RELEASETYPE")
    if rt and rt.lower() in _RELEASE_TYPES:
        out["release_type"] = rt.lower()

    explicit = _advisory(first("ITUNESADVISORY"))
    if explicit is not None:
        out["explicit"] = explicit

    for field, key in CUSTOM_FIELDS.items():
        v = first(key)
        if v is None:
            continue
        if field == "source_date":
            try:
                out[field] = dt.date.fromisoformat(v[:10])
            except ValueError:
                continue
        elif field == "acquired":
            try:
                out[field] = dt.datetime.fromisoformat(v)
            except ValueError:
                continue
        elif field in {"deezer_track_id", "deezer_album_id"}:
            if v.isdigit():
                out[field] = int(v)
        else:
            out[field] = v
    return out


# --------------------------------------------------------------------------- helpers


def _open(path: Path) -> mutagen.FileType:
    try:
        f = mutagen.File(path)
    except mutagen.MutagenError as e:
        raise UnsupportedFormat(f"can't read {path.name}: {e}") from e
    if f is None:
        raise UnsupportedFormat(f"unsupported audio format: {path.name}")
    return f


def _image_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"


def _flac_picture(data: bytes) -> Picture:
    pic = Picture()
    pic.type = 3  # front cover
    pic.mime = _image_mime(data)
    pic.desc = "Cover"
    pic.data = data
    try:
        with Image.open(BytesIO(data)) as im:
            pic.width, pic.height = im.size
            pic.depth = 8 * len(im.getbands())
    except (UnidentifiedImageError, OSError):
        pass
    return pic


def _pick_front(items: list[Any], type_of: Any) -> Any | None:
    if not items:
        return None
    return next((p for p in items if type_of(p) == 3), items[0])


# --------------------------------------------------------------------------- ID3 (mp3 / wav / aiff)


def _fill_id3(tags: ID3, c: dict[str, list[str]], cover: bytes | None) -> None:
    for key, frame_cls in _ID3_TEXT.items():
        if key in c:
            tags.add(frame_cls(encoding=3, text=c[key]))
    if "TRACKNUMBER" in c:
        tags.add(TRCK(encoding=3, text="/".join([c["TRACKNUMBER"][0], *c.get("TRACKTOTAL", [])])))
    if "DISCNUMBER" in c:
        tags.add(TPOS(encoding=3, text="/".join([c["DISCNUMBER"][0], *c.get("DISCTOTAL", [])])))
    handled = {*_ID3_TEXT, "TRACKNUMBER", "TRACKTOTAL", "DISCNUMBER", "DISCTOTAL"}
    for key, values in c.items():
        if key in handled:
            continue
        tags.add(TXXX(encoding=3, desc=_ID3_TXXX_ALIASES.get(key, key), text=values))
    if cover:
        tags.add(APIC(encoding=3, mime=_image_mime(cover), type=3, desc="Cover", data=cover))


def _read_id3(tags: ID3 | None) -> tuple[dict[str, list[str]], bytes | None]:
    c: dict[str, list[str]] = {}
    if tags is None:
        return c, None
    for key, frame_cls in _ID3_TEXT.items():
        frame = tags.get(frame_cls.__name__)
        if frame is not None and frame.text:
            c[key] = [str(x) for x in frame.text]  # ID3TimeStamp -> str
    for frame_id, key in (("TRCK", "TRACKNUMBER"), ("TPOS", "DISCNUMBER")):
        frame = tags.get(frame_id)
        if frame is not None and frame.text:
            c[key] = [str(frame.text[0])]
    if "DATE" not in c and (frame := tags.get("TYER")) is not None and frame.text:
        c["DATE"] = [str(frame.text[0])]
    reverse = {v.upper(): k for k, v in _ID3_TXXX_ALIASES.items()}
    for frame in tags.getall("TXXX"):
        key = reverse.get(frame.desc.upper(), frame.desc.upper())
        c.setdefault(key, [str(x) for x in frame.text])
    pic = _pick_front(tags.getall("APIC"), lambda p: p.type)
    return c, (pic.data if pic is not None else None)


# --------------------------------------------------------------------------- MP4


def _fill_mp4(tags: Any, c: dict[str, list[str]], cover: bytes | None) -> None:
    for key, atom in _MP4_ATOMS.items():
        if key in c:
            tags[atom] = c[key]
    if "TRACKNUMBER" in c:
        tags["trkn"] = [(int(c["TRACKNUMBER"][0]), int(c.get("TRACKTOTAL", ["0"])[0]))]
    if "DISCNUMBER" in c:
        tags["disk"] = [(int(c["DISCNUMBER"][0]), int(c.get("DISCTOTAL", ["0"])[0]))]
    if "ITUNESADVISORY" in c:
        tags["rtng"] = [int(c["ITUNESADVISORY"][0])]
    handled = {*_MP4_ATOMS, "TRACKNUMBER", "TRACKTOTAL", "DISCNUMBER", "DISCTOTAL"}
    for key, values in c.items():
        if key in handled:
            continue
        name = _MP4_FREEFORM + _MP4_FREEFORM_ALIASES.get(key, key)
        tags[name] = [MP4FreeForm(v.encode("utf-8")) for v in values]
    if cover:
        fmt = MP4Cover.FORMAT_PNG if _image_mime(cover) == "image/png" else MP4Cover.FORMAT_JPEG
        tags["covr"] = [MP4Cover(cover, imageformat=fmt)]


def _read_mp4(tags: Any) -> tuple[dict[str, list[str]], bytes | None]:
    c: dict[str, list[str]] = {}
    if tags is None:
        return c, None
    for key, atom in _MP4_ATOMS.items():
        if tags.get(atom):
            c[key] = [str(x) for x in tags[atom]]
    for atom, num_key, total_key in (("trkn", "TRACKNUMBER", "TRACKTOTAL"), ("disk", "DISCNUMBER", "DISCTOTAL")):
        if tags.get(atom):
            n, total = (tuple(tags[atom][0]) + (0, 0))[:2]
            if n:
                c[num_key] = [str(n)]
            if total:
                c[total_key] = [str(total)]
    reverse = {v.upper(): k for k, v in _MP4_FREEFORM_ALIASES.items()}
    prefix = _MP4_FREEFORM.lower()
    for name, values in tags.items():
        if not name.lower().startswith(prefix):
            continue
        key = name[len(prefix):].upper()
        key = reverse.get(key, key)
        c[key] = [bytes(v).decode("utf-8", "replace") for v in values]
    if tags.get("rtng"):
        c["ITUNESADVISORY"] = [str(tags["rtng"][0])]
    covers = tags.get("covr") or []
    return c, (bytes(covers[0]) if covers else None)


# --------------------------------------------------------------------------- Vorbis comments


def _fill_vorbis(tags: Any, c: dict[str, list[str]]) -> None:
    for key, values in c.items():
        tags[key] = values


def _read_vorbis(tags: Any) -> dict[str, list[str]]:
    c: dict[str, list[str]] = {}
    if tags is None:
        return c
    for key, value in tags:  # list of (key, value) pairs; keys are case-insensitive
        c.setdefault(key.upper(), []).append(value)
    return c


def _ogg_cover(c: dict[str, list[str]]) -> bytes | None:
    pics: list[Picture] = []
    for b64 in c.get("METADATA_BLOCK_PICTURE", []):
        try:
            pics.append(Picture(base64.b64decode(b64)))
        except Exception:
            continue
    pic = _pick_front(pics, lambda p: p.type)
    return pic.data if pic is not None else None


# --------------------------------------------------------------------------- public API


def write_tags(path: Path, t: TagData) -> None:
    """Replace all tags in the file with `t` (retag from scratch). Supports mp3, m4a, flac, ogg, opus, wav, aiff."""
    path = Path(path)
    f = _open(path)
    c = _to_canonical(t)
    if isinstance(f, MP3):
        tags = ID3()
        _fill_id3(tags, c, t.cover)
        tags.save(path, v1=0, v2_version=4)  # replaces the whole ID3v2 tag, drops ID3v1
    elif isinstance(f, (WAVE, AIFF)):
        if f.tags is None:
            f.add_tags()
        else:
            f.tags.clear()
        _fill_id3(f.tags, c, t.cover)
        f.save(v2_version=4)
    elif isinstance(f, MP4):
        if f.tags is None:
            f.add_tags()
        else:
            f.tags.clear()
        _fill_mp4(f.tags, c, t.cover)
        f.save()
    elif isinstance(f, FLAC):
        if f.tags is None:
            f.add_tags()
        else:
            f.tags.clear()
        f.clear_pictures()
        _fill_vorbis(f.tags, c)
        if t.cover:
            f.add_picture(_flac_picture(t.cover))
        f.save(deleteid3=True)
    elif isinstance(f, (OggVorbis, OggOpus, OggFLAC)):
        if f.tags is None:
            f.add_tags()
        else:
            f.tags.clear()
        _fill_vorbis(f.tags, c)
        if t.cover:
            f.tags["METADATA_BLOCK_PICTURE"] = [base64.b64encode(_flac_picture(t.cover).write()).decode("ascii")]
        f.save()
    else:
        raise UnsupportedFormat(f"tag writing not supported for {type(f).__name__}: {path.name}")


def _read_raw(path: Path) -> tuple[mutagen.FileType, dict[str, list[str]], bytes | None]:
    f = _open(path)
    if isinstance(f, (MP3, WAVE, AIFF)):
        c, cover = _read_id3(f.tags)
    elif isinstance(f, MP4):
        c, cover = _read_mp4(f.tags)
    elif isinstance(f, FLAC):
        c = _read_vorbis(f.tags)
        pic = _pick_front(list(f.pictures), lambda p: p.type)
        cover = pic.data if pic is not None else _ogg_cover(c)
    elif isinstance(f, (OggVorbis, OggOpus, OggFLAC)):
        c = _read_vorbis(f.tags)
        cover = _ogg_cover(c)
    else:
        raise UnsupportedFormat(f"tag reading not supported for {type(f).__name__}: {path.name}")
    return f, c, cover


def read_tag_fields(path: Path) -> dict[str, Any]:
    """Existing tags as TagData field values (only present ones, incl. `cover`); not validated."""
    _, c, cover = _read_raw(Path(path))
    fields = _from_canonical(c)
    if cover:
        fields["cover"] = cover
    return fields


def read_tags(path: Path) -> dict[str, Any]:
    """Normalized tags for upload pre-fill / re-tagging. Missing values are None."""
    f, c, cover = _read_raw(Path(path))
    v = _from_canonical(c)
    d = v.get("date")
    length = getattr(getattr(f, "info", None), "length", None)
    return {
        "title": v.get("title"),
        "artist": v.get("artist"),
        "artists": v.get("artists", []),
        "album": v.get("album"),
        "album_artist": v.get("album_artist"),
        "track_no": v.get("track_no"),
        "track_total": v.get("track_total"),
        "disc_no": v.get("disc_no"),
        "disc_total": v.get("disc_total"),
        "year": int(d[:4]) if d else None,
        "date": d,
        "genre": v.get("genre"),
        "isrc": v.get("isrc"),
        "release_type": v.get("release_type"),
        "duration_s": float(length) if length else None,
        "has_cover": bool(cover),
        "explicit": v.get("explicit"),
        "homemusic_track_id": v.get("track_uid"),
        "homemusic_album_id": v.get("album_uid"),
        "source": v.get("source"),
        "source_url": v.get("source_url"),
        "source_date": v.get("source_date"),
        "deezer_track_id": v.get("deezer_track_id"),
        "deezer_album_id": v.get("deezer_album_id"),
    }


def read_cover(path: Path) -> bytes | None:
    """Embedded front cover (or the first picture), raw bytes as stored."""
    return _read_raw(Path(path))[2]


def update_tags(path: Path, **fields: Any) -> TagData:
    """Change only the given TagData fields (`cover=None` removes the cover), keep everything else.

    Implemented as read existing -> merge -> write_tags. Returns the written TagData.
    """
    unknown = set(fields) - set(TagData.model_fields)
    if unknown:
        raise TypeError(f"unknown tag fields: {', '.join(sorted(unknown))}")
    path = Path(path)
    merged = read_tag_fields(path)
    merged.update(fields)
    missing = [k for k in ("title", "artist") if not merged.get(k)]
    if missing:
        raise ValueError(f"{path.name}: no {' / '.join(missing)} in the file; pass them explicitly")
    t = TagData(**merged)
    write_tags(path, t)
    return t


def prepare_cover(data: bytes, max_px: int = 1000) -> bytes:
    """Normalize a cover image: RGB JPEG, longest side <= max_px, quality 90. Raises ValueError on bad input."""
    try:
        with Image.open(BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.getchannel("A"))
                im = bg
            elif im.mode != "RGB":
                im = im.convert("RGB")
            if max(im.size) > max_px:
                im.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
            out = BytesIO()
            im.save(out, format="JPEG", quality=90, optimize=True)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise ValueError(f"invalid cover image: {e}") from e

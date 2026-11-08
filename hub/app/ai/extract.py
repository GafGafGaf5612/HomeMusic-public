"""Turning user input into track rows (PLAN.md 8.5.1): screenshots via a vision model, free text via the LLM
(with a no-LLM fast path for pasted "Artist - Title" lists).

The model output is untrusted: it is validated and cleaned with pydantic, rows without a title are dropped.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.ai.llm import LLMClient, LLMUsage, image_part

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
MAX_PARALLEL_TILES = 3

SourceApp = Literal["yandex_music", "vk", "spotify", "shazam", "apple_music", "telegram", "youtube", "other"]
PageType = Literal["playlist", "album", "history", "now_playing", "search", "chart", "other"]
IntentKind = Literal["tracks", "album", "latest_album", "discography", "artist_top", "unknown"]
SOURCE_APPS: tuple[str, ...] = SourceApp.__args__  # type: ignore[attr-defined]
PAGE_TYPES: tuple[str, ...] = PageType.__args__  # type: ignore[attr-defined]
INTENT_KINDS: tuple[str, ...] = IntentKind.__args__  # type: ignore[attr-defined]
RECORD_TYPES = ("album", "ep", "single", "compilation")
_ELLIPSIS_RE = re.compile(r"(\.{3}|…)\s*$")


@lru_cache
def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def _clean_str(v: Any) -> str | None:
    if v is None:
        return None
    if not isinstance(v, str):
        v = str(v)
    v = " ".join(v.replace(" ", " ").split())
    return v or None


# --------------------------------------------------------------------------- models


class ExtractedItem(BaseModel):
    artist_raw: str = ""
    title_raw: str
    album_raw: str | None = None
    duration: str | None = None
    truncated: bool = False
    guess_full: str | None = None
    conf: float = 0

    @field_validator("artist_raw", "title_raw", mode="before")
    @classmethod
    def _req_str(cls, v: Any) -> str:
        return _clean_str(v) or ""

    @field_validator("album_raw", "guess_full", mode="before")
    @classmethod
    def _opt_str(cls, v: Any) -> str | None:
        return _clean_str(v)

    @field_validator("duration", mode="before")
    @classmethod
    def _duration(cls, v: Any) -> str | None:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if v <= 0:
                return None
            s = round(v)
            return f"{s // 60}:{s % 60:02d}"
        return _clean_str(v)

    @field_validator("truncated", mode="before")
    @classmethod
    def _truncated(cls, v: Any) -> bool:
        if isinstance(v, str):
            return v.strip().lower() in {"true", "1", "yes", "да"}
        return bool(v)

    @field_validator("conf", mode="before")
    @classmethod
    def _conf(cls, v: Any) -> float:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.0
        if f != f:  # NaN
            return 0.0
        if 2 <= f <= 100:  # a percentage (85 → 0.85); 1.2 is just clipped
            f /= 100
        return min(1.0, max(0.0, f))

    @model_validator(mode="after")
    def _mark_truncated(self) -> ExtractedItem:
        if not self.truncated and (_ELLIPSIS_RE.search(self.title_raw) or _ELLIPSIS_RE.search(self.artist_raw)):
            self.truncated = True
        if self.guess_full and self.guess_full == self.title_raw:
            self.guess_full = None
        return self


class ExtractionContext(BaseModel):
    playlist_name: str | None = None
    album_title: str | None = None
    album_artist: str | None = None

    @field_validator("playlist_name", "album_title", "album_artist", mode="before")
    @classmethod
    def _opt_str(cls, v: Any) -> str | None:
        return _clean_str(v)

    def is_empty(self) -> bool:
        return not (self.playlist_name or self.album_title or self.album_artist)


def _items_before(v: Any) -> list[dict | ExtractedItem]:
    """Keep only rows that are dicts/items with a non-empty title; invalid rows are dropped, not fatal."""
    out: list[dict | ExtractedItem] = []
    for row in v or []:
        if isinstance(row, ExtractedItem):
            if row.title_raw:
                out.append(row)
            continue
        if not isinstance(row, dict):
            continue
        row = dict(row)
        # tolerate the short names some models use
        row.setdefault("artist_raw", row.pop("artist", None))
        row.setdefault("title_raw", row.pop("title", None))
        try:
            item = ExtractedItem.model_validate(row)
        except ValidationError as e:
            log.info("dropping invalid row %r: %s", row, e)
            continue
        if item.title_raw:
            out.append(item)
    return out


class Extraction(BaseModel):
    source_app: SourceApp = "other"
    page_type: PageType = "other"
    context: ExtractionContext = Field(default_factory=ExtractionContext)
    items: list[ExtractedItem] = Field(default_factory=list)

    @field_validator("source_app", mode="before")
    @classmethod
    def _source_app(cls, v: Any) -> str:
        v = (_clean_str(v) or "other").lower().replace(" ", "_").replace("-", "_")
        aliases = {"yandex": "yandex_music", "yamusic": "yandex_music", "vk_music": "vk", "vkontakte": "vk",
                   "apple": "apple_music", "youtube_music": "youtube", "yt_music": "youtube"}
        v = aliases.get(v, v)
        return v if v in SOURCE_APPS else "other"

    @field_validator("page_type", mode="before")
    @classmethod
    def _page_type(cls, v: Any) -> str:
        v = (_clean_str(v) or "other").lower().replace(" ", "_").replace("-", "_")
        return v if v in PAGE_TYPES else "other"

    @field_validator("context", mode="before")
    @classmethod
    def _context(cls, v: Any) -> Any:
        return v if isinstance(v, (dict, ExtractionContext)) else {}

    @field_validator("items", mode="before")
    @classmethod
    def _items(cls, v: Any) -> list:
        return _items_before(v if isinstance(v, list) else [])


class TextIntent(BaseModel):
    kind: IntentKind = "unknown"
    artist: str | None = None
    album: str | None = None
    tracks: list[ExtractedItem] = Field(default_factory=list)
    playlist_name: str | None = None
    record_types: list[str] = Field(default_factory=list)

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v: Any) -> str:
        v = (_clean_str(v) or "unknown").lower()
        return v if v in INTENT_KINDS else "unknown"

    @field_validator("artist", "album", "playlist_name", mode="before")
    @classmethod
    def _opt_str(cls, v: Any) -> str | None:
        v = _clean_str(v)
        return (v.strip("«»\"'“”").strip() or None) if v else None

    @field_validator("tracks", mode="before")
    @classmethod
    def _tracks(cls, v: Any) -> list:
        return _items_before(v if isinstance(v, list) else [])

    @field_validator("record_types", mode="before")
    @classmethod
    def _record_types(cls, v: Any) -> list[str]:
        out: list[str] = []
        for t in v if isinstance(v, list) else []:
            t = (_clean_str(t) or "").lower()
            if t in RECORD_TYPES and t not in out:
                out.append(t)
        return out


def parse_extraction(data: Any) -> Extraction:
    """Validate raw model output into an Extraction (lenient on garbage, strict on types)."""
    if not isinstance(data, dict):
        return Extraction()
    try:
        return Extraction.model_validate(data)
    except ValidationError as e:
        log.warning("model output failed validation, keeping the rows only: %s", e)
        return Extraction(items=_items_before(data.get("items") if isinstance(data.get("items"), list) else []))


# --------------------------------------------------------------------------- tool schemas

_NULLABLE_STR = {"type": ["string", "null"]}

REPORT_TRACKS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "source_app": {"type": "string", "enum": list(SOURCE_APPS)},
        "page_type": {"type": "string", "enum": list(PAGE_TYPES)},
        "context": {
            "type": "object",
            "properties": {
                "playlist_name": _NULLABLE_STR,
                "album_title": _NULLABLE_STR,
                "album_artist": _NULLABLE_STR,
            },
        },
        "items": {
            "type": "array",
            "description": "Track rows of the page's main list, top to bottom.",
            "items": {
                "type": "object",
                "properties": {
                    "artist_raw": {"type": "string", "description": "Artist(s) exactly as shown"},
                    "title_raw": {"type": "string", "description": "Track title exactly as shown"},
                    "album_raw": _NULLABLE_STR,
                    "duration": {"type": ["string", "null"], "description": "e.g. 3:45"},
                    "truncated": {"type": "boolean", "description": "Title or artist is cut off (… or edge)"},
                    "guess_full": {"type": ["string", "null"], "description": "Full title if truncated and known"},
                    "conf": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["artist_raw", "title_raw", "truncated", "conf"],
            },
        },
    },
    "required": ["source_app", "page_type", "context", "items"],
}

REPORT_INTENT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(INTENT_KINDS)},
        "artist": _NULLABLE_STR,
        "album": _NULLABLE_STR,
        "tracks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"artist_raw": {"type": "string"}, "title_raw": {"type": "string"}},
                "required": ["artist_raw", "title_raw"],
            },
        },
        "playlist_name": _NULLABLE_STR,
        "record_types": {"type": "array", "items": {"type": "string", "enum": list(RECORD_TYPES)}},
    },
    "required": ["kind"],
}


# --------------------------------------------------------------------------- images


def sniff_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/png"


def _tile_starts(height: int, tile_h: int, overlap: int) -> list[int]:
    step = max(1, tile_h - overlap)
    starts = [0]
    while starts[-1] + tile_h < height:
        starts.append(min(starts[-1] + step, height - tile_h))
    return starts


def tile_image(data: bytes, max_h: int = 2600, tile_h: int = 2200, overlap: int = 300) -> list[bytes]:
    """Cut a tall screenshot into overlapping full-width vertical tiles (PNG). Images up to `max_h` are returned as-is.

    Raises ValueError if the data is not an image Pillow can read.
    """
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        raise ValueError("Не удалось открыть изображение") from e
    w, h = img.size
    if h <= max_h:
        return [data]
    if img.mode not in ("RGB", "RGBA", "L", "LA"):
        img = img.convert("RGBA" if "A" in img.getbands() or img.mode == "P" else "RGB")
    tiles: list[bytes] = []
    for top in _tile_starts(h, tile_h, overlap):
        buf = io.BytesIO()
        img.crop((0, top, w, min(h, top + tile_h))).save(buf, format="PNG", optimize=False)
        tiles.append(buf.getvalue())
    return tiles


# --------------------------------------------------------------------------- merge / dedupe

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def norm_key(s: str | None) -> str:
    """casefold, ё→е, no punctuation/ellipsis, single spaces."""
    s = unicodedata.normalize("NFKC", s or "").casefold().replace("ё", "е")
    s = _ELLIPSIS_RE.sub("", s.strip())
    s = _PUNCT_RE.sub(" ", s).replace("_", " ")
    return " ".join(s.split())


def _prefix_match(a: str, b: str, a_trunc: bool, b_trunc: bool) -> bool:
    """Equal, or the truncated one is a prefix (>= 3 chars) of the other."""
    if a == b:
        return True
    if a_trunc and len(a) >= 3 and b.startswith(a):
        return True
    return b_trunc and len(b) >= 3 and a.startswith(b)


def _same_row(x: ExtractedItem, y: ExtractedItem) -> bool:
    xa, ya = norm_key(x.artist_raw), norm_key(y.artist_raw)
    xt, yt = norm_key(x.title_raw), norm_key(y.title_raw)
    if not xt or not yt:
        return False
    return _prefix_match(xt, yt, x.truncated, y.truncated) and _prefix_match(xa, ya, x.truncated, y.truncated)


def _better(keep: ExtractedItem, other: ExtractedItem) -> ExtractedItem:
    """Merge two readings of one row: prefer the non-truncated / longer one, fill gaps from the other."""
    def score(i: ExtractedItem) -> tuple:
        return (not i.truncated, len(norm_key(i.title_raw)) + len(norm_key(i.artist_raw)))

    best, rest = (keep, other) if score(keep) >= score(other) else (other, keep)
    upd: dict[str, Any] = {"conf": max(best.conf, rest.conf)}
    for f in ("album_raw", "duration"):
        if not getattr(best, f) and getattr(rest, f):
            upd[f] = getattr(rest, f)
    if best.truncated and not best.guess_full and rest.guess_full:
        upd["guess_full"] = rest.guess_full
    return best.model_copy(update=upd)


def dedupe_items(items: list[ExtractedItem]) -> list[ExtractedItem]:
    """Drop repeated rows (tile overlaps, overlapping screenshots), keeping the first position and the fullest text."""
    out: list[ExtractedItem] = []
    for it in items:
        for i, prev in enumerate(out):
            if _same_row(prev, it):
                out[i] = _better(prev, it)
                break
        else:
            out.append(it)
    return out


def merge_extractions(parts: list[Extraction]) -> Extraction:
    source_app = next((p.source_app for p in parts if p.source_app != "other"), "other")
    page_type = next((p.page_type for p in parts if p.page_type != "other"), "other")
    context = next((p.context for p in parts if not p.context.is_empty()), ExtractionContext())
    items = dedupe_items([it for p in parts for it in p.items])
    return Extraction(source_app=source_app, page_type=page_type, context=context, items=items)


# --------------------------------------------------------------------------- screenshots


async def _extract_tile(
    llm: LLMClient, tile: bytes, label: str, sem: asyncio.Semaphore
) -> tuple[Extraction, LLMUsage]:
    content = [
        {"type": "text", "text": f"{label}. Report the track rows of the main list via report_tracks."},
        image_part(tile, sniff_mime(tile)),
    ]
    async with sem:
        args, usage = await llm.call_tool(
            system=load_prompt("extract_screenshot"),
            user_content=content,
            tool_name="report_tracks",
            tool_description="Report the music app, page type, page context and the track rows seen in the image.",
            parameters=REPORT_TRACKS_SCHEMA,
            max_tokens=8000,
        )
    return parse_extraction(args), usage


async def extract_from_images(images: list[bytes], llm: LLMClient) -> tuple[Extraction, LLMUsage]:
    """Recognize track rows on one or more screenshots (tiles run concurrently, at most 3 at once)."""
    tiled = await asyncio.gather(*(asyncio.to_thread(tile_image, img) for img in images))
    jobs: list[tuple[bytes, str]] = []
    for si, tiles in enumerate(tiled):
        for ti, tile in enumerate(tiles):
            label = f"Screenshot {si + 1} of {len(images)}"
            if len(tiles) > 1:
                label += f", vertical part {ti + 1} of {len(tiles)} (parts overlap by a few rows)"
            jobs.append((tile, label))
    sem = asyncio.Semaphore(MAX_PARALLEL_TILES)
    results = await asyncio.gather(*(_extract_tile(llm, t, label, sem) for t, label in jobs))
    usage = LLMUsage()
    for _, u in results:
        usage = usage + u
    merged = merge_extractions([e for e, _ in results])
    log.info(
        "extracted %d rows from %d image(s) / %d tile(s), app=%s page=%s, cost=$%.5f",
        len(merged.items), len(images), len(jobs), merged.source_app, merged.page_type, usage.cost_usd,
    )
    return merged, usage


# --------------------------------------------------------------------------- free text

_LIST_LINE_RE = re.compile(
    r"^\s*(?:[-*•]\s*|\d{1,3}\s*[.)]\s*)?"  # bullets / numbering
    r"(?P<artist>\S.*?)\s+[-–—]\s+(?P<title>\S.*?)"
    r"(?:\s+\(?(?P<dur>\d{1,2}:\d{2})\)?)?\s*$"
)


def parse_track_list(text: str) -> list[ExtractedItem] | None:
    """Fast path: >= 2 lines shaped like "Artist - Title" (also –/—, numbering, trailing 3:45) → rows, else None."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    rows: list[ExtractedItem] = []
    for ln in lines:
        if m := _LIST_LINE_RE.match(ln):
            rows.append(ExtractedItem(artist_raw=m["artist"], title_raw=m["title"], duration=m["dur"], conf=1.0))
    if len(rows) >= 2 and len(rows) * 2 >= len(lines):
        return rows
    return None


async def parse_text_request(text: str, llm: LLMClient) -> tuple[TextIntent, LLMUsage]:
    """Understand a free-text request («скачай последний альбом X», a pasted track list, …)."""
    text = (text or "").strip()
    if not text:
        return TextIntent(kind="unknown"), LLMUsage()
    if rows := parse_track_list(text):
        return TextIntent(kind="tracks", tracks=rows), LLMUsage()
    args, usage = await llm.call_tool(
        system=load_prompt("parse_text"),
        user_content=f"User request:\n<<<\n{text}\n>>>",
        tool_name="report_intent",
        tool_description="Report what music the user asks for.",
        parameters=REPORT_INTENT_SCHEMA,
        max_tokens=2000,
    )
    try:
        intent = TextIntent.model_validate(args)
    except ValidationError as e:
        log.warning("intent failed validation: %s", e)
        intent = TextIntent(kind="unknown")
    if intent.kind == "tracks":
        intent.tracks = [t.model_copy(update={"conf": t.conf or 1.0}) for t in intent.tracks]
        if not intent.tracks:
            intent.kind = "unknown"
    return intent, usage

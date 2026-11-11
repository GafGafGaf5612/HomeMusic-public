"""Artist/title normalization for matching (PLAN.md 8.5.3).

Pipeline: NFKC -> unify dashes/quotes -> `ё→е` -> trailing "..." sets `truncated` -> bracket groups and " - " suffixes
are classified (guests / version / explicit-clean marker / kept text) -> unbracketed "feat." guests are cut ->
casefold + punctuation removal. Every string also gets a common Latin form (`latin`) so Cyrillic and Latin spellings
of the same name compare well ("Кино" ~ "Kino", "Тима Белорусских" ~ "Tima Belorusskih").

Artist splitting heuristics (the first name is the main artist, the rest go to `featured`):
- `feat./ft./featuring/при уч./при участии/vs.` always split;
- `,` and `;` always split, except before "the" ("Tyler, The Creator");
- ` x ` / ` × ` (surrounded by spaces) split ("Macan x Jakone", but not "Malcolm X");
- `&` / `+` split unless both sides are single words ("Simon & Garfunkel") or the right side starts with "the"
  ("Florence + the Machine");
- ` and ` / ` и ` split only when both sides have at least two words ("Король и Шут" is kept);
- `/` never splits ("AC/DC").
An unsplit `&` becomes "and" (or "и" for Cyrillic names), so "Simon & Garfunkel" == "Simon and Garfunkel".
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from unidecode import unidecode

from app.providers.base import TrackQuery


@dataclass(frozen=True)
class Norm:
    text: str  # normalized main string (no guests, no version)
    latin: str  # `text` in a common Latin form
    featured: tuple[str, ...] = ()
    version: str | None = None  # lowercase version tag, e.g. "remastered 2019", "remix", "live"
    truncated: bool = False


# ---------------------------------------------------------------- low-level text helpers

_DASHES = "‐‑‒–—―−⁃﹘﹣－"
_PREP_TABLE = str.maketrans(
    {
        **{ch: "-" for ch in _DASHES},
        **{ch: '"' for ch in "«»“”„‟"},
        **{ch: "'" for ch in "‘’‚‛`´′"},
        "ё": "е",
        "Ё": "Е",
    }
)
_CYR_RE = re.compile(r"[Ѐ-ӿ]")


def has_cyrillic(s: str | None) -> bool:
    return bool(s) and _CYR_RE.search(s) is not None


def _prep(s: str | None) -> str:
    """NFKC, unified dashes/quotes, `ё→е`, collapsed spaces (case preserved). NFKC turns "…" into "..."."""
    s = unicodedata.normalize("NFKC", s or "").translate(_PREP_TABLE)
    return " ".join(s.split())


def _strip_truncation(s: str) -> tuple[str, bool]:
    t = s.rstrip()
    if t.endswith(".."):
        return t.rstrip(". ").rstrip(), True
    return t, False


def _clean(s: str) -> str:
    """Casefold and drop punctuation: apostrophes/dots/slashes vanish ("AC/DC" -> "acdc"), the rest become spaces."""
    s = s.casefold().replace("ё", "е")
    if "&" in s:
        s = s.replace("&", " и " if has_cyrillic(s) else " and ")
    s = s.replace("$", "s")
    s = re.sub(r"['./\\]", "", s)
    s = re.sub(r"[\W_]+", " ", s)
    return " ".join(s.split())


_RU_LAT = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
        "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
        "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya", "і": "i", "ї": "yi", "є": "ye", "ґ": "g", "ў": "u",
    }
)  # fmt: skip
# Applied to both sides after transliteration to fold common spelling variants (kh/h, ks/x, iy/y, j/y; щ as
# shh/shch, ё as yo — Deezer lists some Russian releases in GOST 7.79-B: "Chto-to eshhyo").
_LAT_SQUASH = (("shch", "sch"), ("shh", "sch"), ("yo", "e"), ("dzh", "j"), ("kh", "h"), ("ks", "x"), ("iy", "y"),
               ("j", "y"))


def to_latin(s: str) -> str:
    """Common Latin form of a (cleaned) string: ru->lat table, unidecode for the rest, spelling variants folded."""
    t = unidecode(s.casefold().translate(_RU_LAT)).casefold()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    for a, b in _LAT_SQUASH:
        t = t.replace(a, b)
    return " ".join(t.split())


# ---------------------------------------------------------------- markers and versions

# Re-uploads of tracks edited after the 2026 law name the original "(без цензуры)", "(правильная версия)"...
_UNCENSORED = r"uncensored|(?:без|до)\s+цензур\w*|нецензур\w*|правильн\w*\s+верси\w*"
_UNCENSORED_RE = re.compile(rf"(?<!\w)(?:{_UNCENSORED})(?!\w)", re.IGNORECASE)
_EXPLICIT_RE = re.compile(rf"(?<!\w)(?:explicit|dirty|nsfw|{_UNCENSORED})(?!\w)|(?<![\w+])18\+", re.IGNORECASE)
_CLEAN_RE = re.compile(
    r"(?<!\w)(?:clean(?:\s+edit)?|censored|radio\s+(?:edit|version)|радио\s+верси\w*|без\s+мата|цензур\w*)(?!\w)",
    re.IGNORECASE,
)
_MARKER_FILLER_RE = re.compile(r"(?<!\w)(?:version|versión|версия|ver|edition|content|lyrics|mix)(?!\w)", re.IGNORECASE)

# (kind, pattern) in priority order; the kind is what counts when comparing versions.
_KIND_PATTERNS: list[tuple[str, str]] = [
    ("karaoke", r"karaoke|караоке"),
    ("instrumental", r"instrumental\w*|инструментал\w*|минус\w*|minus\s?one|backing\s+track|a\s?cappella|acapella"),
    ("cover", r"cover|covered\s+by|кавер\w*"),
    ("slowed", r"slowed|reverb|замедл\w*"),
    ("sped up", r"sped\s?up|speed\s?up|nightcore|ускор\w*"),
    ("8d", r"8d(?:\s+audio)?"),
    ("bass boosted", r"bass\s?boost\w*"),
    ("remix", r"remix\w*|rmx|ремикс\w*|bootleg|rework|flip|(?<!original )(?<!album )mix|vip|dub"),
    ("acoustic", r"acoustic\w*|акустик\w*|акустич\w*|unplugged|piano\s+version"),
    ("live", r"live|лайв|концерт\w*|concert|живой|живьем"),
    ("demo", r"demo|демо"),
    ("extended", r"extended"),
    ("edit", r"(?<!radio )(?<!clean )edit"),
]
_KIND_RES = [(kind, re.compile(rf"(?<!\w)(?:{pat})(?!\w)", re.IGNORECASE)) for kind, pat in _KIND_PATTERNS]
# Version-ish words that do not change the recording (no penalty when compared to the plain title).
_BENIGN = (
    r"remaster\w*|ремастер\w*|version|versión|версия|ver|mono|stereo|original\s+mix|album\s+version|soundtrack|ost|"
)
_BENIGN += r"deluxe|bonus\s+track|anniversary|single\s+version|edition"
_VERSION_RE = re.compile(rf"(?<!\w)(?:{'|'.join(p for _, p in _KIND_PATTERNS)}|{_BENIGN})(?!\w)", re.IGNORECASE)

_TITLE_CREDIT_RE = re.compile(
    r"^(?:(?:из\s+)?(?:фильма|сериала|к\s*/\s*ф)|саундтрек|prod\.?\s+by)(?!\w)", re.IGNORECASE
)

_BRACKET_FEAT_RE = re.compile(
    r"^\s*(?:featuring|feat|ft|при\s+участии|при\s+уч|with|vs|versus)(?:\.|(?=\s))\s*(.*)$", re.IGNORECASE | re.DOTALL
)
_ARTIST_FEAT_RE = re.compile(
    r"(?<!\w)(?:featuring|feat|ft|при\s+участии|при\s+уч|vs|versus)(?:\.|(?=\s))\s*", re.IGNORECASE
)
_TITLE_FEAT_RE = re.compile(
    r"\s(?:featuring\s+|feat(?:\.\s*|\s+)|ft\.\s*|при\s+участии\s+|при\s+уч(?:\.\s*|\s+))", re.IGNORECASE
)
_GUEST_SPLIT_RE = re.compile(r"\s*[,;&+]\s*|\s+(?:x|×|and|и|vs\.?)\s+", re.IGNORECASE)
_DASH_SPLIT_RE = re.compile(r"\s+-\s+")


def version_kind(version: str | None) -> str | None:
    """Canonical kind of a version tag ("remix", "live", "slowed"...). Remaster/OST/album version -> None (benign)."""
    if not version:
        return None
    for kind, rx in _KIND_RES:
        if rx.search(version):
            return kind
    return None


def _is_pure_marker(c: str) -> bool:
    """True if `c` is only an explicit/clean marker, e.g. "Explicit", "Clean Version", "18+"."""
    if not (_EXPLICIT_RE.search(c) or _CLEAN_RE.search(c)):
        return False
    rest = _MARKER_FILLER_RE.sub(" ", _CLEAN_RE.sub(" ", _EXPLICIT_RE.sub(" ", c)))
    return not re.sub(r"[\W_]+", "", rest)


# ---------------------------------------------------------------- bracket parsing

_OPEN = {"(": ")", "[": "]", "{": "}"}


def _top_groups(s: str) -> list[tuple[int, int, str]]:
    """Top-level bracket groups as (start, end, inner). An unclosed group (truncated title) runs to the end."""
    groups: list[tuple[int, int, str]] = []
    stack: list[str] = []
    start = 0
    for i, ch in enumerate(s):
        if ch in _OPEN:
            if not stack:
                start = i
            stack.append(_OPEN[ch])
        elif ch in ")]}" and stack:
            stack.pop()
            if not stack:
                groups.append((start, i + 1, s[start + 1 : i]))
    if stack:
        groups.append((start, len(s), s[start + 1 :]))
    return groups


def _flatten(inner: str) -> str:
    """Remove nested marker groups, unwrap other nested groups: "Soundtrack Version (Explicit)" -> "Soundtrack Version"."""
    out, pos = [], 0
    for start, end, sub in _top_groups(inner):
        out.append(inner[pos:start])
        sub = _flatten(sub)
        if not _is_pure_marker(sub):
            out.append(f" {sub} ")
        pos = end
    out.append(inner[pos:])
    return " ".join("".join(out).split())


def _classify(c: str) -> tuple[str, str]:
    """Classify bracket/suffix content: ("feat", guests) | ("marker", "") | ("version", text) | ("keep", text)."""
    c = c.strip()
    if not c:
        return "marker", ""
    if m := _BRACKET_FEAT_RE.match(c):
        return "feat", m.group(1)
    if _is_pure_marker(c) or _TITLE_CREDIT_RE.match(c):
        return "marker", ""
    if _VERSION_RE.search(c):
        return "version", c
    return "keep", c


def _split_guests(s: str) -> list[str]:
    return [g for g in (p.strip() for p in _GUEST_SPLIT_RE.split(s)) if g]


def _parse_title(raw: str) -> tuple[str, list[str], list[str], bool]:
    """-> (main title, guests, version strings, truncated), all un-cleaned."""
    s, truncated = _strip_truncation(_prep(raw))
    guests: list[str] = []
    versions: list[str] = []
    out, pos = [], 0
    for start, end, inner in _top_groups(s):
        out.append(s[pos:start])
        pos = end
        kind, payload = _classify(_flatten(inner))
        if kind == "feat":
            guests += _split_guests(payload)
        elif kind == "version":
            versions.append(payload)
        elif kind == "keep":
            out.append(f" {payload} ")
    out.append(s[pos:])
    s = " ".join("".join(out).split())

    parts = _DASH_SPLIT_RE.split(s)
    suffix_versions: list[str] = []
    while len(parts) > 1:
        kind, payload = _classify(_flatten(parts[-1]))
        if kind == "keep":
            break
        parts.pop()
        if kind == "feat":
            guests += _split_guests(payload)
        elif kind == "version":
            suffix_versions.insert(0, payload)
    s = " - ".join(parts)

    if m := _TITLE_FEAT_RE.search(s):
        guests += _split_guests(s[m.end() :])
        s = s[: m.start()]
    return s, guests, versions + suffix_versions, truncated


def _version_text(versions: list[str]) -> str | None:
    cleaned = [_clean(_CLEAN_RE.sub(" ", _EXPLICIT_RE.sub(" ", v))) for v in versions]
    text = " ".join(v for v in cleaned if v)
    return text or None


def _one_word(s: str) -> bool:
    return len(s.split()) <= 1


def _split_artist(s: str) -> tuple[str, list[str]]:
    guests: list[str] = []
    # a leading "feat." has no main artist to attach guests to
    if (m := _ARTIST_FEAT_RE.search(s)) and m.start() > 0:
        guests += _split_guests(s[m.end() :])
        s = s[: m.start()].strip()
    parts = re.split(r"\s*[,;](?!\s*the\s)\s*", s, flags=re.IGNORECASE)
    if len(parts) > 1 and parts[0]:
        s = parts[0]
        guests = [g for p in parts[1:] for g in _split_guests(p)] + guests
    if (m := re.search(r"\s+[x×]\s+", s, re.IGNORECASE)) and m.start() > 0 and s[m.end() :].strip():
        guests = _split_guests(s[m.end() :]) + guests
        s = s[: m.start()]
    if m := re.match(r"^(.+?)\s*[&+]\s*(.+)$", s):
        left, right = m.group(1).strip(), m.group(2).strip()
        if not (_one_word(left) and _one_word(right)) and not right.casefold().startswith("the "):
            s, guests = left, _split_guests(right) + guests
    if m := re.match(r"^(.+?)\s+(?:and|и)\s+(.+)$", s, re.IGNORECASE):
        left, right = m.group(1).strip(), m.group(2).strip()
        if len(left.split()) >= 2 and len(right.split()) >= 2 and not right.casefold().startswith("the "):
            s, guests = left, _split_guests(right) + guests
    return s.strip(), guests


def _dedupe(names: list[str], exclude: str) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for n in names:
        c = _clean(n)
        if c and c != exclude:
            seen.setdefault(c, None)
    return tuple(seen)


# ---------------------------------------------------------------- public API


def normalize_title(s: str | None) -> Norm:
    main, guests, versions, truncated = _parse_title(s or "")
    text = _clean(main)
    return Norm(text, to_latin(text), _dedupe(guests, ""), _version_text(versions), truncated)


def normalize_artist(s: str | None) -> Norm:
    raw, truncated = _strip_truncation(_prep(s or ""))
    guests: list[str] = []
    out, pos = [], 0
    for start, end, inner in _top_groups(raw):
        out.append(raw[pos:start])
        pos = end
        kind, payload = _classify(_flatten(inner))
        if kind == "feat":
            guests += _split_guests(payload)
        elif kind == "keep":
            out.append(f" {payload} ")
    out.append(raw[pos:])
    main, more = _split_artist(" ".join("".join(out).split()))
    text = _clean(main)
    return Norm(text, to_latin(text), _dedupe(more + guests, text), None, truncated)


def split_version(title: str) -> tuple[str, str | None]:
    """Split a raw title into (title without version/explicit markers, lowercase version or None).

    Case and guest brackets are kept: "Song (feat. X) [Live] (Explicit)" -> ("Song (feat. X)", "live").
    """
    s = _prep(title)
    versions: list[str] = []
    out, pos = [], 0
    for start, end, inner in _top_groups(s):
        out.append(s[pos:start])
        pos = end
        kind, payload = _classify(_flatten(inner))
        if kind == "version":
            versions.append(payload)
        elif kind != "marker":
            out.append(s[start:end])
    out.append(s[pos:])
    parts = _DASH_SPLIT_RE.split(" ".join("".join(out).split()))
    suffix: list[str] = []
    while len(parts) > 1:
        kind, payload = _classify(_flatten(parts[-1]))
        if kind not in ("version", "marker"):
            break
        parts.pop()
        if kind == "version":
            suffix.insert(0, payload)
    return " - ".join(parts).strip(), _version_text(versions + suffix)


def explicit_marker(s: str | None) -> bool | None:
    """True for Explicit/18+, False for Clean/Radio Edit, None if unmarked.

    Only bracket groups and " - " suffixes are inspected for Clean, so a song called "Clean" is not a clean version;
    a bare "Explicit"/"18+" (whole string or trailing "18+") counts as explicit.
    """
    s = _prep(s)
    if not s:
        return None
    hay = " | ".join([inner for _, _, inner in _top_groups(s)] + _DASH_SPLIT_RE.split(s)[1:])
    if _EXPLICIT_RE.search(hay) or re.search(r"(?<![\w+])18\+$", s) or (_is_pure_marker(s) and _EXPLICIT_RE.search(s)):
        return True
    if _CLEAN_RE.search(hay):
        return False
    return None


def uncensored_marker(s: str | None) -> bool | None:
    """A re-upload's own word on censorship: True for "(без цензуры)"/"(правильная версия)"/"uncensored", False for
    a clean/censored copy ("(без мата)", "(Clean)"), None otherwise. A label's plain "Explicit" says nothing here:
    the edits after the 2026 law kept it."""
    if _UNCENSORED_RE.search(_prep(s)):
        return True
    return False if explicit_marker(s) is False else None


def drop_markers(s: str | None) -> str:
    """The title without its explicit/clean marker groups: "Кайфоград (без цензуры)" -> "Кайфоград"."""
    s = _prep(s)
    out, pos = [], 0
    for start, end, inner in _top_groups(s):
        if _is_pure_marker(_flatten(inner)):
            out.append(s[pos:start])
            pos = end
    out.append(s[pos:])
    parts = _DASH_SPLIT_RE.split(" ".join("".join(out).split()))
    while len(parts) > 1 and _is_pure_marker(parts[-1]):
        parts.pop()
    return " - ".join(parts)


def fingerprint(artist: str, title: str) -> str:
    """Script-independent "artist|title" key (main artist, title without guests/version) for de-duplication."""
    return f"{normalize_artist(artist).latin}|{normalize_title(title).latin}"


def is_russian_query(q: TrackQuery) -> bool:
    """Russian source order: Cyrillic in artist/title, or the user said lang="ru"."""
    return (q.lang or "").casefold().startswith("ru") or has_cyrillic(q.artist) or has_cyrillic(q.title)

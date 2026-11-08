"""What a source of an import returns: the user's collection as plain data, in the service's order."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

CollectionKind = Literal["likes", "playlist", "albums", "artists", "dislikes"]
# what the items of a collection are
ITEM_KIND: dict[str, str] = {"likes": "track", "playlist": "track", "dislikes": "track", "albums": "album",
                             "artists": "artist"}


class SourceError(Exception):
    """The service refused or failed (message is user-facing, Russian)."""


class AuthPending(Exception):
    """The user has not confirmed the sign-in yet."""


@dataclass
class SnapItem:
    """A track ({artist, artists, title, album, duration_s, liked_at}), an album ({artist, title, year}) or an
    artist ({name}). `external_id` is the service's id: the key of `source_map` and of a repeated import."""

    external_id: str
    raw: dict[str, Any]


@dataclass
class SnapCollection:
    kind: CollectionKind
    external_id: str
    title: str
    items: list[SnapItem] = field(default_factory=list)
    cover_url: str | None = None
    selected: bool = True


@dataclass
class Snapshot:
    account: str | None
    collections: list[SnapCollection] = field(default_factory=list)

"""Reading .torrent files: bencode, the v1 info hash and the file list (BEP 3)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


class BencodeError(ValueError):
    """Not a valid .torrent file."""


MAX_DEPTH = 64


def _decode(data: bytes, i: int, depth: int, spans: dict[str, tuple[int, int]] | None) -> tuple[Any, int]:
    if depth > MAX_DEPTH:
        raise BencodeError("too deep")
    if i >= len(data):
        raise BencodeError("unexpected end")
    c = data[i:i + 1]
    if c == b"i":
        end = data.index(b"e", i)
        raw = data[i + 1:end]
        if not raw or raw in (b"-", b"-0") or (raw[:1] == b"0" and len(raw) > 1):
            raise BencodeError(f"bad integer {raw!r}")
        return int(raw), end + 1
    if c == b"l":
        i += 1
        out: list[Any] = []
        while data[i:i + 1] != b"e":
            v, i = _decode(data, i, depth + 1, None)
            out.append(v)
        return out, i + 1
    if c == b"d":
        i += 1
        d: dict[bytes, Any] = {}
        while data[i:i + 1] != b"e":
            k, i = _decode(data, i, depth + 1, None)
            if not isinstance(k, bytes):
                raise BencodeError("dict key is not a string")
            start = i
            v, i = _decode(data, i, depth + 1, None)
            if spans is not None and depth == 0 and k == b"info":
                spans["info"] = (start, i)
            d[k] = v
        return d, i + 1
    if c.isdigit():
        colon = data.index(b":", i)
        n = int(data[i:colon])
        start = colon + 1
        if start + n > len(data):
            raise BencodeError("string past the end")
        return data[start:start + n], start + n
    raise BencodeError(f"unexpected byte {c!r} at {i}")


def bdecode(data: bytes) -> Any:
    try:
        v, end = _decode(data, 0, 0, None)
    except (ValueError, IndexError) as e:
        raise BencodeError(str(e)) from e
    if end != len(data):
        raise BencodeError("trailing data")
    return v


def bencode(v: Any) -> bytes:
    """The opposite of `bdecode` (tests build .torrent files with it)."""
    if isinstance(v, bool):
        raise TypeError("bool")
    if isinstance(v, int):
        return b"i%de" % v
    if isinstance(v, str):
        v = v.encode()
    if isinstance(v, bytes):
        return b"%d:%s" % (len(v), v)
    if isinstance(v, list):
        return b"l" + b"".join(bencode(x) for x in v) + b"e"
    if isinstance(v, dict):
        items = sorted((k.encode() if isinstance(k, str) else k, x) for k, x in v.items())
        return b"d" + b"".join(bencode(k) + bencode(x) for k, x in items) + b"e"
    raise TypeError(type(v).__name__)


@dataclass(frozen=True)
class TorrentFile:
    path: str  # "<torrent name>/<dir>/<file>", as qBittorrent names it (content layout "Original")
    size: int
    offset: int = 0  # where the file starts in the torrent's data (padding files included): its pieces


@dataclass(frozen=True)
class TorrentMeta:
    info_hash: str  # v1, lower-case hex: qBittorrent's id of the torrent
    name: str
    files: list[TorrentFile]
    piece_length: int = 0

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)


def _text(b: Any) -> str:
    if not isinstance(b, bytes):
        raise BencodeError("expected a string")
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp1251", errors="replace")  # old rutracker releases


def parse_torrent(data: bytes) -> TorrentMeta:
    spans: dict[str, tuple[int, int]] = {}
    try:
        root, end = _decode(data, 0, 0, spans)
    except (ValueError, IndexError) as e:
        raise BencodeError(str(e)) from e
    if end != len(data) or not isinstance(root, dict) or "info" not in spans:
        raise BencodeError("no info dictionary")
    info = root[b"info"]
    if not isinstance(info, dict) or b"pieces" not in info:
        raise BencodeError("not a v1 torrent")
    a, b = spans["info"]
    info_hash = hashlib.sha1(data[a:b]).hexdigest()
    name = _text(info.get(b"name.utf-8") or info.get(b"name") or b"")
    if not name or "/" in name or name in (".", ".."):
        raise BencodeError("bad name")
    files: list[TorrentFile] = []
    if b"files" in info:
        offset = 0
        for f in info[b"files"]:
            size = int(f[b"length"])
            if b"p" in f.get(b"attr", b""):  # BEP 47 padding: not listed, but it takes its place in the pieces
                offset += size
                continue
            parts = [_text(p) for p in (f.get(b"path.utf-8") or f.get(b"path") or [])]
            if not parts or any(p in ("", ".", "..") or "/" in p for p in parts):
                raise BencodeError("bad file path")
            files.append(TorrentFile("/".join([name, *parts]), size, offset))
            offset += size
    else:
        files.append(TorrentFile(name, int(info[b"length"])))
    return TorrentMeta(info_hash=info_hash, name=name, files=files, piece_length=int(info.get(b"piece length", 0)))

"""ffprobe/ffmpeg wrappers: file info, silence detection and post-download checks (PLAN.md 8.3)."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
from pathlib import Path

from pydantic import BaseModel

log = logging.getLogger(__name__)

_SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*(-?[\d.]+)")
_DURATION = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


class ProbeError(Exception):
    """The file can't be opened/decoded by ffprobe (or ffprobe itself is unavailable)."""


class ProbeInfo(BaseModel):
    duration_s: float
    bitrate_kbps: int | None = None
    codec: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    format_name: str | None = None
    # format + audio stream tags, keys lower-cased (Vorbis comments live on the stream in ogg/opus)
    tags: dict[str, str] = {}


class PostcheckResult(BaseModel):
    ok: bool
    reason: str | None = None
    warnings: list[str] = []
    info: ProbeInfo | None = None
    silences: list[tuple[float, float]] = []


def ffprobe_bin() -> str:
    return os.environ.get("FFPROBE_BIN") or "ffprobe"


def ffmpeg_bin() -> str:
    return os.environ.get("FFMPEG_BIN") or "ffmpeg"


async def _run(*args: str, timeout: float) -> tuple[int, bytes, bytes]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise ProbeError(f"{args[0]} not found") from e
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except (TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, out, err


def _int(v: object) -> int | None:
    try:
        return int(float(v))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _float(v: object) -> float | None:
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f >= 0 else None  # drop NaN/inf/negatives


def parse_ffprobe_json(data: dict) -> ProbeInfo:
    """Build ProbeInfo from `ffprobe -show_format -show_streams -print_format json` output."""
    streams = data.get("streams") or []
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if audio is None:
        raise ProbeError("no audio stream")
    fmt = data.get("format") or {}
    duration = _float(audio.get("duration")) or _float(fmt.get("duration"))
    if duration is None:
        raise ProbeError("unknown duration")
    bit_rate = _int(audio.get("bit_rate")) or _int(fmt.get("bit_rate"))
    tags: dict[str, str] = {}
    for src in (fmt.get("tags") or {}, audio.get("tags") or {}):
        for k, v in src.items():
            tags.setdefault(str(k).lower(), str(v))
    return ProbeInfo(
        duration_s=duration,
        bitrate_kbps=round(bit_rate / 1000) if bit_rate else None,
        codec=audio.get("codec_name"),
        sample_rate=_int(audio.get("sample_rate")),
        channels=_int(audio.get("channels")),
        format_name=fmt.get("format_name"),
        tags=tags,
    )


async def probe(path: Path | str, timeout: float = 60) -> ProbeInfo:
    """Run ffprobe on the file. Raises ProbeError if it can't be opened or has no audio stream."""
    path = Path(path)
    if not path.is_file():
        raise ProbeError(f"file not found: {path}")
    code, out, err = await _run(
        ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path),
        timeout=timeout,
    )
    if code != 0:
        raise ProbeError(err.decode("utf-8", "replace").strip() or f"ffprobe exited with {code}")
    try:
        data = json.loads(out.decode("utf-8", "replace"))
    except json.JSONDecodeError as e:
        raise ProbeError(f"bad ffprobe output: {e}") from e
    return parse_ffprobe_json(data)


def parse_silencedetect(stderr: str) -> list[tuple[float, float]]:
    """Parse ffmpeg `silencedetect` log lines. An unterminated silence is closed at the input duration."""
    silences: list[tuple[float, float]] = []
    start: float | None = None
    for line in stderr.splitlines():
        if m := _SILENCE_START.search(line):
            start = max(0.0, float(m.group(1)))
        elif (m := _SILENCE_END.search(line)) and start is not None:
            silences.append((start, float(m.group(1))))
            start = None
    if start is not None and (m := _DURATION.search(stderr)):
        h, mnt, s = m.groups()
        end = int(h) * 3600 + int(mnt) * 60 + float(s)
        if end > start:
            silences.append((start, end))
    return silences


async def detect_silences(
    path: Path | str, min_s: float = 0.7, noise_db: float = -50, timeout: float = 300
) -> list[tuple[float, float]]:
    """(start_s, end_s) of every silence at least `min_s` long and quieter than `noise_db`."""
    code, _, err = await _run(
        ffmpeg_bin(), "-hide_banner", "-nostats", "-nostdin", "-i", str(path),
        "-vn", "-af", f"silencedetect=noise={noise_db}dB:d={min_s}", "-f", "null", "-",
        timeout=timeout,
    )
    text = err.decode("utf-8", "replace")
    if code != 0:
        raise ProbeError(text.strip().splitlines()[-1] if text.strip() else f"ffmpeg exited with {code}")
    return parse_silencedetect(text)


def middle_silences(
    silences: list[tuple[float, float]], duration: float, edge_s: float = 5
) -> list[tuple[float, float]]:
    """Silences fully inside the track body, ignoring ones touching the first/last `edge_s` seconds."""
    return [(s, e) for s, e in silences if s >= edge_s and e <= duration - edge_s]


def fmt_ts(seconds: float) -> str:
    """`94.3` -> `"1:34"`, `3725` -> `"1:02:05"`."""
    total = max(0, int(seconds))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _fmt_secs(x: float) -> str:
    return f"{x:.1f}"


async def postcheck(
    path: Path | str,
    expected_duration_s: int | None,
    tolerance_s: int = 3,
    silence_warn_s: float = 0.7,
) -> PostcheckResult:
    """Post-download checks: the file opens, duration matches, no suspicious mid-track silences.

    Duration mismatch -> ok=False. Silences only add (Russian, user-facing) warnings.
    """
    try:
        info = await probe(path)
    except ProbeError as e:
        return PostcheckResult(ok=False, reason=f"Файл не открывается: {e}")
    if info.duration_s <= 0:
        return PostcheckResult(ok=False, reason="Файл пустой (длительность 0)", info=info)
    if expected_duration_s and abs(info.duration_s - expected_duration_s) > tolerance_s:
        return PostcheckResult(
            ok=False,
            reason=(
                f"Длительность {fmt_ts(info.duration_s)} не совпадает с ожидаемой "
                f"{fmt_ts(expected_duration_s)} (допуск ±{tolerance_s} с)"
            ),
            info=info,
        )
    warnings: list[str] = []
    found: list[tuple[float, float]] = []
    try:
        found = middle_silences(await detect_silences(path, min_s=silence_warn_s), info.duration_s)
    except (ProbeError, TimeoutError) as e:
        log.warning("silencedetect failed for %s: %s", path, e)
    for start, end in found:
        warnings.append(f"Возможно, запикано: тишина {_fmt_secs(end - start)} с на {fmt_ts(start)}")
    return PostcheckResult(ok=True, warnings=warnings, info=info, silences=found)

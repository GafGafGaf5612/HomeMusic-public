import datetime as dt
import math
import shutil
import struct
import subprocess
import uuid
import wave
from io import BytesIO
from pathlib import Path

import mutagen
import pytest
from PIL import Image
from pydantic import ValidationError

from app.library.tagger import (
    TagData,
    UnsupportedFormat,
    _from_canonical,
    _to_canonical,
    prepare_cover,
    read_cover,
    read_tag_fields,
    read_tags,
    update_tags,
    write_tags,
)

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")

# ext -> ffmpeg encoder args (3 s 440 Hz sine)
FORMATS: dict[str, list[str]] = {
    "mp3": ["-c:a", "libmp3lame", "-b:a", "128k"],
    "m4a": ["-c:a", "aac", "-b:a", "128k"],
    "flac": ["-c:a", "flac"],
    "opus": ["-c:a", "libopus", "-b:a", "96k"],
    "ogg": ["-c:a", "libvorbis", "-q:a", "3"],
    "wav": ["-c:a", "pcm_s16le"],
}


def make_wav(path: Path, seconds: float = 1.0, rate: int = 8000) -> Path:
    """Tiny sine WAV without ffmpeg."""
    n = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(n)))
    return path


@pytest.fixture(scope="session")
def audio_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if FFMPEG is None:
        pytest.skip("ffmpeg not installed")
    d = tmp_path_factory.mktemp("audio")
    for ext, args in FORMATS.items():
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
             "-ar", "48000" if ext == "opus" else "44100", *args, str(d / f"sine.{ext}")],
            check=True,
        )
    return d


def png_bytes(size: tuple[int, int] = (64, 64), mode: str = "RGB", color: object = (200, 30, 30)) -> bytes:
    out = BytesIO()
    Image.new(mode, size, color).save(out, format="PNG")
    return out.getvalue()


def jpeg_cover() -> bytes:
    return prepare_cover(png_bytes((300, 300)))


def full_tags(**over: object) -> TagData:
    data = dict(
        title="Группа крови",
        artist="Кино feat. Гость",
        artists=["Кино", "Гость"],
        album="Группа крови",
        album_artist="Кино",
        track_no=1,
        track_total=11,
        disc_no=1,
        disc_total=1,
        date="1988-01-05",
        genre="Rock",
        isrc="SUA018800001",
        explicit=True,
        release_type="album",
        track_uid=str(uuid.uuid4()),
        album_uid=str(uuid.uuid4()),
        source="hitmoz",
        source_url="https://ru.hitmoz.org/song/123",
        source_date=dt.date(2019, 5, 1),
        acquired=dt.datetime(2026, 10, 18, 12, 30, 0),
        deezer_track_id=123456,
        deezer_album_id=654321,
        cover=jpeg_cover(),
    )
    data.update(over)
    return TagData(**data)


# --------------------------------------------------------------------------- pure


def test_prepare_cover_downscales_and_flattens_alpha() -> None:
    out = prepare_cover(png_bytes((2000, 1000), "RGBA", (0, 0, 0, 0)))
    with Image.open(BytesIO(out)) as im:
        assert im.format == "JPEG"
        assert im.mode == "RGB"
        assert im.size == (1000, 500)
        assert im.getpixel((10, 10)) == (255, 255, 255)  # transparent -> white


def test_prepare_cover_keeps_small_and_converts_modes() -> None:
    for mode, color in (("L", 128), ("P", 3), ("CMYK", (0, 0, 0, 0))):
        out = prepare_cover(png_bytes((300, 200), mode, color) if mode != "CMYK" else _jpeg_cmyk())
        with Image.open(BytesIO(out)) as im:
            assert im.format == "JPEG" and im.mode == "RGB" and im.size == (300, 200)


def _jpeg_cmyk() -> bytes:
    out = BytesIO()
    Image.new("CMYK", (300, 200), (0, 0, 0, 0)).save(out, format="JPEG")
    return out.getvalue()


def test_prepare_cover_custom_max() -> None:
    out = prepare_cover(png_bytes((500, 1500)), max_px=300)
    with Image.open(BytesIO(out)) as im:
        assert im.size == (100, 300)


def test_prepare_cover_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        prepare_cover(b"not an image")


def test_tagdata_validation() -> None:
    assert TagData(title="t", artist="a", date=1988).date == "1988"
    assert TagData(title="t", artist="a", date=dt.date(2001, 2, 3)).date == "2001-02-03"
    assert TagData(title="t", artist="a", release_type="Single").release_type == "single"
    assert "cover" not in repr(TagData(title="t", artist="a", cover=b"x" * 10))
    with pytest.raises(ValidationError):
        TagData(title="t", artist="a", date="5th of May")
    with pytest.raises(ValidationError):
        TagData(title="t", artist="a", release_type="mixtape")
    with pytest.raises(ValidationError):
        TagData(title="t")  # artist is required


def test_canonical_roundtrip() -> None:
    t = full_tags(cover=None)
    c = _to_canonical(t)
    assert c["ITUNESADVISORY"] == ["1"]
    assert c["ARTISTS"] == ["Кино", "Гость"]
    assert c["HOMEMUSIC_ACQUIRED"] == ["2026-10-18T12:30:00"]
    assert c["HOMEMUSIC_SOURCE_DATE"] == ["2019-05-01"]
    back = TagData(**_from_canonical(c))
    assert back == t


def test_from_canonical_foreign_values() -> None:
    v = _from_canonical({
        "TRACKNUMBER": ["3/12"], "DISCNUMBER": ["2"], "DATE": ["1999-07-01T00:00:00"], "ITUNESADVISORY": ["2"],
        "RELEASETYPE": ["Album"], "ARTIST": ["A", "B"], "DEEZER_TRACK_ID": ["abc"], "HOMEMUSIC_ACQUIRED": ["junk"],
    })
    assert v["track_no"] == 3 and v["track_total"] == 12 and v["disc_no"] == 2
    assert v["date"] == "1999-07-01"
    assert v["explicit"] is False
    assert v["release_type"] == "album"
    assert v["artist"] == "A, B" and v["artists"] == ["A", "B"]
    assert "deezer_track_id" not in v and "acquired" not in v


def test_unsupported_file(tmp_path: Path) -> None:
    p = tmp_path / "x.mp3"
    p.write_bytes(b"\x00" * 100)
    with pytest.raises(UnsupportedFormat):
        write_tags(p, TagData(title="t", artist="a"))


# --------------------------------------------------------------------------- WAV (no ffmpeg needed)


def test_wav_roundtrip_without_ffmpeg(tmp_path: Path) -> None:
    p = make_wav(tmp_path / "a.wav")
    t = full_tags()
    write_tags(p, t)
    r = read_tags(p)
    assert r["title"] == t.title and r["artist"] == t.artist and r["artists"] == t.artists
    assert r["homemusic_track_id"] == t.track_uid
    assert r["explicit"] is True and r["has_cover"] is True
    assert r["duration_s"] == pytest.approx(1.0, abs=0.01)
    assert read_cover(p) == t.cover
    with wave.open(str(p), "rb") as w:  # still a valid WAV
        assert w.getnframes() == 8000


# --------------------------------------------------------------------------- all formats (ffmpeg)


@pytest.fixture(params=list(FORMATS))
def audio_file(request: pytest.FixtureRequest, audio_dir: Path, tmp_path: Path) -> Path:
    src = audio_dir / f"sine.{request.param}"
    dst = tmp_path / src.name
    shutil.copy(src, dst)
    return dst


@needs_ffmpeg
def test_write_read_roundtrip(audio_file: Path) -> None:
    t = full_tags()
    write_tags(audio_file, t)
    r = read_tags(audio_file)
    assert r["title"] == "Группа крови"
    assert r["artist"] == "Кино feat. Гость"
    assert r["artists"] == ["Кино", "Гость"]
    assert r["album"] == "Группа крови" and r["album_artist"] == "Кино"
    assert (r["track_no"], r["track_total"], r["disc_no"], r["disc_total"]) == (1, 11, 1, 1)
    assert r["year"] == 1988
    assert r["date"] == "1988-01-05"
    assert r["genre"] == "Rock" and r["isrc"] == "SUA018800001"
    assert r["explicit"] is True
    assert r["release_type"] == "album"
    assert r["homemusic_track_id"] == t.track_uid and r["homemusic_album_id"] == t.album_uid
    assert r["source"] == "hitmoz" and r["source_url"] == t.source_url
    assert r["source_date"] == dt.date(2019, 5, 1)
    assert r["deezer_track_id"] == 123456 and r["deezer_album_id"] == 654321
    assert r["has_cover"] is True
    assert r["duration_s"] == pytest.approx(3.0, abs=0.15)
    assert read_cover(audio_file) == t.cover
    # full TagData survives a round trip
    assert TagData(**read_tag_fields(audio_file)) == t


@needs_ffmpeg
def test_retag_from_scratch_drops_old_fields(audio_file: Path) -> None:
    write_tags(audio_file, full_tags())
    write_tags(audio_file, TagData(title="New", artist="Someone", explicit=False))
    r = read_tags(audio_file)
    assert r["title"] == "New" and r["artist"] == "Someone"
    assert r["explicit"] is False
    assert r["album"] is None and r["genre"] is None and r["homemusic_track_id"] is None
    assert r["has_cover"] is False and r["artists"] == []
    # ffmpeg's own encoder tags are gone too
    f = mutagen.File(audio_file)
    keys = {str(k).upper() for k in (f.tags.keys() if f.tags else [])}
    assert not keys & {"TSSE", "ENCODER", "©TOO"}


@needs_ffmpeg
def test_update_tags_keeps_rest(audio_file: Path) -> None:
    t = full_tags()
    write_tags(audio_file, t)
    new_cover = prepare_cover(png_bytes((50, 50), color=(0, 0, 255)))
    written = update_tags(audio_file, title="Группа крови (Remastered)", genre=None, cover=new_cover)
    r = read_tags(audio_file)
    assert r["title"] == "Группа крови (Remastered)"
    assert r["genre"] is None
    assert r["homemusic_track_id"] == t.track_uid and r["homemusic_album_id"] == t.album_uid
    assert r["artist"] == t.artist and r["track_no"] == 1 and r["explicit"] is True
    assert read_cover(audio_file) == new_cover
    assert written.track_uid == t.track_uid
    update_tags(audio_file, cover=None)
    assert read_cover(audio_file) is None
    assert read_tags(audio_file)["title"] == "Группа крови (Remastered)"


@needs_ffmpeg
def test_update_tags_errors(audio_file: Path) -> None:
    with pytest.raises(TypeError):
        update_tags(audio_file, bogus=1)
    with pytest.raises(ValueError):
        update_tags(audio_file, genre="Rock")  # untagged file: no title/artist to keep
    update_tags(audio_file, title="T", artist="A")
    assert read_tags(audio_file)["title"] == "T"


@needs_ffmpeg
def test_mp3_is_id3v24_utf8(audio_dir: Path, tmp_path: Path) -> None:
    from mutagen.id3 import ID3

    p = tmp_path / "a.mp3"
    shutil.copy(audio_dir / "sine.mp3", p)
    t = full_tags()
    write_tags(p, t)
    tags = ID3(p)
    assert tags.version[:2] == (2, 4)
    assert tags["TIT2"].encoding == 3
    assert str(tags["TRCK"]) == "1/11" and str(tags["TPOS"]) == "1/1"
    assert tags["TXXX:HOMEMUSIC_TRACK_ID"].text == [t.track_uid]
    assert tags["TXXX:ITUNESADVISORY"].text == ["1"]
    assert tags["TXXX:MusicBrainz Album Type"].text == ["album"]
    assert tags["TXXX:ARTISTS"].text == ["Кино", "Гость"]
    assert tags.getall("APIC")[0].type == 3 and tags.getall("APIC")[0].mime == "image/jpeg"


@needs_ffmpeg
def test_m4a_atoms(audio_dir: Path, tmp_path: Path) -> None:
    from mutagen.mp4 import MP4

    p = tmp_path / "a.m4a"
    shutil.copy(audio_dir / "sine.m4a", p)
    t = full_tags(explicit=False)
    write_tags(p, t)
    tags = MP4(p).tags
    assert tags["©nam"] == ["Группа крови"] and tags["aART"] == ["Кино"]
    assert tags["trkn"] == [(1, 11)] and tags["rtng"] == [2]
    assert bytes(tags["----:com.apple.iTunes:HOMEMUSIC_TRACK_ID"][0]).decode() == t.track_uid
    assert [bytes(v).decode() for v in tags["----:com.apple.iTunes:ARTISTS"]] == ["Кино", "Гость"]
    assert len(tags["covr"]) == 1


@needs_ffmpeg
@pytest.mark.parametrize("ext", ["flac", "opus", "ogg"])
def test_vorbis_keys(audio_dir: Path, tmp_path: Path, ext: str) -> None:
    p = tmp_path / f"a.{ext}"
    shutil.copy(audio_dir / f"sine.{ext}", p)
    t = full_tags()
    write_tags(p, t)
    f = mutagen.File(p)
    assert f.tags["TITLE"] == ["Группа крови"]
    assert f.tags["ARTISTS"] == ["Кино", "Гость"]
    assert f.tags["TRACKNUMBER"] == ["1"] and f.tags["TRACKTOTAL"] == ["11"]
    assert f.tags["RELEASETYPE"] == ["album"] and f.tags["ITUNESADVISORY"] == ["1"]
    assert f.tags["HOMEMUSIC_ALBUM_ID"] == [t.album_uid]
    if ext == "flac":
        assert len(f.pictures) == 1 and f.pictures[0].type == 3 and f.pictures[0].width == 300
    else:
        assert len(f.tags["METADATA_BLOCK_PICTURE"]) == 1

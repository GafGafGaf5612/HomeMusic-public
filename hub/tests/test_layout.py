from pathlib import Path, PurePosixPath

import pytest

from app.library.layout import (
    album_dirname,
    next_version_path,
    sanitize_component,
    track_filename,
    track_relpath,
    unique_path,
    version_suffixed,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Кино", "Кино"),
        ('AC/DC: "Back" <in> Black?|*', "AC_DC_ _Back_ _in_ Black___"),
        ("  many   spaces  ", "many spaces"),
        ("tab\tand\nnewline", "tab_and_newline"),  # control chars are replaced, not collapsed
        ("...hidden", "hidden"),
        ("trailing dots...", "trailing dots"),
        ("", "_"),
        (None, "_"),
        ("..", "_"),
        ("CON", "_CON"),
        ("con.txt", "_con.txt"),
        ("COM1", "_COM1"),
        ("Console", "Console"),
        ("a\x00b\x1fc", "a_b_c"),
    ],
)
def test_sanitize_component(raw: str | None, expected: str) -> None:
    assert sanitize_component(raw) == expected


def test_sanitize_component_truncates() -> None:
    s = sanitize_component("x" * 300, max_len=50)
    assert len(s) == 50


def test_track_filename() -> None:
    assert track_filename(1, 3, "Группа крови", "MP3") == "1-03 - Группа крови.mp3"
    assert track_filename(None, None, "Intro", ".flac") == "1-00 - Intro.flac"
    assert track_filename(2, 12, "What?", "opus") == "2-12 - What_.opus"
    assert track_filename(1, 1, "x", "") == "1-01 - x.bin"


def test_album_dirname() -> None:
    assert album_dirname(1988, "Группа крови") == "1988 - Группа крови"
    assert album_dirname(None, "Untitled: Demo") == "Untitled_ Demo"


def test_track_relpath() -> None:
    p = track_relpath("Кино", 1988, "Группа крови", 1, 1, "Группа крови", "opus")
    assert p == PurePosixPath("Кино/1988 - Группа крови/1-01 - Группа крови.opus")
    assert isinstance(p, PurePosixPath)


def test_unique_path(tmp_path: Path) -> None:
    assert unique_path(tmp_path, "a.mp3") == tmp_path / "a.mp3"
    (tmp_path / "a.mp3").touch()
    assert unique_path(tmp_path, "a.mp3") == tmp_path / "a (2).mp3"
    (tmp_path / "a (2).mp3").touch()
    assert unique_path(tmp_path, "a.mp3") == tmp_path / "a (3).mp3"
    (tmp_path / "noext").touch()
    assert unique_path(tmp_path, "noext") == tmp_path / "noext (2)"


def test_version_suffixed() -> None:
    assert version_suffixed("1-03 - Title.mp3", 2) == "1-03 - Title [v2].mp3"
    assert version_suffixed("a.mp3", "clean") == "a [clean].mp3"
    assert version_suffixed("a.mp3", "[x/y]") == "a [x_y].mp3"


def test_next_version_path(tmp_path: Path) -> None:
    p = tmp_path / "1-01 - T.flac"
    p.touch()
    assert next_version_path(p) == tmp_path / "1-01 - T [v2].flac"
    (tmp_path / "1-01 - T [v2].flac").touch()
    assert next_version_path(p) == tmp_path / "1-01 - T [v3].flac"

#!/usr/bin/env python3
"""Pack a wheel from files on disk (no build backend): what build-wheels.sh cross-compiled for Android.

    mkwheel.py --name pydantic_core --version 2.50.1 --tag cp312-cp312-android_24_arm64_v8a --out DIR \
        --tree python/pydantic_core:pydantic_core --file target/.../lib_pydantic_core.so:pydantic_core/_pydantic_core.cpython-312.so \
        [--metadata PKG-INFO] [--requires 'typing-extensions>=4.14.1']

`--tree SRC:DEST` copies a package directory (sources only: .py, .pyi, py.typed, data files; no .so, .c, .pyx).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import zipfile
from pathlib import Path

SKIP_SUFFIXES = {".so", ".c", ".cpp", ".h", ".pyx", ".pxd", ".pxi", ".pyc", ".dylib", ".o"}


def _digest(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--tag", required=True)  # e.g. py3-none-any
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--tree", action="append", default=[])
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--metadata", type=Path)
    ap.add_argument("--requires", action="append", default=[])
    a = ap.parse_args()

    files: dict[str, bytes] = {}
    for spec in a.tree:
        src, dest = spec.rsplit(":", 1)
        for p in sorted(Path(src).rglob("*")):
            if p.is_file() and p.suffix not in SKIP_SUFFIXES and "__pycache__" not in p.parts:
                files[f"{dest}/{p.relative_to(src).as_posix()}"] = p.read_bytes()
    for spec in a.file:
        src, dest = spec.rsplit(":", 1)
        files[dest] = Path(src).read_bytes()

    dist = a.name.replace("-", "_")
    info = f"{dist}-{a.version}.dist-info"
    if a.metadata and a.metadata.is_file():
        # PKG-INFO's header block is METADATA; drop the sdist's own Requires-Dist only if we were given ours
        meta = a.metadata.read_text(encoding="utf-8")
        if a.requires:
            meta = "\n".join(line for line in meta.splitlines() if not line.startswith("Requires-Dist:")) + "\n"
    else:
        meta = f"Metadata-Version: 2.1\nName: {a.name}\nVersion: {a.version}\n"
    if a.requires:
        head, sep, body = meta.partition("\n\n")
        head += "".join(f"\nRequires-Dist: {r}" for r in a.requires)
        meta = head + sep + body if sep else head + "\n"
    pure = a.tag.endswith("-none-any")
    files[f"{info}/METADATA"] = meta.encode()
    files[f"{info}/WHEEL"] = (f"Wheel-Version: 1.0\nGenerator: homemusic-mkwheel\nRoot-Is-Purelib: {'true' if pure else 'false'}\n"
                              f"Tag: {a.tag}\n").encode()
    record = [f"{path},{_digest(data)},{len(data)}" for path, data in files.items()] + [f"{info}/RECORD,,"]
    files[f"{info}/RECORD"] = ("\n".join(record) + "\n").encode()

    a.out.mkdir(parents=True, exist_ok=True)
    whl = a.out / f"{dist}-{a.version}-{a.tag}.whl"
    with zipfile.ZipFile(whl, "w", zipfile.ZIP_DEFLATED) as z:
        for path, data in files.items():
            z.writestr(path, data)
    print(whl)


if __name__ == "__main__":
    main()

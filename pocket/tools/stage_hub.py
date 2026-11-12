#!/usr/bin/env python3
"""Stage the Hub for the phone app: its code, migrations and the built PWA, compiled to .pyc, as assets/hub.zip.

    stage_hub.py <repo root> <out zip>

The PWA must be built first (`npm run build` in web/ writes hub/app/static). Bytecode is compiled with this
interpreter (3.12, the app's version) as unchecked-hash .pyc, so the phone doesn't compile 30k lines on the first start
and file times inside the zip don't matter.
"""

from __future__ import annotations

import py_compile
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

SKIP_DIRS = {"__pycache__", "tests", ".pytest_cache", ".ruff_cache"}


def main() -> None:
    repo, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    hub = repo / "hub"
    if not (hub / "app" / "static" / "index.html").is_file():
        sys.exit("hub/app/static/index.html is missing: build the PWA first (cd web && npm ci && npm run build)")
    if sys.version_info[:2] != (3, 12):
        sys.exit(f"needs Python 3.12 (the app's), not {sys.version}")
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "hub"
        for name in ("app", "migrations"):
            shutil.copytree(hub / name, stage / name, ignore=shutil.ignore_patterns(*SKIP_DIRS, "*.pyc"))
        shutil.copy2(hub / "alembic.ini", stage / "alembic.ini")
        for py in stage.rglob("*.py"):
            rel = py.relative_to(stage)
            py_compile.compile(str(py), cfile=str(py.parent / "__pycache__" / f"{py.stem}.cpython-312.pyc"),
                               dfile=str(rel), doraise=True,
                               invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp_zip = out.with_suffix(".tmp")
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(stage.rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(stage).as_posix())
        tmp_zip.replace(out)
    print(out, out.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()

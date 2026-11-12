#!/usr/bin/env bash
# Android (arm64-v8a, API 24) wheels of the Hub's native dependencies that Chaquopy's package index doesn't have:
#   pydantic-core, jiter (Rust, PyO3), selectolax (C, lexbor), rapidfuzz (its pure-Python fallback).
# Needs: NDK (Android NDK r28+), PYTARGET (Chaquopy's target-3.12.*-arm64-v8a.zip unpacked: jniLibs/, include/),
#        cargo + rustup target aarch64-linux-android, python3. Output: $OUT/*.whl (pocket/app/build.gradle.kts reads it).
set -euo pipefail
: "${NDK:?}" "${PYTARGET:?}" "${OUT:?}" "${WORK:?}"
HERE="$(cd "$(dirname "$0")" && pwd)"
TC="$NDK/toolchains/llvm/prebuilt/$(uname -s | tr A-Z a-z)-x86_64"
API=24
CC="$TC/bin/aarch64-linux-android$API-clang"
TAG="cp312-cp312-android_${API}_arm64_v8a"
EXT=".cpython-312.so"
PYLIB="$PYTARGET/jniLibs/arm64-v8a"
LDFLAGS="-Wl,-z,max-page-size=16384"  # 16 KB pages (Android 15+ devices may use them)
mkdir -p "$OUT" "$WORK"

# the versions hub/uv.lock pins (macOS bash 3.2: no associative arrays)
V_PYDANTIC_CORE=2.50.1 V_JITER=0.17.0 V_SELECTOLAX=1.0.1 V_RAPIDFUZZ=3.14.6

fetch() {  # name version -> unpacked sdist dir on stdout
  local name=$1 ver=$2 dir
  dir="$WORK/$(echo "$name" | tr - _)-$ver"
  [ -d "$dir" ] || [ -d "$WORK/$name-$ver" ] || python3 - "$name" "$ver" "$WORK" <<'EOF'
import json, sys, tarfile, urllib.request, io
name, ver, work = sys.argv[1:]
d = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{ver}/json"))
sd = next(u for u in d["urls"] if u["packagetype"] == "sdist")
data = urllib.request.urlopen(sd["url"]).read()
import hashlib
assert hashlib.sha256(data).hexdigest() == sd["digests"]["sha256"], "sdist checksum mismatch"
tarfile.open(fileobj=io.BytesIO(data)).extractall(work, filter="data")
EOF
  [ -d "$dir" ] && echo "$dir" || echo "$WORK/$name-$ver"
}

rust_wheel() {  # sdist-dir manifest lib-name dist-name version tree-spec so-dest
  local src=$1 manifest=$2 lib=$3 dist=$4 ver=$5 tree=$6 dest=$7
  cat > "$WORK/pyo3-android.cfg" <<EOF
implementation=CPython
version=3.12
shared=true
abi3=false
lib_name=python3.12
lib_dir=$PYLIB
pointer_width=64
build_flags=
suppress_build_script_link_lines=false
EOF
  # a fresh target dir every time: cargo's incremental fingerprint check of an sdist (no git, no rerun-if-changed)
  # was seen walking far outside the package on macOS; a clean release build takes 1-3 minutes
  local tgt="$WORK/target-$lib"
  rm -rf "$tgt"
  ( cd "$src"
    CARGO_TARGET_DIR="$tgt" PYO3_CONFIG_FILE="$WORK/pyo3-android.cfg" \
    CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER="$CC" \
    CC_aarch64_linux_android="$CC" AR_aarch64_linux_android="$TC/bin/llvm-ar" \
    CARGO_TARGET_AARCH64_LINUX_ANDROID_RUSTFLAGS="-C link-arg=$LDFLAGS" \
    cargo build --release --locked --target aarch64-linux-android --manifest-path "$manifest" --lib )
  local so="$tgt/aarch64-linux-android/release/lib$lib.so"
  "$TC/bin/llvm-strip" --strip-unneeded "$so"
  "$TC/bin/llvm-readelf" -d "$so" | grep -q 'libpython3.12.so' || { echo "$so does not link libpython3.12" >&2; exit 1; }
  python3 "$HERE/mkwheel.py" --name "$dist" --version "$ver" --tag "$TAG" --out "$OUT" \
    ${tree:+--tree "$tree"} --file "$so:$dest" --metadata "$src/PKG-INFO"
}

# --- pydantic-core ---------------------------------------------------------------------------------------------
v=$V_PYDANTIC_CORE; d=$(fetch pydantic-core "$v")
rust_wheel "$d" Cargo.toml _pydantic_core pydantic_core "$v" "$d/python/pydantic_core:pydantic_core" \
  "pydantic_core/_pydantic_core$EXT"

# --- jiter -----------------------------------------------------------------------------------------------------
v=$V_JITER; d=$(fetch jiter "$v")
rust_wheel "$d" crates/jiter-python/Cargo.toml jiter_python jiter "$v" "" "jiter$EXT"

# --- selectolax (lexbor, C) ------------------------------------------------------------------------------------
v=$V_SELECTOLAX; d=$(fetch selectolax "$v")
obj="$WORK/selectolax-obj"; rm -rf "$obj"; mkdir -p "$obj"
( cd "$d"
  { find lexbor/source -name '*.c' | grep -v '/ports/' ; find lexbor/source -path '*/ports/posix/*' -name '*.c'; echo selectolax/lexbor.c; } |
    xargs -P "$(sysctl -n hw.ncpu 2>/dev/null || nproc)" -I{} sh -c \
      'o="$1/$(echo "$2" | tr / _).o"; "$0" -c -fPIC -O2 -g0 -std=c99 -DLEXBOR_STATIC -Wno-unused-variable -Wno-unused-function \
        -Wno-unreachable-code-fallthrough -Wno-deprecated-declarations -Ilexbor/source -I"$3/include/python3.12" "$2" -o "$o"' \
      "$CC" "$obj" {} "$PYTARGET"
  "$CC" -shared -o "$obj/lexbor$EXT" "$obj"/*.o -L"$PYLIB" -lpython3.12 $LDFLAGS
  "$TC/bin/llvm-strip" --strip-unneeded "$obj/lexbor$EXT" )
python3 "$HERE/mkwheel.py" --name selectolax --version "$v" --tag "$TAG" --out "$OUT" \
  --tree "$d/selectolax:selectolax" --file "$obj/lexbor$EXT:selectolax/lexbor$EXT" --metadata "$d/PKG-INFO"

# --- rapidfuzz: pure Python (its C++ part is an optional speed-up; the Hub only scores short strings) ------------
v=$V_RAPIDFUZZ; d=$(fetch rapidfuzz "$v")
python3 "$HERE/mkwheel.py" --name rapidfuzz --version "$v" --tag py3-none-any --out "$OUT" \
  --tree "$d/src/rapidfuzz:rapidfuzz" --metadata "$d/PKG-INFO"

ls -la "$OUT"

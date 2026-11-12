#!/usr/bin/env bash
# Navidrome for Android (GOOS=android, bionic through the NDK): libnavidrome.so in jniLibs/arm64-v8a.
#
# The official linux/arm64 release is a static glibc binary: its threads start with clone3, which the seccomp filter
# Android puts on every app process rejects with SIGSYS (it runs from `adb shell`, where there is no such filter, and
# dies at once inside the app). A build against bionic makes only the system calls an Android app may make.
# Needs: NDK, WORK, OUT, Node 22+ (the web UI), curl, git. Go is downloaded into $WORK (checksum-verified).
set -euo pipefail
: "${NDK:?}" "${WORK:?}" "${OUT:?}"
NAVIDROME_VERSION=${NAVIDROME_VERSION:-0.64.2}
GO_VERSION=${GO_VERSION:-1.27.4}
GO_SHA256_DARWIN_AMD64=${GO_SHA256_DARWIN_AMD64:-2b799377704ef12db39bd254995d5c348c9cc8d86562ca817724390e56c38f43}
TC="$NDK/toolchains/llvm/prebuilt/$(uname -s | tr A-Z a-z)-x86_64"
API=24
mkdir -p "$WORK" "$OUT"
cd "$WORK"

# --- Go -----------------------------------------------------------------------------------------------------------
os=$(uname -s | tr A-Z a-z); arch=$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/')
if [ ! -x "go-$GO_VERSION/bin/go" ]; then
  f="go$GO_VERSION.$os-$arch.tar.gz"
  curl -fsSL -o "$f" "https://go.dev/dl/$f"
  if [ "$os-$arch" = darwin-amd64 ]; then echo "$GO_SHA256_DARWIN_AMD64  $f" | shasum -a 256 -c -; fi
  mkdir -p "go-$GO_VERSION" && tar xzf "$f" -C "go-$GO_VERSION" --strip-components=1 && rm "$f"
fi
export GOROOT="$WORK/go-$GO_VERSION" GOPATH="$WORK/gopath" GOCACHE="$WORK/gocache" GOTOOLCHAIN=local
export PATH="$GOROOT/bin:$PATH"

# --- sources and the web UI (embedded into the binary from ui/build) ----------------------------------------------
[ -d "navidrome-$NAVIDROME_VERSION" ] ||
  git clone -q --depth 1 --branch "v$NAVIDROME_VERSION" https://github.com/navidrome/navidrome "navidrome-$NAVIDROME_VERSION"
cd "navidrome-$NAVIDROME_VERSION"
[ -f ui/build/index.html ] || ( cd ui && npm ci --no-audit --no-fund && npm run build )

# --- the binary ---------------------------------------------------------------------------------------------------
sha=$(git rev-parse --short HEAD)
CGO_ENABLED=1 GOOS=android GOARCH=arm64 CC="$TC/bin/aarch64-linux-android$API-clang" \
  CGO_LDFLAGS="-Wl,-z,max-page-size=16384" \
  go build -trimpath -tags netgo,sqlite_fts5 \
    -ldflags "-s -w -X github.com/navidrome/navidrome/consts.gitSha=$sha -X github.com/navidrome/navidrome/consts.gitTag=v$NAVIDROME_VERSION" \
    -o "$OUT/libnavidrome.so" .
chmod 755 "$OUT/libnavidrome.so"
file "$OUT/libnavidrome.so" | cut -c1-160

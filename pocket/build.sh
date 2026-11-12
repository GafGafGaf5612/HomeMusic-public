#!/usr/bin/env bash
# The phone app in one command (macOS or Linux): Android wheels and executables, the PWA, the APK.
#
# Needs: JDK 17+ (JAVA_HOME), Android SDK with platform 36 and NDK r28 (ANDROID_HOME, NDK), Rust with the
# aarch64-linux-android target, Python 3.12 (PYTHON), Node 22+, cmake, ninja, make, curl.
# Optional: WORK (build dir for the native parts; default pocket/build-native), RELEASE=1 for assembleRelease.
#
#   NDK=$ANDROID_HOME/ndk/28.2.13676358 ./build.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
: "${ANDROID_HOME:?set ANDROID_HOME}" "${NDK:?set NDK (Android NDK r28+)}"
WORK=${WORK:-$HERE/build-native}
PYTHON=${PYTHON:-python3.12}
CHAQUOPY_TARGET=${CHAQUOPY_TARGET:-3.12.15-0}  # Chaquopy's Python build: headers and libpython for the wheels
mkdir -p "$WORK"

if [ ! -f "$WORK/pytarget/jniLibs/arm64-v8a/libpython3.12.so" ]; then
  curl -fsSL -o "$WORK/target.zip" \
    "https://repo1.maven.org/maven2/com/chaquo/python/target/$CHAQUOPY_TARGET/target-$CHAQUOPY_TARGET-arm64-v8a.zip"
  mkdir -p "$WORK/pytarget" && unzip -q -o "$WORK/target.zip" -d "$WORK/pytarget" && rm "$WORK/target.zip"
fi

PYTARGET="$WORK/pytarget" OUT="$WORK/wheels" WORK="$WORK/src" "$HERE/native/build-wheels.sh"
OUT="$WORK/jniLibs/arm64-v8a" WORK="$WORK/bin-src" "$HERE/native/build-binaries.sh"
OUT="$WORK/jniLibs/arm64-v8a" WORK="$WORK/navidrome" "$HERE/native/build-navidrome.sh"

( cd "$HERE/../web" && npm ci --no-audit --no-fund && npm run build )

if [ ! -f "$HERE/local.properties" ]; then
  cat > "$HERE/local.properties" <<EOF
sdk.dir=$ANDROID_HOME
pocket.python=$(command -v "$PYTHON")
pocket.wheels=$WORK/wheels
pocket.jniLibs=$WORK/jniLibs
EOF
fi

cd "$HERE"
if [ "${RELEASE:-}" = 1 ]; then ./gradlew assembleRelease; ls -la app/build/outputs/apk/release/
else ./gradlew assembleDebug; ls -la app/build/outputs/apk/debug/; fi

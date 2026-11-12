#!/usr/bin/env bash
# Executables the phone app runs as child processes, packed as lib*.so into jniLibs/arm64-v8a (Android only lets an
# app execute files from its native library directory):
#   libffmpeg.so, libffprobe.so — ffmpeg built from source, audio only (the Hub's probes and yt-dlp's remux, Navidrome)
#   libqjs.so                  — QuickJS-ng: the JS runtime yt-dlp needs for YouTube (deno and node don't run here)
#   (libnavidrome.so — build-navidrome.sh)
# Needs: NDK, WORK (sources/build dir, no spaces needed), OUT (jniLibs/arm64-v8a), cmake, ninja, make, curl.
set -euo pipefail
: "${NDK:?}" "${WORK:?}" "${OUT:?}"
FFMPEG_VERSION=${FFMPEG_VERSION:-9.0.2}
QJS_VERSION=${QJS_VERSION:-v0.18.0}
TC="$NDK/toolchains/llvm/prebuilt/$(uname -s | tr A-Z a-z)-x86_64"
API=24
CC="$TC/bin/aarch64-linux-android$API-clang"
JOBS=$(sysctl -n hw.ncpu 2>/dev/null || nproc)
mkdir -p "$WORK" "$OUT"
cd "$WORK"

# --- ffmpeg -----------------------------------------------------------------------------------------------------
if [ ! -f "$OUT/libffmpeg.so" ] || [ ! -f "$OUT/libffprobe.so" ]; then
  [ -d "ffmpeg-$FFMPEG_VERSION" ] || {
    curl -fsSL -o ffmpeg.tar.xz "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz"
    curl -fsSL -o ffmpeg.tar.xz.asc "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz.asc" || true
    tar xJf ffmpeg.tar.xz && rm ffmpeg.tar.xz
  }
  ( cd "ffmpeg-$FFMPEG_VERSION"
    ./configure \
      --prefix="$WORK/ffmpeg-out" --target-os=android --arch=aarch64 --cpu=armv8-a --enable-cross-compile \
      --cc="$CC" --cxx="$TC/bin/aarch64-linux-android$API-clang++" --ld="$CC" \
      --ar="$TC/bin/llvm-ar" --nm="$TC/bin/llvm-nm" --ranlib="$TC/bin/llvm-ranlib" --strip="$TC/bin/llvm-strip" \
      --extra-ldflags="-Wl,-z,max-page-size=16384" \
      --enable-pic --enable-static --disable-shared --disable-debug --disable-doc --disable-ffplay \
      --disable-autodetect --disable-network --disable-everything --enable-zlib \
      --enable-ffmpeg --enable-ffprobe --enable-swresample --enable-swscale \
      --enable-protocol=file,pipe \
      --enable-demuxer=mp3,aac,flac,ogg,matroska,mov,wav,w64,aiff,ape,wv,tta,dsf,asf,caf,image2,image2pipe,mjpeg,png_pipe,jpeg_pipe \
      --enable-decoder=mp3,mp3float,mp2,aac,aac_latm,alac,flac,opus,vorbis,ape,wavpack,tta,wmav1,wmav2,wmalossless,wmapro,dsd_lsbf,dsd_msbf,dsd_lsbf_planar,dsd_msbf_planar,pcm_s16le,pcm_s16be,pcm_s24le,pcm_s24be,pcm_s32le,pcm_f32le,pcm_u8,mjpeg,png \
      --enable-encoder=aac,flac,opus,vorbis,pcm_s16le,mjpeg,png \
      --enable-muxer=mp4,ipod,mov,ogg,opus,flac,adts,matroska,webm,wav,null,image2,image2pipe,mjpeg \
      --enable-parser=aac,aac_latm,flac,mpegaudio,opus,vorbis,mjpeg,png \
      --enable-bsf=aac_adtstoasc,null \
      --enable-filter=aresample,aformat,anull,silencedetect,volume,atrim,asetnsamples,null,copy,scale,format \
      > "$WORK/ffmpeg-configure.log"
    make -j"$JOBS" ffmpeg ffprobe > "$WORK/ffmpeg-make.log" 2>&1 )
  "$TC/bin/llvm-strip" -o "$OUT/libffmpeg.so" "ffmpeg-$FFMPEG_VERSION/ffmpeg"
  "$TC/bin/llvm-strip" -o "$OUT/libffprobe.so" "ffmpeg-$FFMPEG_VERSION/ffprobe"
fi

# --- QuickJS-ng ---------------------------------------------------------------------------------------------------
if [ ! -f "$OUT/libqjs.so" ]; then
  [ -d "quickjs-$QJS_VERSION" ] || {
    mkdir "quickjs-$QJS_VERSION"
    curl -fsSL "https://github.com/quickjs-ng/quickjs/archive/refs/tags/$QJS_VERSION.tar.gz" | tar xz --strip-components=1 -C "quickjs-$QJS_VERSION"
  }
  cmake -S "quickjs-$QJS_VERSION" -B "quickjs-build" -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" -DANDROID_ABI=arm64-v8a \
    -DANDROID_PLATFORM=android-$API -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_EXE_LINKER_FLAGS="-Wl,-z,max-page-size=16384" > "$WORK/qjs-cmake.log"
  cmake --build "quickjs-build" --target qjs_exe -j"$JOBS" > "$WORK/qjs-build.log"
  "$TC/bin/llvm-strip" -o "$OUT/libqjs.so" quickjs-build/qjs
fi

for f in "$OUT"/lib{ffmpeg,ffprobe,qjs}.so; do
  chmod 755 "$f"; printf '%-16s %6s KB  ' "$(basename "$f")" $(( $(stat -f%z "$f" 2>/dev/null || stat -c%s "$f") / 1024 )); file -b "$f" | cut -c1-70
done

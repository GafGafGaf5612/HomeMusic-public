"""Speed and memory of the real model on library files:
docker compose run --rm embedder python -m app.bench --songs 64 [--device cpu|cuda]"""

import argparse
import asyncio
import json
import random
import resource
import time
from dataclasses import replace

from .audio import decode
from .config import Settings
from .models import MuQMuLan

AUDIO = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav"}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--songs", type=int, default=64)
    ap.add_argument("--device", default=None)
    ap.add_argument("--batch", type=int, default=None)
    a = ap.parse_args()
    s = Settings()
    s = replace(s, device=a.device or s.device, batch=a.batch or s.batch)
    files = [p for p in s.music_root.rglob("*") if p.suffix.lower() in AUDIO]
    random.Random(1).shuffle(files)
    files = files[: a.songs]

    t = time.monotonic()
    wavs = await asyncio.gather(*(decode(f, sr=MuQMuLan.sr, clip_s=s.clip_s) for f in files))
    t_decode = time.monotonic() - t

    t = time.monotonic()
    model = MuQMuLan(s)
    t_load = time.monotonic() - t
    model.embed_audio(wavs[:1])  # warm-up
    import torch

    if model.device == "cuda":
        torch.cuda.reset_peak_memory_stats()
    t = time.monotonic()
    for i in range(0, len(wavs), s.batch):
        model.embed_audio(wavs[i : i + s.batch])
    t_model = time.monotonic() - t
    t = time.monotonic()
    model.embed_text(["energetic russian rock with male vocals"])
    t_text = time.monotonic() - t
    print(json.dumps({
        "device": model.device,
        "gpu": torch.cuda.get_device_name() if model.device == "cuda" else None,
        "songs": len(wavs),
        "clip_s": s.clip_s,
        "batch": s.batch,
        "load_s": round(t_load, 1),
        "decode_songs_per_s": round(len(wavs) / t_decode, 1),
        "model_songs_per_s": round(len(wavs) / t_model, 2),
        "text_s": round(t_text, 3),
        "gpu_peak_mb": round(torch.cuda.max_memory_allocated() / 2**20) if model.device == "cuda" else None,
        "ram_peak_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024),
    }, indent=1))


if __name__ == "__main__":
    asyncio.run(main())

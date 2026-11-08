"""Settings from the environment (docker-compose.yml sets them; all have defaults)."""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "/data")))
    # weights (Hugging Face cache) live in their own volume, not in the image
    models_dir: Path = field(default_factory=lambda: Path(_env("MODELS_DIR", "/models")))
    # library folder, read-only: `path` items are relative to it (as Navidrome's song.path)
    music_root: Path = field(default_factory=lambda: Path(_env("MUSIC_ROOT", "/music")))
    # empty: no auth (the service is reachable only from the compose network)
    token: str = field(default_factory=lambda: _env("EMBEDDER_TOKEN", ""))
    # muq-mulan | fake (tests, a machine without a GPU)
    model: str = field(default_factory=lambda: _env("EMBEDDER_MODEL", "muq-mulan"))
    model_repo: str = field(default_factory=lambda: _env("EMBEDDER_MODEL_REPO", "OpenMuQ/MuQ-MuLan-large"))
    # cuda | cpu | auto: the GPU when it has min_free_gpu_mb free at load time, else the model waits for the card
    # (an error in /healthz, the worker retries every retry_s); CPU only when set — ~6 GB of RAM, 50× slower
    device: str = field(default_factory=lambda: _env("EMBEDDER_DEVICE", "auto"))
    # threads for the model on CPU: half of the PC's 12, the rest stays for the Hub and the owner's jobs
    cpu_threads: int = field(default_factory=lambda: int(_env("EMBEDDER_CPU_THREADS", "6")))
    # the model takes ~4 GB of the card by nvidia-smi with clip_batch 4 (3.4 GB reserved by torch, MIXES.md 4.2)
    min_free_gpu_mb: int = field(default_factory=lambda: int(_env("EMBEDDER_MIN_FREE_GPU_MB", "5000")))
    # fp16 — the audio tower on the GPU under fp16 autocast (vectors as fp32 ones, MIXES.md 4.2); fp32 — off
    precision: str = field(default_factory=lambda: _env("EMBEDDER_PRECISION", "fp16"))
    # songs per GPU batch and 10-second clips per forward pass. Speed hardly depends on clip_batch (14.1 songs/s at 4,
    # 14.3 at 24), video memory does (3.4 vs 7.7 GB) — and under WSL2 video memory is charged to Windows' commit limit
    batch: int = field(default_factory=lambda: int(_env("EMBEDDER_BATCH", "16")))
    clip_batch: int = field(default_factory=lambda: int(_env("EMBEDDER_CLIP_BATCH", "4")))
    # fragment of a song that is embedded: this many seconds from its middle (a preview is taken whole)
    clip_s: float = field(default_factory=lambda: float(_env("EMBEDDER_CLIP_S", "30")))
    downloads: int = field(default_factory=lambda: int(_env("EMBEDDER_DOWNLOADS", "8")))
    max_download_mb: int = field(default_factory=lambda: int(_env("EMBEDDER_MAX_DOWNLOAD_MB", "60")))
    download_timeout_s: float = field(default_factory=lambda: float(_env("EMBEDDER_DOWNLOAD_TIMEOUT_S", "60")))
    # a transient failure (network, 5xx, GPU out of memory) is retried this many times, after retry_s × attempt
    max_attempts: int = field(default_factory=lambda: int(_env("EMBEDDER_MAX_ATTEMPTS", "3")))
    retry_s: float = field(default_factory=lambda: float(_env("EMBEDDER_RETRY_S", "60")))
    # the model leaves the GPU after this long without work (the card is shared); 0 — keep it loaded
    idle_unload_s: float = field(default_factory=lambda: float(_env("EMBEDDER_IDLE_UNLOAD_S", "300")))
    # idle queue poll; a POST /embed wakes the worker at once
    poll_s: float = field(default_factory=lambda: float(_env("EMBEDDER_POLL_S", "5")))
    disable_worker: bool = field(default_factory=lambda: _env("EMBEDDER_DISABLE_WORKER", "") in ("1", "true"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "embedder.sqlite3"

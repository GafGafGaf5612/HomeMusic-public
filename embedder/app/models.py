"""Models: audio and text → L2-normalized vectors in one space. MuQ-MuLan (real) and a fake for tests."""

import contextlib
import hashlib
import json
import logging
import os
import subprocess
from typing import Protocol

import numpy as np

from .config import Settings

log = logging.getLogger("embedder.model")


class Model(Protocol):
    name: str
    dim: int
    sr: int
    device: str

    def embed_audio(self, wavs: list[np.ndarray]) -> np.ndarray: ...

    def embed_text(self, texts: list[str]) -> np.ndarray: ...

    def close(self) -> None: ...


def _l2(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=-1, keepdims=True)
    return (m / np.maximum(n, 1e-12)).astype(np.float32)


class FakeModel:
    """Deterministic and instant: the vector is the log spectrum in bands, so tones of one pitch land close and
    different pitches apart (enough to test similarity end to end). Text — a hash-seeded vector."""

    name = "fake"
    dim = 32
    sr = 8000
    device = "cpu"

    def __init__(self) -> None:
        self.audio_calls: list[int] = []

    def embed_audio(self, wavs: list[np.ndarray]) -> np.ndarray:
        self.audio_calls.append(len(wavs))
        out = []
        for w in wavs:
            spec = np.abs(np.fft.rfft(w[: self.sr]))  # first second
            bands = np.array_split(spec, self.dim)
            out.append(np.log1p([b.sum() for b in bands]))
        return _l2(np.array(out))

    def embed_text(self, texts: list[str]) -> np.ndarray:
        out = []
        for t in texts:
            seed = int.from_bytes(hashlib.sha256(t.encode()).digest()[:4], "little")
            out.append(np.random.default_rng(seed).standard_normal(self.dim))
        return _l2(np.array(out))

    def close(self) -> None:
        pass


def gpu_free_mb() -> int | None:
    """Free memory of GPU 0 as the driver sees it (NVML via nvidia-smi). Under WSL2 torch.cuda.mem_get_info()
    counts only this process and reports a card filled by Windows programs as free; WDDM then spills into shared
    system memory and both jobs crawl."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits", "-i", "0"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
        return int(out.strip().splitlines()[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


class GpuBusy(RuntimeError):
    """The card is taken by another job: the model is not loaded now (the worker retries later)."""


def pick_device(settings: Settings, free_mb=gpu_free_mb) -> str:
    """cuda | cpu as set; auto — the GPU when it has min_free_gpu_mb free, else GpuBusy. No fallback to CPU:
    there the model needs ~6 GB of RAM and is 50× slower (MIXES.md 4.2), and the RAM is shared with Windows."""
    import torch

    if settings.device in ("cpu", "cuda"):
        return settings.device
    if not torch.cuda.is_available():
        raise GpuBusy("no CUDA device in the container (EMBEDDER_DEVICE=cpu to run on the CPU)")
    free = free_mb()
    if free is None:
        free = torch.cuda.mem_get_info()[0] >> 20
    if free < settings.min_free_gpu_mb:
        raise GpuBusy(f"GPU has {free} MB free (< {settings.min_free_gpu_mb} MB): waiting for the card")
    return "cuda"


@contextlib.contextmanager
def _submodels_without_weights():
    """muq builds MuQ-MuLan by first downloading and loading the pretrained audio (MuQ-large, 1.3 GB) and text
    (XLM-RoBERTa, 1.1 GB) models into RAM; MuQ-MuLan's own checkpoint then overwrites all of those weights.
    Here they are built from their configs only, on the current default device."""
    from unittest import mock

    from huggingface_hub import hf_hub_download
    from muq import MuQ
    from muq.muq_mulan.models import text
    from transformers import XLMRobertaConfig, XLMRobertaModel

    def muq_from_config(name, cache_dir=None, **_):
        with open(hf_hub_download(name, "config.json", cache_dir=cache_dir), encoding="utf-8") as f:
            return MuQ(json.load(f))

    class Roberta:
        @staticmethod
        def from_pretrained(name, cache_dir=None, **_):
            return XLMRobertaModel(XLMRobertaConfig.from_pretrained(name, cache_dir=cache_dir))

    with mock.patch.object(MuQ, "from_pretrained", muq_from_config), mock.patch.object(text, "XLMRobertaModel", Roberta):
        yield


def _drop_page_cache(path: str) -> None:
    """The checkpoint is read once; its 2.5 GB in the page cache would only swell the WSL VM (vmmem)."""
    with contextlib.suppress(OSError, AttributeError):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        finally:
            os.close(fd)


def load_muq_mulan(repo: str, cache_dir: str, device: str):
    """MuQ-MuLan straight onto `device`: the modules are created there (random init, fast on the GPU) and the
    checkpoint is memory-mapped and copied tensor by tensor into them — one copy of the weights, and in RAM only
    the file's pages while they are read. muq's own from_pretrained peaked at ~6 GB of RAM plus ~5 GB of page cache."""
    import torch
    from huggingface_hub import hf_hub_download
    from muq import MuQMuLan as _MuQMuLan

    with open(hf_hub_download(repo, "config.json", cache_dir=cache_dir), encoding="utf-8") as f:
        config = json.load(f)
    weights = hf_hub_download(repo, "pytorch_model.bin", cache_dir=cache_dir)
    with _submodels_without_weights(), torch.device(device):
        model = _MuQMuLan(config=config, hf_hub_cache_dir=cache_dir)
    state = torch.load(weights, map_location="cpu", mmap=True, weights_only=True)
    try:
        model.load_state_dict(state, strict=True)
    finally:
        del state
        _drop_page_cache(weights)
    return model.eval()


def _without_autocast(fn):
    """`fn` in fp32 even under autocast. MuQ's input features are the raw (not log) mel power, normalized
    linearly: on loud music they pass fp16's 65504 in the mel projection and the first convolutions, and the
    vector comes out NaN (58 of 64 library songs); after those layers values are small."""
    import torch

    def run(*args, **kwargs):
        with torch.autocast("cuda", enabled=False):
            return fn(*(a.float() if torch.is_tensor(a) and a.is_floating_point() else a for a in args), **kwargs)

    return run


class MuQMuLan:
    """OpenMuQ/MuQ-MuLan-large, 24 kHz. A song is cut into 10-second clips; clips of the whole batch go through
    the audio tower together and a song's vector is the mean of its clips. Weights stay fp32 (as the model's README
    advises); on the GPU the audio tower runs under fp16 autocast (EMBEDDER_PRECISION) — MIXES.md 4.2."""

    dim = 512
    sr = 24000

    def __init__(self, settings: Settings) -> None:
        import torch

        self._torch = torch
        self.device = pick_device(settings)
        if self.device == "cpu":
            torch.set_num_threads(settings.cpu_threads)
        self.name = settings.model_repo
        self.clip_batch = settings.clip_batch
        # autocast dtype of the audio tower, None — plain fp32
        self.amp = {"fp16": torch.float16, "bf16": torch.bfloat16}.get(settings.precision) if self.device == "cuda" else None
        try:
            self.model = load_muq_mulan(settings.model_repo, str(settings.models_dir / "hf"), self.device)
        except BaseException:
            if self.device == "cuda":
                torch.cuda.empty_cache()
            raise
        self.clip_len = int(self.model.sr * self.model.clip_secs)
        if self.amp:
            muq = self.model.mulan_module.audio.model.model
            muq.preprocessing = _without_autocast(muq.preprocessing)
            muq.conv.forward = _without_autocast(muq.conv.forward)

    def _clips(self, wav: np.ndarray) -> np.ndarray:
        """As MuQMuLan._get_all_clips: whole clips; a tail is filled up from the start of the fragment."""
        n, d = len(wav), self.clip_len
        if n < d:
            return np.resize(wav, d)[None, :]
        clips = [wav[i : i + d] for i in range(0, n - d + 1, d)]
        rest = n % d
        if rest and rest >= d // 2:  # a short tail would only dilute the mean
            clips.append(np.concatenate([wav[n - rest :], wav[: d - rest]]))
        return np.stack(clips)

    def embed_audio(self, wavs: list[np.ndarray]) -> np.ndarray:
        torch = self._torch
        per_song = [self._clips(w) for w in wavs]
        clips = np.concatenate(per_song)
        latents = []
        with torch.inference_mode(), torch.autocast("cuda", dtype=self.amp or torch.float16, enabled=self.amp is not None):
            for i in range(0, len(clips), self.clip_batch):
                x = torch.from_numpy(clips[i : i + self.clip_batch]).to(self.device)
                latents.append(self.model.mulan_module.get_audio_latents(x).float().cpu().numpy())
        lat = np.concatenate(latents)
        out, i = [], 0
        for c in per_song:
            out.append(lat[i : i + len(c)].mean(axis=0))
            i += len(c)
        return _l2(np.array(out))

    def embed_text(self, texts: list[str]) -> np.ndarray:
        with self._torch.inference_mode():
            return _l2(self.model(texts=texts).float().cpu().numpy())

    def close(self) -> None:
        del self.model
        if self.device == "cuda":
            self._torch.cuda.empty_cache()


def load_model(settings: Settings) -> Model:
    if settings.model == "fake":
        return FakeModel()
    if settings.model == "muq-mulan":
        return MuQMuLan(settings)
    raise ValueError(f"unknown EMBEDDER_MODEL: {settings.model}")

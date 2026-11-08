"""The real MuQ-MuLan: slow (weights ~3 GB on the first run), only with EMBEDDER_REAL_MODEL_TESTS=1 in the image
(command — README, «Сервис embedder»)."""

import os

import numpy as np
import pytest

pytestmark = [
    pytest.mark.gpu,
    pytest.mark.skipif(os.environ.get("EMBEDDER_REAL_MODEL_TESTS") != "1", reason="set EMBEDDER_REAL_MODEL_TESTS=1"),
]


@pytest.fixture(scope="module")
def model():
    from app.config import Settings
    from app.models import MuQMuLan

    m = MuQMuLan(Settings())
    yield m
    m.close()


def test_vectors_are_normalized_deterministic_and_comparable(model):
    sr = model.sr
    t = np.arange(30 * sr) / sr
    rng = np.random.default_rng(0)
    a = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    noise = (0.3 * rng.standard_normal(len(t))).astype(np.float32)
    v = model.embed_audio([a, noise, a[: 20 * sr]])
    assert v.shape == (3, 512)
    assert np.linalg.norm(v, axis=1) == pytest.approx(1.0, abs=1e-4)
    again = model.embed_audio([a])[0]
    assert float(v[0] @ again) == pytest.approx(1.0, abs=1e-3)
    # the same tone cut shorter is closer to itself than to white noise
    assert float(v[0] @ v[2]) > float(v[0] @ v[1])
    txt = model.embed_text(["white noise", "a pure sine tone"])
    assert txt.shape == (2, 512)


def test_fp16_autocast_matches_fp32_on_loud_audio(model):
    if model.device != "cuda":
        pytest.skip("autocast is for the GPU")
    rng = np.random.default_rng(1)
    # full-scale noise and a clipped square: raw mel power far above fp16's range (NaN before the fp32 front end)
    t = np.arange(20 * model.sr) / model.sr
    loud = [np.clip(rng.standard_normal(len(t)), -1, 1).astype(np.float32),
            np.sign(np.sin(2 * np.pi * 110 * t)).astype(np.float32)]
    amp, model.amp = model.amp, None
    try:
        ref = model.embed_audio(loud)
    finally:
        model.amp = amp
    half = model.embed_audio(loud)
    assert np.isfinite(half).all()
    assert (ref * half).sum(axis=1) == pytest.approx(1.0, abs=1e-4)

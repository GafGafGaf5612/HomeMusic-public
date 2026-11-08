from dataclasses import replace

import pytest

from app import models

torch = pytest.importorskip("torch")


@pytest.mark.parametrize(
    "setting,cuda,free,expected",
    [
        ("auto", True, 12000, "cuda"),
        ("auto", True, 700, models.GpuBusy),  # the card is busy with another job: wait, no CPU fallback
        ("auto", False, None, models.GpuBusy),
        ("cuda", True, 700, "cuda"),  # forced
        ("cpu", True, 12000, "cpu"),
    ],
    ids=["free-gpu", "busy-gpu", "no-gpu", "forced-cuda", "forced-cpu"],
)
def test_device_follows_free_gpu_memory(settings, monkeypatch, setting, cuda, free, expected):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: cuda)
    s = replace(settings, device=setting, min_free_gpu_mb=8000)
    if isinstance(expected, str):
        assert models.pick_device(s, free_mb=lambda: free) == expected
    else:
        with pytest.raises(expected):
            models.pick_device(s, free_mb=lambda: free)

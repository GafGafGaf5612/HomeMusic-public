import numpy as np
import pytest

from app.search import Index, score


def unit(*xs):
    v = np.array(xs, dtype=np.float32)
    return v / np.linalg.norm(v)


A, B, C = unit(1, 0, 0), unit(0, 1, 0), unit(1, 1, 0)


def test_center_is_weighted():
    # weight 3 on A, 1 on B: the center leans to A
    [r] = score({"c": C}, {"a": (A, 3.0), "b": (B, 1.0)}, top=2)
    center = unit(3, 1, 0)
    assert r.pos == pytest.approx(float(C @ center))
    assert [n.key for n in r.nearest] in (["a", "b"], ["b", "a"])
    assert r.neg is None and r.nearest_neg is None and r.score == pytest.approx(r.pos)


def test_negative_keys_form_their_own_center():
    [r] = score({"a": A}, {"c": (C, 1.0), "b": (B, -2.0)}, top=3)
    assert r.pos == pytest.approx(float(A @ C))
    assert r.neg == pytest.approx(0.0, abs=1e-6)
    assert r.nearest_neg.key == "b" and r.score == pytest.approx(r.pos)
    [r] = score({"c": C}, {"a": (A, 1.0), "c2": (C, -1.0)}, top=3)
    assert r.score == pytest.approx(float(C @ A) - 0.5 * 1.0)


@pytest.mark.parametrize("profile", [{}, {"a": (A, 1.0)}], ids=["empty", "only-self"])
def test_candidate_without_other_profile_keys(profile):
    [r] = score({"a": A}, profile, top=3)
    assert r.nearest == []
    assert r.pos == (None if not profile else pytest.approx(1.0))


def test_index_filters_and_orders():
    class Store:
        version = 1

        def all_vectors(self, model):
            return ["nd:a", "nd:b", "dz:c"], np.stack([A, B, C])

    idx = Index()
    idx.refresh(Store(), "m")
    assert [n.key for n in idx.nearest(A, limit=3, prefix=None, keys=None)] == ["nd:a", "dz:c", "nd:b"]
    assert [n.key for n in idx.nearest(A, limit=3, prefix="nd:", keys=None)] == ["nd:a", "nd:b"]
    assert [n.key for n in idx.nearest(A, limit=1, prefix=None, keys={"nd:b", "dz:c"})] == ["dz:c"]

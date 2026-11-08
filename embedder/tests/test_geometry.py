import weakref

import numpy as np
import pytest

from app.db import Store
from app.geometry import Geometry, facets, similarity

from .support import store_vectors


def test_centering_removes_common_component_and_keeps_raw_export(tmp_path):
    store = Store(tmp_path / "vectors.sqlite")
    try:
        store_vectors(store, {"a": [10, 1, 0], "b": [10, -1, 0], "c": [10, 0, 1], "d": [10, 0, -1]})
        space = Geometry().current(store, "fake")
        assert space.vectors["a"] == pytest.approx([0, 1, 0])
        assert space.vectors["c"] == pytest.approx([0, 0, 1])
        assert float(space.vectors["a"] @ space.vectors["c"]) == 0
        assert store.vectors(["a"], "fake")["a"].tolist() == [10, 1, 0]
    finally:
        store.close()


def test_facets_preserve_small_cluster_and_are_deterministic():
    vectors = {f"a:{i:02}": np.array([1., 0]) for i in range(24)}
    vectors.update({f"b:{i:02}": np.array([0., 1]) for i in range(6)})
    weights = dict.fromkeys(vectors, 1.)
    found = facets(vectors, weights)
    assert [f.weight for f in found] == pytest.approx([.8, .2])
    assert len(found[1].members) == 6
    assert facets(dict(reversed(list(vectors.items()))), dict(reversed(list(weights.items())))) == found
    [result] = similarity({"minor": np.array([0., 1])}, [f.center for f in found], {})
    one_center_cos = 6 / np.linalg.norm([24, 6])
    assert result["pos"] == 1 and one_center_cos < .25


def test_tiny_facet_merges_and_heavy_weights_control_center():
    vectors = {f"a:{i:02}": np.array([1., 0]) for i in range(28)}
    vectors.update({f"b:{i}": np.array([0., 1]) for i in range(2)})
    [found] = facets(vectors, dict.fromkeys(vectors, 1.))
    assert len(found.members) == 30 and found.weight == 1
    [weighted] = facets({"a": np.array([1., 0]), "b": np.array([0., 1])}, {"a": 3, "b": 1})
    assert weighted.center == pytest.approx(np.array([3, 1]) / np.sqrt(10))


def test_negative_penalty_is_local_and_empty_positive_is_zero():
    vectors = {"close": np.array([1., 0]), "far": np.array([0., 1]), "opposite": np.array([-1., 0])}
    scored = {r["key"]: r for r in similarity(vectors, [], {"negative": np.array([1., 0])})}
    assert scored["close"]["score"] == -.5
    assert scored["far"]["score"] == 0
    assert scored["opposite"]["score"] == 0


def test_store_and_model_revision_invalidate_and_cache_retains_only_current(tmp_path):
    store = Store(tmp_path / "vectors.sqlite")
    geometry = Geometry()
    try:
        store_vectors(store, {"a": [1, 0], "b": [-1, 0]})
        initial = geometry.current(store, "fake")
        assert geometry.current(store, "fake") is initial
        # Removing a done vector by requeue must invalidate before a replacement finishes.
        store.enqueue([("b", "path", "changed")], force=True, model="fake")
        changed = geometry.current(store, "fake")
        assert changed.space_id != initial.space_id and "b" not in changed.vectors
        assert geometry.current(store, "other-model").space_id != changed.space_id
        previous = []
        for i in range(20):
            store_vectors(store, {f"new:{i}": [i, 1]})
            previous.append(weakref.ref(geometry.current(store, "fake")))
        assert all(ref() is None for ref in previous[:-1])
        geometry.clear()
        assert previous[-1]() is None
    finally:
        store.close()


def test_space_identity_survives_restart_and_distinguishes_stores(tmp_path):
    path = tmp_path / "vectors.sqlite"
    geometry = Geometry()
    store = Store(path)
    store_vectors(store, {"a": [1, 0], "b": [-1, 0]})
    space = geometry.current(store, "fake")
    store.close()
    store = Store(path)
    try:
        assert geometry.current(store, "fake").space_id == space.space_id
    finally:
        store.close()
    other = Store(tmp_path / "other.sqlite")
    try:
        assert geometry.current(other, "fake").space_id != space.space_id
    finally:
        other.close()

"""Versioned, centered audio geometry. No GPU or per-user persistent state."""

import hashlib
import threading
from dataclasses import dataclass

import numpy as np

from .db import Store

EPS = 1e-12
MAX_DIM = 4096


def unit(v: np.ndarray) -> np.ndarray | None:
    n = float(np.linalg.norm(v))
    return v / n if np.isfinite(n) and n > EPS else None


@dataclass(frozen=True)
class Axes:
    """Two principal axes of a set of centered vectors (for the 2D map, MIXES.md 4.2 `/v2/project`)."""
    mean: np.ndarray
    axes: np.ndarray  # (dim, 2)
    explained: tuple[float, float]


@dataclass(frozen=True)
class Space:
    model: str
    space_id: str
    revision: int
    dim: int | None
    vectors: dict[str, np.ndarray]
    zero: frozenset[str]
    invalid: frozenset[str]


class Geometry:
    """Exactly one snapshot is retained; old ids expire instead of mixing coordinate systems."""

    def __init__(self):
        self._lock = threading.Lock()
        self._space: Space | None = None
        self._store: Store | None = None
        self._axes: tuple[str, Axes] | None = None  # the "global" axes of the one retained snapshot

    def clear(self):
        with self._lock:
            self._space = None
            self._store = None
            self._axes = None

    def global_axes(self, space: Space) -> Axes:
        """Axes fitted on every valid vector of the snapshot; cached until the snapshot changes."""
        with self._lock:
            if self._axes and self._axes[0] == space.space_id:
                return self._axes[1]
        fitted = fit_axes(space.vectors)
        with self._lock:
            self._axes = (space.space_id, fitted)
        return fitted

    def current(self, store: Store, model: str) -> Space:
        with self._lock:
            if self._store is store and self._space and (self._space.model, self._space.revision) == (model, store.version):
                return self._space
            revision, rows = store.geometry_snapshot(model)
            # A model has one dimension; reject corrupt minority dimensions deterministically.
            sizes: dict[int, int] = {}
            for _, v in rows:
                if 0 < len(v) <= MAX_DIM and np.isfinite(v).all():
                    sizes[len(v)] = sizes.get(len(v), 0) + 1
            dim = min(sizes, key=lambda d: (-sizes[d], d)) if sizes else None
            good = [(k, v.astype(np.float64)) for k, v in rows
                    if len(v) == dim and np.isfinite(v).all()]
            invalid = frozenset(k for k, _ in rows) - {k for k, _ in good}
            digest = hashlib.sha256(b"homemusic-centered-v2\0" + model.encode())
            # Content identity also survives restarts (the in-memory revision does not).
            for key, v in rows:
                digest.update(len(key.encode()).to_bytes(4, "big"))
                digest.update(key.encode())
                digest.update(len(v).to_bytes(4, "big"))
                digest.update(v.tobytes())
            vectors, zero = {}, set()
            if good:
                mean = np.mean(np.stack([v for _, v in good]), axis=0)
                for key, v in good:
                    residual = unit(v - mean)
                    if residual is None:
                        zero.add(key)
                    else:
                        residual.setflags(write=False)
                        vectors[key] = residual
            self._space = Space(model, digest.hexdigest(), revision, dim, vectors,
                                frozenset(zero), invalid)
            self._store = store
            return self._space


@dataclass
class Facet:
    id: str
    weight: float
    members: list[str]
    center: list[float]


def _fit(matrix: np.ndarray, weights: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray, float]:
    # Input keys are sorted, so argmax resolves every tie by stable key.
    seeds = [int(np.argmax(weights))]
    while len(seeds) < k:
        distance = 1 - np.max(matrix @ matrix[seeds].T, axis=1)
        distance[seeds] = -1
        seeds.append(int(np.argmax(distance)))
    centers = matrix[seeds].copy()
    previous = None
    for _ in range(100):
        labels = np.argmax(matrix @ centers.T, axis=1)
        if previous is not None and np.array_equal(labels, previous):
            break
        previous = labels.copy()
        for j in range(k):
            mask = labels == j
            if mask.any():
                c = unit((matrix[mask] * weights[mask, None]).sum(axis=0))
                centers[j] = c if c is not None else matrix[np.flatnonzero(mask)[np.argmax(weights[mask])]]
    labels = np.argmax(matrix @ centers.T, axis=1)
    gain = float(np.sum(weights * np.sum(matrix * centers[labels], axis=1)) / weights.sum())
    return labels, centers, gain


def facets(vectors: dict[str, np.ndarray], weights: dict[str, float]) -> list[Facet]:
    keys = sorted(k for k, w in weights.items() if w >= 1 and k in vectors)
    if not keys:
        return []
    matrix = np.stack([vectors[k] for k in keys])
    w = np.array([weights[k] for k in keys], dtype=np.float64)
    labels, centers, objective = _fit(matrix, w, 1)
    for k in range(2, min(5, 1 + len(keys) // 15) + 1):
        proposed, cs, value = _fit(matrix, w, k)
        if value - objective < .03:
            break
        labels, centers, objective = proposed, cs, value
    groups = {j: list(np.flatnonzero(labels == j)) for j in range(len(centers)) if (labels == j).any()}
    while len(groups) > 1:
        small = [j for j, ids in groups.items() if w[ids].sum() / w.sum() < .08]
        if not small:
            break
        j = min(small, key=lambda j: (float(w[groups[j]].sum()), keys[groups[j][0]]))
        target = min((t for t in groups if t != j), key=lambda t: (-float(centers[j] @ centers[t]),
                                                                 keys[groups[t][0]]))
        groups[target] = sorted(groups[target] + groups.pop(j))
        ids = groups[target]
        c = unit((matrix[ids] * w[ids, None]).sum(axis=0))
        centers[target] = c if c is not None else matrix[ids[np.argmax(w[ids])]]
    out = []
    for j, ids in groups.items():
        members = [keys[i] for i in ids]
        digest = hashlib.sha256()
        for key in members:
            digest.update(len(key.encode()).to_bytes(4, "big"))
            digest.update(key.encode())
        identity = digest.hexdigest()[:20]
        out.append(Facet(identity, float(w[ids].sum() / w.sum()), members, centers[j].tolist()))
    return sorted(out, key=lambda f: (-f.weight, f.members))


def similarity(vectors: dict[str, np.ndarray], centers: list[list[float]], negatives: dict[str, np.ndarray]):
    positive = [u for c in centers if (u := unit(np.asarray(c, dtype=np.float64))) is not None]
    negative = [negatives[k] for k in sorted(negatives)]
    keys = list(vectors)
    p = np.stack(positive).T if positive else None
    n = np.stack(negative).T if negative else None
    # Bound temporary memory even for 5000 candidates against 5000 negatives.
    for start in range(0, len(keys), 128):
        chunk = keys[start:start + 128]
        matrix = np.stack([vectors[k] for k in chunk])
        pos = np.max(matrix @ p, axis=1) if p is not None else np.zeros(len(chunk))
        neg = np.maximum(0., np.max(matrix @ n, axis=1)) if n is not None else np.zeros(len(chunk))
        for i, key in enumerate(chunk):
            yield {"key": key, "pos": float(pos[i]), "neg": float(neg[i]), "score": float(pos[i] - .5 * neg[i])}


def fit_axes(vectors: dict[str, np.ndarray]) -> Axes:
    """PCA of the two leading axes. Keys are sorted, so the result does not depend on the request order."""
    dim = len(next(iter(vectors.values()))) if vectors else 0
    if not vectors:
        return Axes(np.zeros(dim), np.zeros((dim, 2)), (0.0, 0.0))
    matrix = np.stack([vectors[k] for k in sorted(vectors)])
    mean = matrix.mean(axis=0)
    centered = matrix - mean
    values, directions = np.linalg.eigh(centered.T @ centered)
    total = float(values.clip(min=0).sum())
    axes = np.zeros((dim, 2))
    explained = [0.0, 0.0]
    for slot, j in enumerate((dim - 1, dim - 2)):
        # a degenerate axis (no variance left) stays zero instead of an arbitrary direction
        if j < 0 or values[j] <= EPS * max(total, 1.0):
            continue
        axis = directions[:, j].copy()
        # deterministic sign: the largest coordinate is positive (ties: the lower index)
        if axis[int(np.argmax(np.abs(axis)))] < 0:
            axis = -axis
        axes[:, slot] = axis
        explained[slot] = float(values[j] / total)
    return Axes(mean, axes, (explained[0], explained[1]))


def project(axes: Axes, points: dict[str, np.ndarray]) -> dict[str, list[float]]:
    return {k: [float(x) for x in (v - axes.mean) @ axes.axes] for k, v in points.items()}


def facet_cosines(vectors: dict[str, np.ndarray], centers: list[list[float]]) -> dict[str, list[float]]:
    """Cosine of each (unit, centered) vector to every facet center, in the order of `centers`."""
    units = [unit(np.asarray(c, dtype=np.float64)) for c in centers]
    if not vectors or not centers:
        return {k: [] for k in vectors}
    keys = list(vectors)
    matrix = np.stack([vectors[k] for k in keys])
    cos = matrix @ np.stack([u if u is not None else np.zeros(matrix.shape[1]) for u in units]).T
    return {k: [float(x) for x in cos[i]] for i, k in enumerate(keys)}

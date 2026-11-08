"""Vector math for the API: candidates against a weighted taste profile, text → nearest keys."""

from dataclasses import dataclass

import numpy as np

# MIXES.md, section 5: sim = cos(to the positive center) − 0.5·cos(to the negative center)
NEG_WEIGHT = 0.5


@dataclass
class Near:
    key: str
    sim: float


@dataclass
class Scored:
    key: str
    pos: float | None
    neg: float | None
    score: float
    nearest: list[Near]
    nearest_neg: Near | None


def _center(vecs: np.ndarray, weights: np.ndarray) -> np.ndarray | None:
    if not len(vecs):
        return None
    c = (vecs * weights[:, None]).sum(axis=0)
    n = np.linalg.norm(c)
    return c / n if n > 1e-12 else None


def score(
    candidates: dict[str, np.ndarray], profile: dict[str, tuple[np.ndarray, float]], *, top: int
) -> list[Scored]:
    """Each candidate: cosine to the weighted center of the positive profile keys (`pos`) and of the negative ones
    (`neg`, weights taken by absolute value), `score = pos − 0.5·neg`, the `top` nearest positive keys and the
    nearest negative one. A candidate that is itself in the profile is not its own neighbour."""
    pkeys = [k for k, (_, w) in profile.items() if w > 0]
    nkeys = [k for k, (_, w) in profile.items() if w < 0]
    P = np.stack([profile[k][0] for k in pkeys]) if pkeys else np.zeros((0, 1), np.float32)
    N = np.stack([profile[k][0] for k in nkeys]) if nkeys else np.zeros((0, 1), np.float32)
    cp = _center(P, np.array([profile[k][1] for k in pkeys])) if pkeys else None
    cn = _center(N, np.array([-profile[k][1] for k in nkeys])) if nkeys else None
    out = []
    for key, v in candidates.items():
        pos = float(v @ cp) if cp is not None else None
        neg = float(v @ cn) if cn is not None else None
        nearest: list[Near] = []
        if pkeys:
            sims = P @ v
            for i in np.argsort(-sims):
                if pkeys[i] == key:
                    continue
                nearest.append(Near(pkeys[i], float(sims[i])))
                if len(nearest) >= top:
                    break
        nearest_neg = None
        if nkeys:
            sims = N @ v
            order = [i for i in np.argsort(-sims) if nkeys[i] != key]
            if order:
                nearest_neg = Near(nkeys[order[0]], float(sims[order[0]]))
        out.append(Scored(key, pos, neg, (pos or 0.0) - NEG_WEIGHT * (neg or 0.0), nearest, nearest_neg))
    return out


class Index:
    """All vectors of the model in memory for /text; rebuilt when the store has new vectors."""

    def __init__(self) -> None:
        self.version = -1
        self.keys: list[str] = []
        self.matrix: np.ndarray | None = None

    def refresh(self, store, model: str) -> None:
        if store.version != self.version:
            version = store.version
            self.keys, self.matrix = store.all_vectors(model)
            self.version = version

    def nearest(self, q: np.ndarray, *, limit: int, prefix: str | None, keys: set[str] | None) -> list[Near]:
        if self.matrix is None:
            return []
        sims = self.matrix @ q
        out = []
        for i in np.argsort(-sims):
            k = self.keys[i]
            if (prefix and not k.startswith(prefix)) or (keys is not None and k not in keys):
                continue
            out.append(Near(k, float(sims[i])))
            if len(out) >= limit:
                break
        return out

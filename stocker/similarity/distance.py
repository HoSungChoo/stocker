"""거리 함수와 백분위 변환. 모두 numpy 순수 함수.

용어:
- DTW(Dynamic Time Warping): 두 수열을 시간축에서 늘리고 줄여 맞춰 본 뒤의 최소 누적 차이.
  같은 모양이 조금 빨리/늦게 진행돼도 가깝게 본다. 여기서는 점별 |a_i - b_j|의 누적합.
- Sakoe-Chiba 밴드: DTW가 시간축을 어긋나게 맞출 수 있는 최대 폭. 창 길이의 `band_frac`.
- 코사인 거리: 1 - 코사인 유사도. 두 벡터의 방향 차이만 본다(크기 무시).
- 백분위 점수: 후보 전체에서 거리 순위를 0~1로 바꾼 것. 1이 가장 가깝다.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.stats import rankdata


def _as_2d(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype="float64")
    return m[None, :] if m.ndim == 1 else m


def dtw_distance_batch(
    query: np.ndarray, candidates: np.ndarray, band_frac: float = 0.1
) -> np.ndarray:
    """query (n,) 와 candidates (K, m) 각 행 사이의 DTW 거리 (K,).

    비용은 |a_i - b_j|. 밴드 폭 w = max(ceil(band_frac * max(n, m)), |n - m|).
    메모리는 두 행(prev, cur)만 유지하므로 K가 수백이어도 가볍다.
    """
    a = np.asarray(query, dtype="float64")
    B = _as_2d(candidates)
    n, (K, m) = len(a), B.shape
    if n == 0 or m == 0:
        raise ValueError("빈 수열에는 DTW를 정의하지 않는다")
    w = max(int(math.ceil(band_frac * max(n, m))), abs(n - m))
    inf = np.inf
    prev = np.full((K, m + 1), inf)
    prev[:, 0] = 0.0
    for i in range(1, n + 1):
        cur = np.full((K, m + 1), inf)
        j_lo = max(1, i - w)
        j_hi = min(m, i + w)
        ai = a[i - 1]
        for j in range(j_lo, j_hi + 1):
            cost = np.abs(ai - B[:, j - 1])
            best = np.minimum(np.minimum(prev[:, j], prev[:, j - 1]), cur[:, j - 1])
            cur[:, j] = cost + best
        prev = cur
    return prev[:, m]


def dtw_distance(a: np.ndarray, b: np.ndarray, band_frac: float = 0.1) -> float:
    """단일 쌍 DTW 거리."""
    return float(dtw_distance_batch(np.asarray(a), np.asarray(b)[None, :], band_frac)[0])


def euclidean_batch(query: np.ndarray, candidates: np.ndarray) -> np.ndarray:
    """||q - c||_2 (K,). NaN 차원은 쌍마다 제외하고 남은 차원 수로 보정한다."""
    q = np.asarray(query, dtype="float64")
    C = _as_2d(candidates)
    diff = C - q
    valid = ~np.isnan(diff)
    n_valid = valid.sum(axis=1)
    sq = np.where(valid, diff**2, 0.0).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.sqrt(sq * (C.shape[1] / n_valid))
    return np.where(n_valid > 0, out, np.nan)


def cosine_distance_batch(query: np.ndarray, candidates: np.ndarray) -> np.ndarray:
    """1 - cos(q, c) (K,). 어느 한쪽이 NaN인 차원은 쌍마다 제외한다. 유효 차원이 없으면 NaN."""
    q = np.asarray(query, dtype="float64")
    C = _as_2d(candidates)
    valid = ~np.isnan(C) & ~np.isnan(q)
    qz = np.where(valid, q, 0.0)
    cz = np.where(valid, C, 0.0)
    dot = (qz * cz).sum(axis=1)
    nq = np.sqrt((qz**2).sum(axis=1))
    nc = np.sqrt((cz**2).sum(axis=1))
    with np.errstate(invalid="ignore", divide="ignore"):
        cos = dot / (nq * nc)
    ok = (valid.sum(axis=1) > 0) & (nq > 0) & (nc > 0)
    return np.where(ok, 1.0 - cos, np.nan)


def standardized_euclidean_batch(
    query: np.ndarray, candidates: np.ndarray, scale: np.ndarray
) -> np.ndarray:
    """sqrt(mean(((q - c) / scale)^2)) (K,). NaN 차원은 쌍마다 제외. scale이 0/NaN인 차원도 제외."""
    q = np.asarray(query, dtype="float64")
    C = _as_2d(candidates)
    sc = np.asarray(scale, dtype="float64")
    sc_ok = np.isfinite(sc) & (sc > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        diff = (C - q) / np.where(sc_ok, sc, 1.0)
    valid = ~np.isnan(diff) & sc_ok
    n_valid = valid.sum(axis=1)
    sq = np.where(valid, diff**2, 0.0).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.sqrt(sq / n_valid)
    return np.where(n_valid > 0, out, np.nan)


def rank_percentile(dist: np.ndarray) -> np.ndarray:
    """거리 → 0~1 백분위 (0이 가장 가까움, 1이 가장 멂). 동점은 평균 순위. NaN은 NaN."""
    d = np.asarray(dist, dtype="float64")
    out = np.full(d.shape, np.nan)
    ok = ~np.isnan(d)
    k = int(ok.sum())
    if k == 0:
        return out
    if k == 1:
        out[ok] = 0.0
        return out
    ranks = rankdata(d[ok], method="average")
    out[ok] = (ranks - 1.0) / (k - 1.0)
    return out


def to_score(dist: np.ndarray) -> np.ndarray:
    """거리 → 0~1 점수 (1이 가장 닮음)."""
    return 1.0 - rank_percentile(dist)

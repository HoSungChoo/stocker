"""공통 쿼리 파라미터 → SimilarityConfig, 값 변환 유틸."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Annotated

import pandas as pd
from fastapi import Depends, HTTPException, Query

from stocker.similarity.config import DEFAULT_WEIGHTS, SimilarityConfig

WINDOWS = (60, 120, 250)


@dataclass(frozen=True)
class CommonQuery:
    """계약 17절의 공통 쿼리. as_of=None이면 DB 마지막 거래일."""

    as_of: str | None
    window: int
    top_k: int
    w_price: float
    w_macro: float
    w_cycle: float
    cycle_lag: int

    def config(self) -> SimilarityConfig:
        total = self.w_price + self.w_macro + self.w_cycle
        if total <= 0:
            raise HTTPException(400, "가중치 합은 0보다 커야 한다")
        weights = {
            "price_shape": self.w_price / total,
            "macro": self.w_macro / total,
            "semi_cycle": self.w_cycle / total,
        }
        try:
            return SimilarityConfig(
                window=self.window,
                top_k=self.top_k,
                weights=weights,
                cycle_lag_days=self.cycle_lag,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    def key(self) -> dict:
        """캐시 키용 dict (as_of가 None이면 'latest')."""
        return {
            "as_of": self.as_of or "latest",
            "window": self.window,
            "top_k": self.top_k,
            "w": (round(self.w_price, 6), round(self.w_macro, 6), round(self.w_cycle, 6)),
            "cycle_lag": self.cycle_lag,
        }


def common_query(
    as_of: Annotated[
        str | None, Query(description="기준일 YYYY-MM-DD (기본 DB 마지막 거래일)")
    ] = None,
    window: Annotated[int, Query(description="창 길이(거래일) 60|120|250")] = 120,
    top_k: Annotated[int, Query(ge=1, le=20)] = 5,
    w_price: Annotated[float, Query(ge=0)] = DEFAULT_WEIGHTS["price_shape"],
    w_macro: Annotated[float, Query(ge=0)] = DEFAULT_WEIGHTS["macro"],
    w_cycle: Annotated[float, Query(ge=0)] = DEFAULT_WEIGHTS["semi_cycle"],
    cycle_lag: Annotated[int, Query(ge=0, le=365)] = 45,
) -> CommonQuery:
    if window not in WINDOWS:
        raise HTTPException(400, f"window는 {'|'.join(map(str, WINDOWS))} 중 하나")
    if as_of is not None:
        try:
            as_of = pd.Timestamp(as_of).strftime("%Y-%m-%d")
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, f"as_of 날짜 형식 오류: {as_of}") from exc
    return CommonQuery(as_of, window, top_k, w_price, w_macro, w_cycle, cycle_lag)


Common = Annotated[CommonQuery, Depends(common_query)]


def iso(ts) -> str | None:
    if ts is None:
        return None
    try:
        if pd.isna(ts):
            return None
    except (TypeError, ValueError):
        pass
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def num(x) -> float | None:
    """NaN/None → None, 그 밖은 float."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v

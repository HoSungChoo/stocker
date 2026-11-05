"""설명 엔진: 점수의 근거(그때 값 / 지금 값)를 돌려준다. 계약: docs/PHASE5_CONTRACT.md 17절.

score()는 레이어별 점수만 내고 그 점수를 만든 값들을 버린다. explain()은 **같은 features.py
함수**로 같은 입력을 다시 계산해, 과거 국면의 끝 날짜(match_end)와 기준일(as_of) 시점의 값을
나란히 돌려준다. 따라서 여기서 나온 값은 score()가 거리 계산에 쓴 값과 정확히 같다.

룩어헤드 금지는 engine.score와 같은 방식이다:
- 입력은 모두 as_of(이하 마지막 거래일) 이하로 자른다.
- z-score는 expanding(각 날짜까지의 평균·표준편차)이므로 "그때(then)"의 z-score는 그때까지
  알 수 있었던 값만으로 계산된다. as_of 이후 데이터를 잘라도 then/now가 바뀌지 않는다.
- forward(이후 수익률)만 표시용으로 as_of 이후 데이터를 쓴다.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import pandas as pd

from stocker.sim.leveraged import forward_returns
from stocker.similarity.comment import build_comment
from stocker.similarity.config import SimilarityConfig
from stocker.similarity.engine import _clean_close, _truncate, score
from stocker.similarity.features import (
    DD_DAYS_COL,
    DRAWDOWN_COL,
    MU_GM_METRIC,
    build_cycle_features,
    build_macro_features,
    build_price_features,
)

# FRED 시리즈 → (한국어 이름, 단위). 계약 17절의 표기 그대로.
MACRO_LABELS: dict[str, tuple[str, str]] = {
    "DFF": ("기준금리", "%"),
    "DGS10": ("10년물", "%"),
    "T10Y2Y": ("장단기 스프레드", "%p"),
    "DCOILWTICO": ("WTI 유가", "$"),
    "DTWEXBGS": ("달러지수", ""),
    "VIXCLS": ("VIX", ""),
}
# features.py 컬럼 → (계약의 metric 이름, 한국어 이름, 소수로 바꾸는 배율)
CYCLE_METRICS: dict[str, tuple[str, str, float]] = {
    "wsts_yoy": ("wsts_sales_yoy", "반도체 매출 YoY", 0.01),
    "wsts_yoy_chg6": ("wsts_yoy_chg6m", "YoY 6개월 변화", 0.01),
    MU_GM_METRIC: ("mu_gross_margin", "마이크론 매출총이익률", 1.0),
}


@dataclass
class ThenNow:
    then: float | None
    now: float | None


@dataclass
class MatchSummary:
    start: pd.Timestamp
    end: pd.Timestamp
    combined: float | None
    scores: dict[str, float | None]


@dataclass
class PriceExplanation:
    dtw_rank: int | None
    euclid_rank: int | None
    drawdown: ThenNow
    drawdown_days: ThenNow


@dataclass
class MacroExplanation:
    series: str
    label: str
    unit: str
    then: float | None = None
    now: float | None = None
    z_then: float | None = None
    z_now: float | None = None


@dataclass
class CycleExplanation:
    metric: str
    label: str
    then: float | None = None
    now: float | None = None


@dataclass
class Explanation:
    as_of: pd.Timestamp
    match: MatchSummary
    price: PriceExplanation
    macro: list[MacroExplanation]
    cycle: list[CycleExplanation]
    forward: dict[int, float | None]
    layers_missing: list[str] = field(default_factory=list)
    # 평가표 코멘트(26절). explain()은 적합 판정(fit)을 모르므로 fit 없는 문장을 넣고,
    # API가 fit이 있으면 build_comment(exp, fit)로 다시 만든다.
    comment: str = ""

    def to_dict(self) -> dict:
        """JSON 가능한 dict (날짜는 YYYY-MM-DD, horizon 키는 문자열)."""
        d = asdict(self)
        d["as_of"] = self.as_of.strftime("%Y-%m-%d")
        d["match"]["start"] = self.match.start.strftime("%Y-%m-%d")
        d["match"]["end"] = self.match.end.strftime("%Y-%m-%d")
        d["forward"] = {str(h): v for h, v in self.forward.items()}
        return d


def _opt(x) -> float | None:
    """NaN/None → None, 그 밖은 float."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _opt_int(x) -> int | None:
    v = _opt(x)
    return None if v is None else int(v)


def _align(feats: pd.DataFrame, trading_index: pd.DatetimeIndex) -> pd.DataFrame:
    """engine._layer_scores_from_features와 같은 정렬: 격자 특징을 거래일로 ffill."""
    return feats.reindex(feats.index.union(trading_index)).ffill().reindex(trading_index)


def _asof_value(s: pd.Series, date: pd.Timestamp) -> float | None:
    """date 이하의 마지막 관측값 (영업일 격자 ffill과 같은 값). 없으면 None."""
    part = s.loc[:date]
    return None if part.empty else _opt(part.iloc[-1])


def explain(
    as_of,
    match_end,
    close: pd.Series,
    macro: dict[str, pd.Series] | None,
    cycle: dict[str, pd.Series] | None,
    config: SimilarityConfig | None = None,
) -> Explanation:
    """as_of 국면과 과거 국면(match_end로 끝나는 창)의 근거 값을 나란히 돌려준다 (순수 함수).

    match_end는 거래일이 아니면 그 이하의 마지막 거래일로 맞춘다. 그 날짜가 엔진의 후보
    (as_of보다 min_gap_days 이상 앞선 창 끝)가 아니면 ValueError.
    """
    cfg = config or SimilarityConfig()
    result = score(as_of, close, macro, cycle, cfg)
    anchor = result.as_of
    full_close = _clean_close(close)
    hist = full_close.loc[:anchor]

    cands = result.candidates
    if cands is None:
        raise RuntimeError("score()가 후보 점수표를 돌려주지 않았다")
    end_req = pd.Timestamp(match_end).normalize()
    before = hist.loc[:end_req]
    if before.empty:
        raise ValueError(f"match_end={end_req.date()} 이전에 시세가 없다")
    end = before.index[-1]
    if end not in cands.index:
        raise ValueError(
            f"match_end={end.date()}는 후보 창의 끝 날짜가 아니다 "
            f"(as_of보다 min_gap_days={cfg.min_gap_days} 거래일 이상 앞선 날짜여야 한다)"
        )
    row = cands.loc[end]
    end_pos = hist.index.get_loc(end)
    start = hist.index[end_pos - cfg.window + 1]

    # --- price: engine과 같은 build_price_features(hist, window) ---
    feats = build_price_features(hist, cfg.window)
    price = PriceExplanation(
        dtw_rank=_opt_int(row["dtw_rank"]),
        euclid_rank=_opt_int(row["euclid_rank"]),
        drawdown=ThenNow(
            then=_opt(feats.at[end, DRAWDOWN_COL]), now=_opt(feats.at[anchor, DRAWDOWN_COL])
        ),
        drawdown_days=ThenNow(
            then=_opt_int(feats.at[end, DD_DAYS_COL]), now=_opt_int(feats.at[anchor, DD_DAYS_COL])
        ),
    )

    # --- macro: engine과 같은 build_macro_features(as_of 이하, change_days, min_periods) ---
    macro_in = _truncate(macro, anchor)
    macro_out: list[MacroExplanation] = []
    mz = None
    if macro_in:
        mfeats = build_macro_features(macro_in, cfg.change_days, cfg.zscore_min_periods)
        if not mfeats.empty:
            mz = _align(mfeats, hist.index)
    for sid in cfg.macro_series:
        label, unit = MACRO_LABELS.get(sid, (sid, ""))
        item = MacroExplanation(series=sid, label=label, unit=unit)
        if sid in macro_in:
            raw = macro_in[sid].astype("float64")
            item.then = _asof_value(raw, end)
            item.now = _asof_value(raw, anchor)
        col = f"{sid}_level_z"
        if mz is not None and col in mz:
            item.z_then = _opt(mz.at[end, col])
            item.z_now = _opt(mz.at[anchor, col])
        macro_out.append(item)

    # --- cycle: engine과 같은 build_cycle_features + 발표 지연(cycle_lag_days) ---
    cycle_in = _truncate(cycle, anchor)
    cycle_out: list[CycleExplanation] = []
    cz = None
    if cycle_in:
        cfeats = build_cycle_features(cycle_in)
        if not cfeats.empty and cfg.cycle_lag_days > 0:
            cfeats = cfeats.copy()
            cfeats.index = cfeats.index + pd.Timedelta(days=cfg.cycle_lag_days)
            cfeats = cfeats.loc[:anchor]
        if not cfeats.empty:
            cz = _align(cfeats, hist.index)
    for col, (metric, label, scale) in CYCLE_METRICS.items():
        if col == MU_GM_METRIC and (cz is None or col not in cz):
            continue  # 선택 지표: 데이터가 없으면 항목 자체를 뺀다
        item = CycleExplanation(metric=metric, label=label)
        if cz is not None and col in cz:
            t, n = _opt(cz.at[end, col]), _opt(cz.at[anchor, col])
            item.then = None if t is None else t * scale
            item.now = None if n is None else n * scale
        cycle_out.append(item)

    # --- forward (표시용; as_of 이후 데이터를 쓰는 유일한 값) ---
    fwd = forward_returns(full_close, cfg.horizons).loc[end]
    forward = {int(h): _opt(fwd[f"fwd_{h}"]) for h in cfg.horizons}

    scores = {k: _opt(row[k]) for k in ("price_shape", "macro", "semi_cycle") if k in cands}
    exp = Explanation(
        as_of=anchor,
        match=MatchSummary(start=start, end=end, combined=_opt(row["combined"]), scores=scores),
        price=price,
        macro=macro_out,
        cycle=cycle_out,
        forward=forward,
        layers_missing=list(result.layers_missing),
    )
    exp.comment = build_comment(exp)
    return exp


def explain_db(
    as_of, match_end, config: SimilarityConfig | None = None, engine=None
) -> Explanation:
    """DB에서 입력을 읽어(loaders.load_inputs) explain을 돌린다."""
    from stocker.similarity.loaders import load_inputs

    cfg = config or SimilarityConfig()
    close, macro, cycle = load_inputs(cfg, engine)
    return explain(as_of, match_end, close, macro, cycle, cfg)


__all__ = [
    "CycleExplanation",
    "Explanation",
    "MacroExplanation",
    "MatchSummary",
    "PriceExplanation",
    "ThenNow",
    "explain",
    "explain_db",
]

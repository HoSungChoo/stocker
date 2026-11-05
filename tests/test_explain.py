"""stocker.similarity.explain 테스트 (계약 PHASE5 17절 필수 3개 + 보조). 기본 실행은 합성 데이터."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stocker.similarity import SimilarityConfig, score
from stocker.similarity.explain import explain
from stocker.similarity.features import (
    DD_DAYS_COL,
    DRAWDOWN_COL,
    build_cycle_features,
    build_macro_features,
    build_price_features,
)

WINDOW = 120


def _close(seed: int = 5, n: int = 3000) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("1994-01-03", periods=n)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0002, 0.02, n))), index=idx, name="^SOX")


def _macro(index: pd.DatetimeIndex, seed: int = 9, late_from: int | None = None) -> dict:
    """DFF·VIXCLS는 전 구간, DTWEXBGS는 late_from 위치부터만 (2006년 이후 달러지수 흉내)."""
    rng = np.random.default_rng(seed)
    out = {
        "DFF": pd.Series(np.cumsum(rng.normal(0, 0.02, len(index))) + 3, index=index),
        "VIXCLS": pd.Series(20 + np.cumsum(rng.normal(0, 0.3, len(index))), index=index),
    }
    if late_from is not None:
        late = index[late_from:]
        out["DTWEXBGS"] = pd.Series(100 + np.cumsum(rng.normal(0, 0.2, len(late))), index=late)
    return out


def _cycle(start: str = "1994-01-31", months: int = 150, seed: int = 2) -> dict:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=months, freq="ME")
    sales = 10_000 * np.exp(np.cumsum(rng.normal(0.004, 0.04, months)))
    return {"wsts_sales_world_usd_m": pd.Series(sales, index=idx)}


@pytest.fixture(scope="module")
def inputs():
    close = _close()
    macro = _macro(close.index, late_from=2000)  # 달러지수는 2000번째 거래일부터
    cycle = _cycle()
    cfg = SimilarityConfig(window=WINDOW, top_k=5)
    as_of = close.index[2400]
    res = score(as_of, close, macro, cycle, cfg)
    return close, macro, cycle, cfg, as_of, res


def _align(feats, idx):
    return feats.reindex(feats.index.union(idx)).ffill().reindex(idx)


def _same(got, want) -> bool:
    """features의 NaN은 explain에서 None이 된다."""
    if want is None or (isinstance(want, float) and np.isnan(want)):
        return got is None
    return got == pytest.approx(want)


# ------------------------------------------------------------------ (1) features와 동일
def test_explain_matches_features_and_score(inputs):
    close, macro, cycle, cfg, as_of, res = inputs
    m = res.matches[0]
    exp = explain(as_of, m.end_date, close, macro, cycle, cfg)

    assert exp.as_of == res.as_of
    assert exp.match.end == m.end_date and exp.match.start == m.start_date
    assert exp.match.combined == pytest.approx(m.combined, abs=1e-12)
    for layer, v in m.scores.items():  # 레이어 점수가 NaN(그 시점 데이터 없음)이면 None
        if np.isnan(v):
            assert exp.match.scores[layer] is None
        else:
            assert exp.match.scores[layer] == pytest.approx(v, abs=1e-12)
    assert exp.layers_missing == []

    hist = close.loc[:as_of]
    feats = build_price_features(hist, WINDOW)
    assert exp.price.drawdown.then == pytest.approx(feats.at[m.end_date, DRAWDOWN_COL])
    assert exp.price.drawdown.now == pytest.approx(feats.at[as_of, DRAWDOWN_COL])
    assert exp.price.drawdown_days.then == int(feats.at[m.end_date, DD_DAYS_COL])
    assert exp.price.drawdown_days.now == int(feats.at[as_of, DD_DAYS_COL])
    assert isinstance(exp.price.euclid_rank, int) and 1 <= exp.price.euclid_rank <= res.n_candidates
    assert exp.price.dtw_rank is None or 1 <= exp.price.dtw_rank <= cfg.prefilter

    cut_macro = {k: v.loc[:as_of] for k, v in macro.items()}
    mz = _align(
        build_macro_features(cut_macro, cfg.change_days, cfg.zscore_min_periods), hist.index
    )
    by_series = {x.series: x for x in exp.macro}
    assert list(by_series) == list(cfg.macro_series)
    for sid in ("DFF", "VIXCLS"):
        x = by_series[sid]
        assert _same(x.z_then, mz.at[m.end_date, f"{sid}_level_z"])
        assert _same(x.z_now, mz.at[as_of, f"{sid}_level_z"])
        assert x.z_now is not None
        assert x.then == pytest.approx(macro[sid].loc[: m.end_date].iloc[-1])
        assert x.now == pytest.approx(macro[sid].loc[:as_of].iloc[-1])
    assert by_series["DFF"].label == "기준금리" and by_series["DFF"].unit == "%"

    cz = build_cycle_features({k: v.loc[:as_of] for k, v in cycle.items()})
    cz.index = cz.index + pd.Timedelta(days=cfg.cycle_lag_days)
    cz = _align(cz.loc[:as_of], hist.index)
    by_metric = {x.metric: x for x in exp.cycle}
    assert set(by_metric) == {"wsts_sales_yoy", "wsts_yoy_chg6m"}
    assert _same(by_metric["wsts_sales_yoy"].then, cz.at[m.end_date, "wsts_yoy"] / 100)
    assert _same(by_metric["wsts_sales_yoy"].now, cz.at[as_of, "wsts_yoy"] / 100)
    assert _same(by_metric["wsts_yoy_chg6m"].then, cz.at[m.end_date, "wsts_yoy_chg6"] / 100)
    assert _same(by_metric["wsts_yoy_chg6m"].now, cz.at[as_of, "wsts_yoy_chg6"] / 100)
    assert by_metric["wsts_sales_yoy"].now is not None

    for h, v in m.forward.items():
        assert exp.forward[h] == pytest.approx(v, nan_ok=True)


# ------------------------------------------------------------------ (2) 룩어헤드
def test_explain_no_lookahead_truncated_inputs_identical(inputs):
    close, macro, cycle, cfg, as_of, res = inputs
    end = res.matches[0].end_date
    full = explain(as_of, end, close, macro, cycle, cfg).to_dict()
    cut = explain(
        as_of,
        end,
        close.loc[:as_of],
        {k: v.loc[:as_of] for k, v in macro.items()},
        {k: v.loc[:as_of] for k, v in cycle.items()},
        cfg,
    ).to_dict()
    # forward는 표시용으로 as_of 이후 데이터를 쓰므로 비교에서 뺀다
    full.pop("forward")
    cut.pop("forward")
    assert full == cut


# ------------------------------------------------------------------ (3) 없는 값은 then=null
def test_series_starting_after_match_end_has_null_then(inputs):
    close, macro, cycle, cfg, as_of, res = inputs
    # 달러지수는 2000번째 거래일부터 있고, 후보는 as_of(2400)보다 250일 이상 앞이므로
    # 1800번째 거래일 이전 후보를 고르면 then이 없다
    end = close.index[1500]
    exp = explain(as_of, end, close, macro, cycle, cfg)
    usd = next(x for x in exp.macro if x.series == "DTWEXBGS")
    assert usd.then is None and usd.z_then is None
    assert usd.now is not None and usd.z_now is not None
    d = exp.to_dict()
    assert d["macro"][4]["then"] is None  # JSON에서는 null


# ------------------------------------------------------------------ 보조
def test_non_candidate_end_raises(inputs):
    close, macro, cycle, cfg, as_of, _ = inputs
    with pytest.raises(ValueError, match="match_end"):
        explain(as_of, as_of, close, macro, cycle, cfg)  # as_of 자신은 min_gap 위반


def test_end_snaps_to_previous_trading_day(inputs):
    close, macro, cycle, cfg, as_of, res = inputs
    end = res.matches[0].end_date
    exp = explain(as_of, end + pd.Timedelta(days=1), close, macro, cycle, cfg)
    assert exp.match.end == end or (end + pd.Timedelta(days=1)) in close.index


def test_to_dict_is_json_shaped(inputs):
    close, macro, cycle, cfg, as_of, res = inputs
    d = explain(as_of, res.matches[0].end_date, close, macro, cycle, cfg).to_dict()
    assert set(d) >= {"as_of", "match", "price", "macro", "cycle", "forward", "layers_missing"}
    assert set(d["forward"]) == {"21", "63", "126", "252"}
    assert d["match"]["end"] == res.matches[0].end_date.strftime("%Y-%m-%d")
    assert d["price"]["drawdown"]["then"] <= 0


def test_explain_without_macro_layer():
    close = _close(7)
    cfg = SimilarityConfig(window=WINDOW, top_k=3)
    as_of = close.index[2400]
    res = score(as_of, close, None, _cycle(), cfg)
    exp = explain(as_of, res.matches[0].end_date, close, None, _cycle(), cfg)
    assert exp.layers_missing == ["macro"]
    assert all(x.then is None and x.z_now is None for x in exp.macro)
    assert "macro" not in exp.match.scores

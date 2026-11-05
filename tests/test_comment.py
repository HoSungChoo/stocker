"""평가표 코멘트(계약 PHASE6 26절) 테스트. 합성 Explanation만 쓰므로 DB·네트워크가 필요 없다."""

from __future__ import annotations

import pandas as pd
import pytest

from stocker.similarity.comment import FORBIDDEN_PHRASES, build_comment
from stocker.similarity.explain import (
    CycleExplanation,
    Explanation,
    MacroExplanation,
    MatchSummary,
    PriceExplanation,
    ThenNow,
)


def make_exp(
    dd=(-0.287, -0.298),
    days=(151, 135),
    dff=(6.44, 3.88),
    dgs10=(5.93, 4.95),
    spread=(-0.23, 0.32),
    oil=(29.0, 100.0),
    usd=(None, 121.7),
    vix=(18.8, 20.8),
    yoy=(0.407, 1.443),
    forward=None,
) -> Explanation:
    def m(sid, label, unit, tn):
        return MacroExplanation(series=sid, label=label, unit=unit, then=tn[0], now=tn[1])

    return Explanation(
        as_of=pd.Timestamp("2026-11-04"),
        match=MatchSummary(pd.Timestamp("2000-02-17"), pd.Timestamp("2000-08-08"), 0.95, {}),
        price=PriceExplanation(1, 3, ThenNow(*dd), ThenNow(*days)),
        macro=[
            m("DFF", "기준금리", "%", dff),
            m("DGS10", "10년물", "%", dgs10),
            m("T10Y2Y", "장단기 스프레드", "%p", spread),
            m("DCOILWTICO", "WTI 유가", "$", oil),
            m("DTWEXBGS", "달러지수", "", usd),
            m("VIXCLS", "VIX", "", vix),
        ],
        cycle=[CycleExplanation("wsts_sales_yoy", "반도체 매출 YoY", yoy[0], yoy[1])],
        forward={21: 0.151, 63: -0.216, 126: -0.331, 252: -0.385} if forward is None else forward,
    )


def sentences(text: str) -> list[str]:
    return [s for s in text.replace("다. ", "다.\n").split("\n") if s]


def test_full_comment_structure_for_2000_08_08():
    c = build_comment(make_exp())
    s = sentences(c)
    assert len(s) == 4
    assert "낙폭이 -28.7% 대 -29.8%로 거의 같고" in s[0]
    assert "둘 다 반도체 매출 증가기" in s[0]
    assert s[1].startswith("다만 기준금리는 그때가 2.6%p 높았고")
    assert "3.5배" in s[1]
    assert s[2] == "이 국면은 위치는 닮았지만 환경은 다른 사례다."
    assert s[3] == "이 국면 뒤 12개월 SOX는 -38.5%였다."


def test_deterministic():
    assert build_comment(make_exp()) == build_comment(make_exp())


@pytest.mark.parametrize(
    "dd, word",
    [
        ((-0.30, -0.33), "거의 같"),  # 정확히 3%p 이내
        ((-0.30, -0.3301), "비슷하"),  # 3%p 초과
        ((-0.30, -0.38), "비슷하"),  # 정확히 8%p
        ((-0.30, -0.3801), "다르"),  # 8%p 초과
    ],
)
def test_drawdown_thresholds(dd, word):
    c = build_comment(make_exp(dd=dd))
    drawdown_part = [p for p in c.split(",") if "낙폭이" in p][0]
    assert word in drawdown_part


@pytest.mark.parametrize(
    "dff, word",
    [
        ((4.0, 4.5), "같은 수준"),  # 0.5%p
        ((4.0, 4.51), "비슷하"),  # 0.5 초과
        ((4.0, 6.0), "비슷하"),  # 2%p
        ((4.0, 6.01), "그때가 2.0%p 낮았"),  # 2 초과 → 다른 점
    ],
)
def test_rate_thresholds(dff, word):
    c = build_comment(make_exp(dff=dff, dgs10=(None, None), spread=(None, None), oil=(None, None)))
    assert word in c


def test_ratio_threshold_two_times():
    calm = dict(dff=(4.0, 4.0), yoy=(0.3, 0.3))  # 다른 점 자리를 유가가 쓰도록 나머지는 같게
    just_below = build_comment(make_exp(oil=(50.0, 99.0), **calm))
    assert "벌어졌" not in just_below
    at_two = build_comment(make_exp(oil=(50.0, 100.0), **calm))
    assert "WTI 유가는 50달러 대 100달러로 2.0배 벌어졌다" in at_two


def test_missing_indicators_are_skipped():
    c = build_comment(make_exp(usd=(None, 121.7), yoy=(None, None), dgs10=(None, None)))
    assert "달러지수" not in c
    assert "매출" not in c
    assert "10년물" not in c
    assert "낙폭" in c


def test_all_optional_missing_still_produces_comment():
    exp = make_exp(
        dd=(None, None), days=(None, None), dff=(None, None), dgs10=(None, None),
        spread=(None, None), oil=(None, None), usd=(None, None), vix=(None, None),
        yoy=(None, None), forward={252: None},
    )  # fmt: skip
    c = build_comment(exp)
    assert "뚜렷하게 닮은 지표" in c
    assert "눈에 띄게 다른 지표는 없다" in c
    assert "SOX는" not in c  # 결과 문장은 값이 없으면 빠진다


def test_forward_falls_back_to_shorter_horizon():
    c = build_comment(make_exp(forward={21: 0.05, 63: None, 126: 0.12, 252: None}))
    assert "6개월 SOX는 +12.0%였다." in c


@pytest.mark.parametrize(
    "status, phrase",
    [
        ("strong", "위치·환경 모두 닮은 사례"),
        ("ok", "위치는 닮았지만 환경은 다른 사례"),
        ("weak", "닮았다고 보기 어려운 사례(참고용)"),
    ],
)
def test_verdict_follows_fit_status(status, phrase):
    class Fit:  # FitCheck 모양 (dataclass 대신 속성만)
        def __init__(self, status):
            self.status = status

    assert phrase in build_comment(make_exp(), Fit(status))
    assert phrase in build_comment(make_exp(), {"status": status})  # dict도 받는다


def test_verdict_without_fit_uses_counts():
    near = dict(
        dff=(4.0, 4.2), dgs10=(4.0, 4.3), spread=(0.5, 0.4), oil=(70.0, 80.0),
        vix=(20.0, 21.0), yoy=(0.3, 0.35),
    )  # fmt: skip
    assert "위치·환경 모두 닮은 사례" in build_comment(make_exp(**near))
    # 위치(낙폭)가 다르고 환경도 다름 → 참고용
    far = dict(dd=(-0.1, -0.4), dff=(1.0, 5.0), oil=(20.0, 100.0), yoy=(-0.2, 0.5))
    assert "닮았다고 보기 어려운 사례(참고용)" in build_comment(make_exp(**far))
    # 위치는 다르지만 환경은 닮음
    assert "환경은 닮았지만 위치는 다른 사례" in build_comment(make_exp(dd=(-0.1, -0.4), **near))


def test_wsts_direction_mismatch():
    c = build_comment(make_exp(yoy=(-0.1, 0.3)))
    assert "방향이 엇갈리" in c
    assert "둘 다 반도체 매출" not in c


@pytest.mark.parametrize(
    "exp",
    [
        make_exp(),
        make_exp(dd=(-0.1, -0.4), dff=(1.0, 5.0), oil=(20.0, 100.0), yoy=(-0.2, 0.5)),
        make_exp(forward={252: 0.5}),
    ],
)
def test_no_advice_or_forecast_phrases(exp):
    for status in (None, "strong", "ok", "weak"):
        c = build_comment(exp, None if status is None else {"status": status})
        for bad in FORBIDDEN_PHRASES:
            assert bad not in c
        assert "?" not in c and "!" not in c

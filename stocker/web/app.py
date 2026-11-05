"""Streamlit 엔트리. 화면 배치만 담당한다 (계산은 data.py·charts.py). 계약 10.2."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from stocker.web import charts, data

st.set_page_config(page_title="stocker — SOX 국면 유사도", layout="wide")

LAYER_WARNINGS = {
    "macro": "매크로 레이어 꺼짐 — FRED 키 필요 "
    "(`.env`의 FRED_API_KEY 후 `python -m stocker.ingest fred`)",
    "semi_cycle": "산업 사이클 레이어 꺼짐 — `make seed` 후 `python -m stocker.ingest semi`",
}
WINDOWS = (60, 120, 250)


def pct(x: float) -> str:
    return "—" if pd.isna(x) else f"{x:+.1f}%"


# ------------------------------------------------------------------ 데이터 로딩
try:
    close, _macro, _cycle = data.load_all()
except Exception as exc:  # noqa: BLE001
    st.error(
        f"DB에서 SOX 시세를 읽지 못했다: {exc.__class__.__name__}. "
        "`docker compose up -d --wait db` → `uv run python -m stocker.db init` → "
        "`uv run python -m stocker.ingest prices` 순서로 준비했는지 확인하라."
    )
    st.stop()

last_day = close.index[-1].date()

# ------------------------------------------------------------------ 사이드바
with st.sidebar:
    st.header("설정")
    as_of_in = st.date_input(
        "기준일 (as_of)",
        value=last_day,
        min_value=close.index[400].date(),
        max_value=last_day,
        help="이 날짜까지의 데이터만으로 '그날의 현재'를 만든다. "
        "과거 날짜를 고르면 그때 시점에서 본 유사 국면이 나온다.",
    )
    window = st.selectbox(
        "창 길이 (거래일)",
        WINDOWS,
        index=1,
        help="비교할 최근 구간의 길이. 120거래일 ≈ 6개월.",
    )
    top_k = st.slider("보여줄 과거 국면 수 (K)", 3, 10, 5)
    st.markdown("**레이어 가중치**")
    w_price = st.slider("가격 형태", 0.0, 1.0, 0.5, 0.05, help="DTW로 모양을 비교한다 (아래 설명).")
    w_macro = st.slider("매크로", 0.0, 1.0, 0.3, 0.05, help="금리·유가·달러·VIX 등의 상태 비교.")
    w_cycle = st.slider("산업 사이클", 0.0, 1.0, 0.2, 0.05, help="WSTS 반도체 매출 증감률 비교.")
    w_sum = w_price + w_macro + w_cycle
    if w_sum <= 0:
        st.error("가중치 합이 0이다. 하나 이상 올려라.")
        st.stop()
    st.caption(
        f"정규화 후: 가격 {w_price / w_sum:.0%} · 매크로 {w_macro / w_sum:.0%} · "
        f"산업 사이클 {w_cycle / w_sum:.0%}"
    )
    cycle_lag = st.slider(
        "산업 사이클 발표 지연 (일)",
        0,
        90,
        45,
        5,
        help="WSTS 월간 매출은 한 달 반쯤 뒤에 공개된다. 그만큼 늦게 알게 된 것으로 취급해 "
        "'그날 알 수 없던 정보'를 쓰지 않게 한다 (룩어헤드 방지).",
    )
    show_sim = st.toggle("SOXL 시뮬 보기", value=True)
    st.markdown("**이벤트 마커**")
    kinds = st.multiselect(
        "표시할 이벤트 종류",
        list(charts.EVENT_STYLES),
        default=list(charts.EVENT_STYLES),
        format_func=lambda k: charts.EVENT_STYLES[k][0],
        help="이벤트는 맥락 표시용이며 유사도 점수에는 들어가지 않는다.",
    )
    if st.button("DB 다시 읽기"):
        data.clear_cache()
        st.rerun()

# ------------------------------------------------------------------ 계산
weights = {
    "price_shape": w_price / w_sum,
    "macro": w_macro / w_sum,
    "semi_cycle": w_cycle / w_sum,
}
try:
    result = data.run_similarity(
        str(as_of_in), window, top_k, tuple(sorted(weights.items())), cycle_lag
    )
except ValueError as exc:
    st.error(f"이 설정으로는 계산할 수 없다: {exc}")
    st.stop()

matches = result.matches
cfg = result.config
anchor = result.as_of
summary = result.forward_summary
n = len(matches)

# ------------------------------------------------------------------ 1. 헤더
st.title("SOX 국면 유사도")
st.caption(
    "지금의 반도체 지수(SOX) 흐름이 과거 어느 때와 닮았는지 찾고, 그 뒤에 어떻게 됐는지 보여준다. "
    "예측기가 아니라 '닮은 과거 n개의 결과가 어떻게 갈렸는가'를 보는 도구다."
)
c1, c2, c3, c4 = st.columns(4)
c1.metric("기준일", f"{anchor:%Y-%m-%d}", help="요청일 이하의 마지막 거래일")
c2.metric(
    "후보 창 수",
    f"{result.n_candidates:,}",
    help="기준일과 최소 간격을 만족해 비교 대상이 된 과거 창의 수",
)
c3.metric(
    "표본 수 n", n, help="아래 결과가 몇 개 국면에서 나온 것인지. 작을수록 결론을 믿기 어렵다."
)
c4.metric("가중치(실제 적용)", " / ".join(f"{v:.0%}" for v in result.weights_used.values()))
for layer in result.layers_missing:
    st.warning(LAYER_WARNINGS.get(layer, f"{layer} 레이어 꺼짐"))
if result.layers_missing:
    st.caption(f"꺼진 레이어의 가중치는 남은 레이어에 다시 나눠 적용했다: {result.weights_used}")
if n < 5:
    st.warning(f"표본이 {n}개뿐이다. 중앙값 하나로 요약하지 말고 개별 국면을 보라.")

# ------------------------------------------------------------------ 3. 결과 카드
st.subheader("1. 가장 닮은 과거 국면")
table = charts.matches_frame(matches, cfg.horizons, window, close.index)
score_cfg = {
    name: st.column_config.NumberColumn(name, format="%.2f", help="0~1, 1이 가장 닮음")
    for name in [*charts.LAYER_NAMES.values(), "종합"]
}
ret_cfg = {
    f"{charts.horizon_name(h)} 후 (%)": st.column_config.NumberColumn(format="%+.1f")
    for h in cfg.horizons
}
st.dataframe(table, hide_index=True, column_config={**score_cfg, **ret_cfg}, width="stretch")
st.caption(
    "레이어 점수는 합치지 않고 항상 따로 보여준다. "
    "'—'는 그 레이어가 꺼졌거나 그 시기 데이터가 없다는 뜻. "
    "수익률 '—'은 아직 그만큼 시간이 지나지 않았다는 뜻."
)

# ------------------------------------------------------------------ 4·5. 겹친 차트 + 이벤트
st.subheader(
    "2. 현재 창 vs 과거 창",
    help="DTW(동적 시간 왜곡): 두 곡선의 모양이 비슷한지를 "
    "시간축을 조금 늘리고 줄여 가며 비교하는 방법. "
    "z-score: 값에서 평균을 빼고 표준편차로 나눠 '평소보다 얼마나 벗어났나'로 바꾼 것.",
)
paths = charts.build_overlay(close, matches, anchor, window)
events = data.load_events(f"{paths['date'].min():%Y-%m-%d}", f"{paths['date'].max():%Y-%m-%d}")
points = charts.attach_events(paths, events, kinds)
st.plotly_chart(charts.overlay_figure(paths, window, points), width="stretch")
if events.empty:
    st.caption("이벤트 데이터가 없어 마커 없이 그렸다 (`python -m stocker.ingest events`).")
else:
    st.caption(
        "굵은 빨간 선이 현재, 얇은 선이 과거 국면(창 안은 실선, 창이 끝난 뒤 1년은 점선). "
        "마커에 마우스를 올리면 이벤트 이름이 보인다. "
        "과거 이벤트는 사후 기록이라 당시 알려진 정보와 다를 수 있다."
    )

# ------------------------------------------------------------------ 6. 이후 수익 분포
st.subheader("3. 닮은 국면 이후의 수익")
left, right = st.columns([1, 1])
with left:
    st.dataframe(
        charts.forward_summary_frame(summary),
        column_config={
            c: st.column_config.NumberColumn(format="%+.1f")
            for c in ("중앙값 (%)", "하위 10% (%)", "상위 10% (%)")
        },
        width="stretch",
    )
    st.caption("하위/상위 10%는 표본이 작아 극단값에 가깝다. 상승·하락 개수가 갈리는지 먼저 보라.")
with right:
    st.plotly_chart(
        charts.forward_figure(matches, cfg.horizons, window, close.index), width="stretch"
    )

# ------------------------------------------------------------------ 7. SOXL 시뮬
if show_sim:
    st.subheader(
        "4. 같은 경로를 SOXL(3배 레버리지)로 들고 있었다면",
        help="레버리지 감가: 3배 ETF는 매일 3배로 맞추기 때문에 "
        "오르내림이 반복되면 지수가 제자리여도 "
        "가치가 깎인다. 추적오차: 시뮬레이션과 실제 ETF의 일일 수익률 차이의 변동성(연율).",
    )
    rf = data.load_rf()
    soxl = data.load_soxl()
    if rf is None:
        st.warning(
            "금융비용 0 가정(FRED 없음): 실제보다 시뮬이 낙관적으로 나온다 "
            "(3배 ETF는 빌린 돈의 이자를 낸다)."
        )
    sim_paths, sim_table = charts.build_sim(close, matches, window, soxl_adj=soxl, rf=rf)
    st.plotly_chart(charts.sim_figure(sim_paths, window), width="stretch")
    st.dataframe(
        sim_table,
        hide_index=True,
        column_config={
            c: st.column_config.NumberColumn(format="%+.1f") for c in charts.SIM_SUMMARY_COLS[1:5]
        }
        | {charts.SIM_SUMMARY_COLS[5]: st.column_config.NumberColumn(format="%.1f")},
        width="stretch",
    )
    st.caption(
        "가정: 3배, 보수 0.75%, 금융비용 = 기준금리(DFF) + 1.36%p "
        "(SOXX→SOXL 실제 데이터로 보정한 값). "
        "실선은 시뮬, 점선은 실제 SOXL(2010년 3월 상장 이후만). 상장 전 구간에 걸친 창은 "
        "실제 SOXL이 처음 생긴 날의 시뮬 값에서 출발시킨다. "
        "'12개월 후'와 '최대낙폭'은 창이 끝난 날부터 센다."
    )

# ------------------------------------------------------------------ 8. 정직성 푸터
st.divider()
long_h = max(cfg.horizons)
row = summary.loc[long_h]
st.info(
    f"표본 {n}개. 유사 국면의 결과는 갈릴 수 있으며, 이 도구는 예측하지 않는다. "
    f"{charts.horizon_name(long_h)} 뒤 기준 상승 {int(row['n_up'])}개 · "
    f"하락 {int(row['n_down'])}개 "
    f"(결과가 나온 {int(row['n'])}개 중)."
)
st.caption(f"데이터 마지막 날: {last_day} · 생성: {dt.datetime.now():%Y-%m-%d %H:%M}")

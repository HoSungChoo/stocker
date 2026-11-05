"""대시보드 차트와 차트 입력 표 만들기. streamlit을 import하지 않는 순수 함수 모음.

입력은 pandas 객체, 출력은 DataFrame 또는 plotly Figure다. DB·네트워크 접근 없음.
trace의 `meta` 값(`current`, `past`, `future`, `event`, `sim`, `actual`, `forward`)으로
종류를 구분하므로 테스트와 호출자가 trace 수를 셀 수 있다.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from stocker.sim import simulate_leveraged

EXTEND_DAYS = 252  # 과거 창이 끝난 뒤 보여줄 거래일 수 (1년)
LEVERAGE = 3.0
EXPENSE_RATIO = 0.0075  # SOXL 총보수 근사 (연율)
FINANCING_SPREAD = 0.0136  # 계약 4절 검증값 (SOXX→SOXL 보정)

CURRENT_COLOR = "#FF4B4B"
PAST_COLORS = [
    "#1f77b4",
    "#2ca02c",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#17becf",
    "#bcbd22",
    "#7f7f7f",
    "#ff7f0e",
    "#393b79",
]

# kind → (한국어 이름, 마커 모양, 색)
EVENT_STYLES: dict[str, tuple[str, str, str]] = {
    "fomc": ("FOMC", "diamond", "#0072B2"),
    "cpi": ("CPI", "square", "#E69F00"),
    "jobs": ("고용지표", "triangle-up", "#009E73"),
    "earnings": ("실적", "circle", "#56B4E9"),
    "policy": ("정책·규제", "star", "#882255"),
    "war": ("전쟁", "x", "#555555"),
    "election": ("선거", "pentagon", "#117733"),
}

LAYER_NAMES = {"price_shape": "가격 형태", "macro": "매크로", "semi_cycle": "산업 사이클"}


def horizon_name(h: int) -> str:
    """거래일 수 → '1개월' 식 이름 (21거래일 ≈ 1개월)."""
    return f"{round(h / 21)}개월"


def match_label(match, window: int, index: pd.DatetimeIndex | None = None) -> str:
    """범례·표에 쓰는 구간 이름 'YYYY-MM-DD ~ YYYY-MM-DD'."""
    start = getattr(match, "start_date", None)
    if start is None and index is not None:
        pos = index.get_loc(match.end_date)
        start = index[max(pos - window + 1, 0)]
    end = pd.Timestamp(match.end_date)
    if start is None:
        return f"{end:%Y-%m-%d} 끝"
    return f"{pd.Timestamp(start):%Y-%m-%d} ~ {end:%Y-%m-%d}"


# ---------------------------------------------------------------------------
# 표 / 가공
# ---------------------------------------------------------------------------


def matches_frame(matches: Sequence, horizons: Sequence[int], window: int, index=None):
    """상위 K 국면 표. 수익률은 %, 점수는 0~1. 없는 레이어 점수는 NaN."""
    rows = []
    for m in matches:
        row = {"구간": match_label(m, window, index)}
        for key, name in LAYER_NAMES.items():
            row[name] = m.scores.get(key, np.nan)
        row["종합"] = m.combined
        for h in horizons:
            row[f"{horizon_name(h)} 후 (%)"] = m.forward.get(h, np.nan) * 100
        rows.append(row)
    return pd.DataFrame(rows)


def forward_summary_frame(summary: pd.DataFrame) -> pd.DataFrame:
    """engine의 forward_summary(수익률은 소수)를 화면용 표(%)로 바꾼다."""
    out = pd.DataFrame(index=[horizon_name(h) for h in summary.index])
    out["중앙값 (%)"] = summary["median"].to_numpy() * 100
    out["하위 10% (%)"] = summary["p10"].to_numpy() * 100
    out["상위 10% (%)"] = summary["p90"].to_numpy() * 100
    out["상승"] = summary["n_up"].to_numpy()
    out["하락"] = summary["n_down"].to_numpy()
    out["표본 n"] = summary["n"].to_numpy()
    return out


def build_overlay(
    close: pd.Series, matches: Sequence, as_of, window: int, extend: int = EXTEND_DAYS
) -> pd.DataFrame:
    """현재 창과 과거 창을 '창 시작=100', x='창 시작 후 거래일'로 맞춘 long 표.

    columns: label, role(current|past), day, date, level.
    현재 창은 window개 점까지만, 과거 창은 window 이후 extend일까지(미래 데이터가 있는 만큼).
    """
    close = close.dropna().sort_index()
    cur = close.loc[:as_of].iloc[-window:]
    frames = [_path_frame("현재", "current", cur)]
    for m in matches:
        pos = close.index.get_loc(m.end_date)
        seg = close.iloc[max(pos - window + 1, 0) : pos + extend + 1]
        frames.append(_path_frame(match_label(m, window, close.index), "past", seg))
    return pd.concat(frames, ignore_index=True)


def _path_frame(label: str, role: str, seg: pd.Series) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "label": label,
            "role": role,
            "day": np.arange(len(seg)),
            "date": seg.index,
            "level": (seg / seg.iloc[0] * 100).to_numpy(),
        }
    )


def attach_events(paths: pd.DataFrame, events: pd.DataFrame | None, kinds=None) -> pd.DataFrame:
    """각 경로 위에 이벤트를 올린다(휴일 이벤트는 다음 거래일에). 없으면 빈 표.

    columns: label, role, day, date(거래일), level, kind, title, symbol, scheduled.
    """
    cols = ["label", "role", "day", "date", "level", "kind", "title", "symbol", "scheduled"]
    if events is None or events.empty or paths.empty:
        return pd.DataFrame(columns=cols)
    ev = events.copy()
    ev["date"] = pd.to_datetime(ev["date"])
    if kinds is not None:
        ev = ev[ev["kind"].isin(list(kinds))]
    out = []
    for _label, g in paths.groupby("label", sort=False):
        g = g.sort_values("day").reset_index(drop=True)
        sub = ev[(ev["date"] >= g["date"].iloc[0]) & (ev["date"] <= g["date"].iloc[-1])]
        if sub.empty:
            continue
        pos = g["date"].searchsorted(sub["date"].to_numpy(), side="left")
        hit = g.iloc[pos][["label", "role", "day", "date", "level"]].reset_index(drop=True)
        meta = sub[["kind", "title", "symbol", "scheduled"]].reset_index(drop=True)
        out.append(pd.concat([hit, meta], axis=1))
    if not out:
        return pd.DataFrame(columns=cols)
    return pd.concat(out, ignore_index=True)[cols]


# ---------------------------------------------------------------------------
# 겹친 차트 (화면 4·5)
# ---------------------------------------------------------------------------


def overlay_figure(paths: pd.DataFrame, window: int, event_points: pd.DataFrame | None = None):
    """현재 창(굵은 선)과 과거 창(얇은 선, window 이후는 점선)을 겹친다.

    trace 구성: 과거 창마다 solid 1(meta=past) + 점선 연장 1(meta=future),
    현재 창 1(meta=current), 이벤트 kind마다 마커 1(meta=event).
    """
    fig = go.Figure()
    past_labels = list(paths.loc[paths["role"] == "past", "label"].unique())
    for i, label in enumerate(past_labels):
        g = paths[paths["label"] == label]
        color = PAST_COLORS[i % len(PAST_COLORS)]
        inside = g[g["day"] < window]
        after = g[g["day"] >= window - 1]  # 이어 보이도록 마지막 창 점을 포함
        fig.add_trace(_line(inside, label, color, 1.6, "solid", "past", True, label))
        fig.add_trace(_line(after, label, color, 1.4, "dot", "future", False, label))
    cur = paths[paths["role"] == "current"]
    if not cur.empty:
        fig.add_trace(_line(cur, "현재", CURRENT_COLOR, 4.5, "solid", "current", True, "현재"))
    if event_points is not None and not event_points.empty:
        for kind, g in event_points.groupby("kind", sort=False):
            name, symbol, color = EVENT_STYLES.get(kind, (kind, "circle", "#999999"))
            text = [
                f"{t}" + (f" ({s})" if isinstance(s, str) and s else "")
                for t, s in zip(g["title"], g["symbol"], strict=True)
            ]
            fig.add_trace(
                go.Scatter(
                    x=g["day"],
                    y=g["level"],
                    mode="markers",
                    name=name,
                    legendgroup=f"event-{kind}",
                    marker={
                        "symbol": symbol,
                        "color": color,
                        "size": 9,
                        "line": {"width": 1, "color": "white"},
                    },
                    text=text,
                    customdata=np.stack([g["date"].dt.strftime("%Y-%m-%d"), g["label"]], axis=-1),
                    hovertemplate="%{text}<br>%{customdata[0]}<br>%{customdata[1]}<extra></extra>",
                    meta="event",
                )
            )
    fig.add_vline(
        x=window - 1,
        line={"dash": "dash", "color": "gray", "width": 1},
        annotation_text="현재 창 끝 → 점선은 과거 창의 '그 뒤'",
        annotation_position="top left",
    )
    fig.update_layout(
        height=540,
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
        xaxis_title="창 시작 후 거래일",
        yaxis_title="창 시작 = 100",
        hovermode="closest",
        legend={"orientation": "h", "y": -0.18},
    )
    return fig


def _line(g, label, color, width, dash, meta, showlegend, group):
    return go.Scatter(
        x=g["day"],
        y=g["level"],
        mode="lines",
        name=label,
        legendgroup=group,
        showlegend=showlegend,
        line={"color": color, "width": width, "dash": dash},
        customdata=g["date"].dt.strftime("%Y-%m-%d"),
        hovertemplate=f"{label}<br>%{{customdata}}<br>%{{y:.1f}}<extra></extra>",
        meta=meta,
    )


# ---------------------------------------------------------------------------
# 이후 수익 분포 (화면 6)
# ---------------------------------------------------------------------------


def forward_figure(matches: Sequence, horizons: Sequence[int], window: int, index=None):
    """horizon별 박스 + 개별 점(표본이 작으므로 점을 전부 찍는다). 단위 %."""
    fig = go.Figure()
    labels = [match_label(m, window, index) for m in matches]
    for h in horizons:
        vals = np.array([m.forward.get(h, np.nan) for m in matches], dtype=float) * 100
        keep = ~np.isnan(vals)
        fig.add_trace(
            go.Box(
                y=vals[keep],
                text=[lab for lab, k in zip(labels, keep, strict=True) if k],
                name=horizon_name(h),
                boxpoints="all",
                jitter=0.4,
                pointpos=0,
                marker={"size": 9},
                hovertemplate="%{text}<br>%{y:.1f}%<extra></extra>",
                meta="forward",
            )
        )
    fig.add_hline(y=0, line={"color": "gray", "width": 1, "dash": "dash"})
    fig.update_layout(
        height=380,
        margin={"l": 10, "r": 10, "t": 20, "b": 10},
        yaxis_title="이후 수익률 (%)",
        showlegend=False,
    )
    return fig


# ---------------------------------------------------------------------------
# SOXL 시뮬 (화면 7)
# ---------------------------------------------------------------------------

SIM_SUMMARY_COLS = [
    "구간",
    "SOX 12개월 후 (%)",
    "SOXL 시뮬 12개월 후 (%)",
    "SOXL 시뮬 최대낙폭 (%)",
    "실제 SOXL 12개월 후 (%)",
    "시뮬-실제 추적오차 (연, %)",
]


def build_sim(
    close: pd.Series,
    matches: Sequence,
    window: int,
    soxl_adj: pd.Series | None = None,
    rf: pd.Series | None = None,
    extend: int = EXTEND_DAYS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """각 과거 창(window + extend)의 SOX 경로를 SOXL(3x)로 시뮬한다.

    rf: 연율 DFF(퍼센트 단위, 5.33 = 5.33%). None이면 금융비용 0.
    soxl_adj: 실제 SOXL 조정종가. 겹치는 구간은 첫 겹침일에 시뮬 NAV와 같은 값에서 출발시켜
              (창 전체가 SOXL 상장 이후면 창 시작=100) 시뮬과의 차이를 눈으로 비교한다.
    반환: (경로 long 표[label,day,date,sox,sim,actual], 요약 표[SIM_SUMMARY_COLS]).
    요약의 '12개월 후'는 창 끝 기준 extend일 뒤 값이며, 그만큼의 미래가 없으면 NaN.
    """
    close = close.dropna().sort_index()
    fin = rf if rf is not None else 0.0
    paths, rows = [], []
    for m in matches:
        pos = close.index.get_loc(m.end_date)
        seg = close.iloc[max(pos - window + 1, 0) : pos + extend + 1]
        nav = simulate_leveraged(
            seg.pct_change(),
            leverage=LEVERAGE,
            expense_ratio=EXPENSE_RATIO,
            financing_rate=fin,
            financing_spread=FINANCING_SPREAD,
            rate_scale="percent",
        )
        sox = seg / seg.iloc[0] * 100
        actual = _anchored_actual(soxl_adj, nav)
        label = match_label(m, window, close.index)
        paths.append(
            pd.DataFrame(
                {
                    "label": label,
                    "day": np.arange(len(seg)),
                    "date": seg.index,
                    "sox": sox.to_numpy(),
                    "sim": nav.to_numpy(),
                    "actual": actual.to_numpy(),
                }
            )
        )
        rows.append(_sim_row(label, sox, nav, actual, window, extend))
    path_df = (
        pd.concat(paths, ignore_index=True)
        if paths
        else pd.DataFrame(columns=["label", "day", "date", "sox", "sim", "actual"])
    )
    return path_df, pd.DataFrame(rows, columns=SIM_SUMMARY_COLS)


def _anchored_actual(soxl_adj: pd.Series | None, nav: pd.Series) -> pd.Series:
    empty = pd.Series(np.nan, index=nav.index)
    if soxl_adj is None or soxl_adj.empty:
        return empty
    a = soxl_adj.reindex(nav.index)
    valid = a.dropna()
    if valid.empty:
        return empty
    first = valid.index[0]
    return a / valid.iloc[0] * nav.loc[first]


def _after_return(s: pd.Series, window: int, extend: int) -> float:
    """창 끝 → extend거래일 뒤 수익률(소수). 미래가 모자라거나 값이 없으면 NaN."""
    if len(s) < window + extend:
        return np.nan
    a, b = s.iloc[window - 1], s.iloc[window - 1 + extend]
    return float(b / a - 1) if pd.notna(a) and pd.notna(b) and a > 0 else np.nan


def _sim_row(label, sox, nav, actual, window, extend) -> dict:
    after = nav.iloc[window - 1 :]
    drawdown = float((after / after.cummax() - 1).min()) if len(after) else np.nan
    both = pd.concat([nav, actual], axis=1, keys=["sim", "act"]).dropna()
    both = both[both["sim"] > 0]
    te = np.nan
    if len(both) >= 21:
        diff = np.log(both["sim"]).diff() - np.log(both["act"]).diff()
        te = float(diff.std(ddof=1) * np.sqrt(252))
    return {
        "구간": label,
        "SOX 12개월 후 (%)": _after_return(sox, window, extend) * 100,
        "SOXL 시뮬 12개월 후 (%)": _after_return(nav, window, extend) * 100,
        "SOXL 시뮬 최대낙폭 (%)": drawdown * 100,
        "실제 SOXL 12개월 후 (%)": _after_return(actual, window, extend) * 100,
        "시뮬-실제 추적오차 (연, %)": te * 100,
    }


def sim_figure(sim_paths: pd.DataFrame, window: int):
    """SOXL 시뮬 NAV(실선)와 실제 SOXL(점선)을 겹친다. 로그 눈금, 창 시작=100."""
    fig = go.Figure()
    for i, (label, g) in enumerate(sim_paths.groupby("label", sort=False)):
        color = PAST_COLORS[i % len(PAST_COLORS)]
        d = g["date"].dt.strftime("%Y-%m-%d")
        fig.add_trace(
            go.Scatter(
                x=g["day"],
                y=g["sim"],
                mode="lines",
                name=label,
                legendgroup=label,
                line={"color": color, "width": 2},
                customdata=d,
                hovertemplate=f"SOXL 시뮬 {label}<br>%{{customdata}}<br>%{{y:.1f}}<extra></extra>",
                meta="sim",
            )
        )
        act = g.dropna(subset=["actual"])
        if not act.empty:
            fig.add_trace(
                go.Scatter(
                    x=act["day"],
                    y=act["actual"],
                    mode="lines",
                    name=f"{label} (실제 SOXL)",
                    legendgroup=label,
                    showlegend=False,
                    line={"color": color, "width": 2, "dash": "dot"},
                    customdata=act["date"].dt.strftime("%Y-%m-%d"),
                    hovertemplate=(
                        f"실제 SOXL {label}<br>%{{customdata}}<br>%{{y:.1f}}<extra></extra>"
                    ),
                    meta="actual",
                )
            )
    fig.add_vline(x=window - 1, line={"dash": "dash", "color": "gray", "width": 1})
    fig.update_layout(
        height=460,
        margin={"l": 10, "r": 10, "t": 20, "b": 10},
        xaxis_title="창 시작 후 거래일",
        yaxis_title="창 시작 = 100 (로그 눈금)",
        yaxis_type="log",
        hovermode="closest",
        legend={"orientation": "h", "y": -0.2},
    )
    return fig

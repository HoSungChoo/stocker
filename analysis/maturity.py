"""계산 1 — 성숙도 계기판 평가. 제품 코드를 바꾸지 않고 stocker.checklist 규칙을 그대로 쓴다.

실행: uv run --with matplotlib python analysis/maturity.py   (DB `db` 컨테이너 필요)

산출물(analysis/out/):
- maturity_monthly.csv : 1990-01~2025-10 매월 말 신호 9개 켜짐/꺼짐/없음, 켜진 개수(원 규칙·대체 규칙), 결과 변수
- maturity_buckets.md  : 켜진 개수 묶음별 정점까지 개월 수·이후 낙폭 분위표, 거짓 경보, 오늘의 위치
- maturity_episodes.csv: 개수 4 이상 연속 구간(에피소드) 목록
- maturity_timeseries.png : 개수 시계열 + SOX (1999~2000, 2007~2008, 2021~2022 음영)

룩어헤드:
- 신호(켜진 개수)는 evaluate()가 날짜 이하 데이터만 쓴다(룩어헤드 없음). BAA10Y 대체 규칙도 날짜 이하만 쓴다.
- "정점까지 개월 수"와 "정점 이후 낙폭"은 미래 종가를 쓰는 결과 변수다. 표시·평가용이며 신호가 아니다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from stocker.checklist import RULES, evaluate, load_inputs  # noqa: E402
from stocker.checklist.rules import DAILY_STALE_DAYS  # noqa: E402

DATA = ROOT / "analysis" / "data"
OUT = ROOT / "analysis" / "out"

START, END = "1990-01-31", "2026-10-31"  # 2025-11 이후는 결과 변수가 불완전(분위표에서 제외)
TODAY = "2026-11-04"
BAA_THRESHOLD = 3.0  # %p. 근거는 보고서 참조(2002·2008·2020 위기 최고치 3.9·6.16·4.31이 모두 넘음)
FWD_MONTHS = 24  # "이후 2년 내 최고가"
MAJOR_TOP_DECLINE = 0.30  # 주요 정점: 이후 새 고점 전에 30% 이상 하락한 고점
BUCKETS = [(0, 1, "0~1"), (2, 3, "2~3"), (4, 5, "4~5"), (6, 99, "6+")]
SHADE = [("1999-01-01", "2000-12-31"), ("2007-01-01", "2008-12-31"), ("2021-01-01", "2022-12-31")]


def _on(x) -> bool:
    """켜짐 판정(값이 None/NaN이면 False). DataFrame을 거치면 numpy bool이 되므로 `is True`를 쓰지 않는다."""
    return x is not None and not pd.isna(x) and bool(x)


def bucket_of(n: float) -> str:
    for lo, hi, name in BUCKETS:
        if lo <= n <= hi:
            return name
    return "?"


def baa_alt(baa: pd.Series, date: pd.Timestamp) -> tuple[bool | None, float | None]:
    """BAA10Y 대체 위기 신호: 날짜 이하 마지막 값 ≥ 임계값. 일간 시리즈 규칙과 같은 공백 기준."""
    s = baa.loc[:date]
    if s.empty or (date - s.index[-1]).days > DAILY_STALE_DAYS:
        return None, None
    v = float(s.iloc[-1])
    return v >= BAA_THRESHOLD, v


def major_tops(close: pd.Series, decline: float) -> list[pd.Timestamp]:
    """지그재그 방식의 주요 정점: 그 고점에서 decline 이상 떨어진 뒤에야 확정되고, 저점은 그 저점에서
    decline 이상 오른 뒤에야 확정된다(전체 역사 사용 → 룩어헤드. 표시용). 2007-07처럼 사상 최고가가
    아닌 고점도 잡는다."""
    tops: list[pd.Timestamp] = []
    peak_v, peak_d = close.iloc[0], close.index[0]
    trough_v, trough_d = close.iloc[0], close.index[0]
    looking_for_top = True
    for d, v in close.items():
        if looking_for_top:
            if v > peak_v:
                peak_v, peak_d = v, d
            elif v / peak_v - 1 <= -decline:
                tops.append(peak_d)
                trough_v, trough_d = v, d
                looking_for_top = False
        else:
            if v < trough_v:
                trough_v, trough_d = v, d
            elif v / trough_v - 1 >= decline:
                peak_v, peak_d = v, d
                looking_for_top = True
    return tops


def months_between(a: pd.Timestamp, b: pd.Timestamp) -> float:
    return round((b - a).days / 30.4375, 1)


def outcomes(close: pd.Series, date: pd.Timestamp, tops: list[pd.Timestamp]) -> dict:
    """결과 변수(룩어헤드 있음, 표시용)."""
    last = close.index[-1]
    end = date + pd.DateOffset(months=FWD_MONTHS)
    out = {
        "fwd_window_complete": end <= last,
        "peak_date": pd.NaT,
        "months_to_peak_24m": np.nan,
        "peak_gain": np.nan,
        "dd_after_peak": np.nan,
        "dd_after_peak_complete": False,
        "months_to_major_top": np.nan,
    }
    w = close.loc[date:end]
    w = w[w.index > date]
    if not w.empty and not close.loc[:date].empty:
        p = w.idxmax()
        out["peak_date"] = p
        out["months_to_peak_24m"] = months_between(date, p)
        base = close.loc[:date].iloc[-1]
        out["peak_gain"] = float(w.max() / base - 1)
        after = close.loc[p : p + pd.DateOffset(months=FWD_MONTHS)]
        out["dd_after_peak"] = float(after.min() / w.max() - 1)
        out["dd_after_peak_complete"] = (p + pd.DateOffset(months=FWD_MONTHS)) <= last
    nxt = [t for t in tops if t >= date]
    if nxt:
        out["months_to_major_top"] = months_between(date, nxt[0])
    return out


def quantile_row(x: pd.Series) -> dict:
    x = x.dropna()
    if x.empty:
        return {"p10": np.nan, "p50": np.nan, "p90": np.nan}
    return {"p10": x.quantile(0.1), "p50": x.quantile(0.5), "p90": x.quantile(0.9)}


def episodes(mask: pd.Series) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """True가 연속된 달을 하나의 에피소드로."""
    eps, start, prev = [], None, None
    for d, m in mask.items():
        if m and start is None:
            start = d
        if not m and start is not None:
            eps.append((start, prev))
            start = None
        prev = d
    if start is not None:
        eps.append((start, prev))
    return eps


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    series, close = load_inputs()
    baa = pd.read_csv(DATA / "fred_BAA10Y.csv", index_col=0, parse_dates=True)["value"].dropna()
    tops = major_tops(close, MAJOR_TOP_DECLINE)

    dates = list(pd.date_range(START, END, freq="ME")) + [pd.Timestamp(TODAY)]
    rows = []
    for d in dates:
        sig = evaluate(d, series, close)
        row = {"date": d}
        for s in sig:
            row[s.id] = s.on
            row[f"{s.id}_value"] = s.value
        on_alt, v_alt = baa_alt(baa, d)
        row["crisis_hy_alt"] = on_alt
        row["crisis_hy_alt_value"] = v_alt
        avail = [s.on for s in sig if s.on is not None]
        row["n_on"] = sum(avail)
        row["n_avail"] = len(avail)
        # 대체 규칙: crisis_hy를 BAA10Y 규칙으로 바꾼다(다른 8개는 그대로)
        alt = [s.on for s in sig if s.id != "crisis_hy" and s.on is not None] + (
            [on_alt] if on_alt is not None else []
        )
        row["n_on_alt"] = sum(alt)
        row["n_avail_alt"] = len(alt)
        # DFF 월말 잡음 점검: 같은 달 20일에 평가한 연준 인하 신호
        d20 = d.replace(day=20) if d.day > 20 else d
        sig20 = {s.id: s.on for s in evaluate(d20, series, close)}
        row["bottom_fed_cut_d20"] = sig20["bottom_fed_cut"]
        # 보정 개수: 대체 규칙 + 연준 인하 신호만 같은 달 20일 값으로(월말 DFF 잡음 제거)
        robust = [
            s.on for s in sig if s.id not in ("crisis_hy", "bottom_fed_cut") and s.on is not None
        ]
        robust += [on_alt] if on_alt is not None else []
        robust += [sig20["bottom_fed_cut"]] if sig20["bottom_fed_cut"] is not None else []
        row["n_on_robust"] = sum(robust)
        row.update(outcomes(close, d, tops))
        rows.append(row)
    df = pd.DataFrame(rows).set_index("date")
    df["bucket"] = df["n_on"].map(bucket_of)
    df["bucket_alt"] = df["n_on_alt"].map(bucket_of)
    df["bucket_robust"] = df["n_on_robust"].map(bucket_of)
    df.to_csv(OUT / "maturity_monthly.csv")

    today = df.loc[pd.Timestamp(TODAY)]
    hist = df.loc[:END]
    # 결과 변수가 완전한(24개월 창이 다 지난) 달만 분위표에 넣는다
    comp = hist[hist["fwd_window_complete"]]

    md = ["# 계산 1 — 성숙도 계기판 (자동 생성: analysis/maturity.py)", ""]
    md.append(
        f"- 평가 달 수: {len(hist)} (1990-01~2026-10 월말). 결과 변수가 완전한 달(24개월 창이 지난 달): "
        f"{len(comp)} ({comp.index[0].date()}~{comp.index[-1].date()})."
    )
    md.append(
        f"- 주요 정점(이후 새 고점 전에 {int(MAJOR_TOP_DECLINE * 100)}% 이상 하락한 SOX 고점, 전체 역사 사용): "
        + ", ".join(t.strftime("%Y-%m-%d") for t in tops)
    )
    md.append(f"- BAA10Y 대체 위기 규칙 임계값: {BAA_THRESHOLD}%p.")
    md.append("")

    for label, ncol, bcol in [
        ("원 규칙", "n_on", "bucket"),
        ("대체 규칙(BAA10Y)", "n_on_alt", "bucket_alt"),
        ("보정 규칙(대체 + 연준 인하는 20일 값)", "n_on_robust", "bucket_robust"),
    ]:
        md.append(f"## 켜진 개수 묶음별 결과 — {label}")
        md.append("")
        md.append(
            "| 묶음 | 달 수 | 독립 에피소드 | 정점까지 개월(10/50/90%) | 정점까지 상승(50%) "
            "| 정점 이후 낙폭(10/50/90%) | 주요 정점까지 개월(50%) | 2년 안에 주요 정점이 안 온 달 비율 |"
        )
        md.append("|---|---|---|---|---|---|---|---|")
        for _, _, name in BUCKETS:
            g = comp[comp[bcol] == name]
            eps = episodes(hist[bcol] == name)
            q1 = quantile_row(g["months_to_peak_24m"])
            q2 = quantile_row(g["dd_after_peak"])
            mt = g["months_to_major_top"]
            no_top = ((mt > 24) | mt.isna()).mean() if len(g) else np.nan
            md.append(
                f"| {name} | {len(g)} | {len(eps)} | {q1['p10']:.0f} / {q1['p50']:.0f} / {q1['p90']:.0f} "
                f"| {g['peak_gain'].median() * 100:+.0f}% "
                f"| {q2['p10'] * 100:.0f}% / {q2['p50'] * 100:.0f}% / {q2['p90'] * 100:.0f}% "
                f"| {mt.median():.0f} | {no_top * 100:.0f}% |"
                if len(g)
                else f"| {name} | 0 | {len(eps)} | – | – | – | – | – |"
            )
        md.append("")

    # 단조성: 주요 정점 직전 개월 구간별 평균 개수
    md.append("## 주요 정점 전 개월 구간별 평균 켜진 개수 (원 규칙 / 대체 규칙)")
    md.append("")
    md.append(
        "| 정점까지 남은 개월 | 달 수 | 평균 개수(원) | 평균 개수(대체) | 개수 4 이상 비율(대체) |"
    )
    md.append("|---|---|---|---|---|")
    bins = [(0, 3), (4, 6), (7, 12), (13, 24), (25, 48), (49, 999)]
    for lo, hi in bins:
        g = hist[(hist["months_to_major_top"] >= lo) & (hist["months_to_major_top"] <= hi)]
        if len(g):
            md.append(
                f"| {lo}~{hi if hi < 999 else '∞'} | {len(g)} | {g['n_on'].mean():.2f} | "
                f"{g['n_on_alt'].mean():.2f} | {(g['n_on_alt'] >= 4).mean() * 100:.0f}% |"
            )
    rho = hist[["months_to_major_top", "n_on_alt"]].dropna().corr(method="spearman").iloc[0, 1]
    md.append("")
    md.append(
        f"- 스피어만 상관(주요 정점까지 개월 vs 대체 개수, n={hist['months_to_major_top'].notna().sum()}): {rho:+.2f} (음수면 정점에 가까울수록 개수가 큼)"
    )
    md.append("")

    # 에피소드 (대체 규칙 개수 4 이상)
    md.append("## 켜진 개수 4 이상 에피소드 (대체 규칙 기준, 연속된 달을 하나로)")
    md.append("")
    md.append(
        "| 시작 | 끝 | 길이(달) | 최대 개수 | 시작 시점 켜진 신호 | 시작→주요 정점(개월) | 2년 내 최고가 이후 낙폭 | 판정 |"
    )
    md.append("|---|---|---|---|---|---|---|---|")
    ep_rows = []
    sig_ids = [r.id for r in RULES if r.id != "crisis_hy"] + ["crisis_hy_alt"]
    for s, e in episodes(hist["n_on_alt"] >= 4):
        g = hist.loc[s:e]
        first = hist.loc[s]
        on_ids = [i for i in sig_ids if _on(first[i])]
        mt = first["months_to_major_top"]
        dd = first["dd_after_peak"]
        if pd.isna(mt):
            verdict = "판정 불가(이후 주요 정점 아직 없음)"
        elif mt > 24:
            verdict = "거짓 경보(2년 넘게 정점 없음)"
        else:
            verdict = "정점 2년 내"
        ep_rows.append(
            {
                "start": s,
                "end": e,
                "months": len(g),
                "max_n": g["n_on_alt"].max(),
                "signals_at_start": "+".join(on_ids),
                "months_to_major_top": mt,
                "dd_after_peak": dd,
                "verdict": verdict,
            }
        )
        md.append(
            f"| {s:%Y-%m} | {e:%Y-%m} | {len(g)} | {g['n_on_alt'].max()} | {', '.join(on_ids)} | "
            f"{'–' if pd.isna(mt) else f'{mt:.0f}'} | {'–' if pd.isna(dd) else f'{dd * 100:.0f}%'} | {verdict} |"
        )
    pd.DataFrame(ep_rows).to_csv(OUT / "maturity_episodes.csv", index=False)
    md.append("")

    # 세 에피소드에서 개수가 어디까지 올라갔나
    md.append("## 세 에피소드의 최대 개수 (대체 규칙)")
    md.append("")
    md.append("| 구간 | 최대 개수 | 그 달 | 그때 켜진 신호 | 구간 내 주요 정점 |")
    md.append("|---|---|---|---|---|")
    for a, b in SHADE:
        g = hist.loc[a:b]
        m = g["n_on_alt"].idxmax()
        on_ids = [i for i in sig_ids if _on(hist.loc[m, i])]
        tt = [t.strftime("%Y-%m-%d") for t in tops if pd.Timestamp(a) <= t <= pd.Timestamp(b)]
        md.append(
            f"| {a[:4]}–{b[:4]} | {int(g['n_on_alt'].max())} | {m:%Y-%m} | {', '.join(on_ids)} | {', '.join(tt) or '없음'} |"
        )
    md.append("")

    # DFF 월말 잡음
    diff = hist[(hist["bottom_fed_cut"] == True) & (hist["bottom_fed_cut_d20"] == False)]  # noqa: E712
    md.append(
        f"- 연준 인하 신호(DFF 30일 변화)가 월말에는 켜졌지만 같은 달 20일에는 꺼져 있던 달: {len(diff)}개 "
        f"(실효연방기금금리의 월말·연말 일시 급변 때문. 예: {', '.join(d.strftime('%Y-%m') for d in diff.index[:6])})"
    )
    md.append("")

    # 최근 12개월 경로
    md.append("## 최근 12개월 켜진 개수 (대체 규칙; 결과 변수는 아직 없음)")
    md.append("")
    md.append("| 월말 | 개수 | 켜진 신호 | SOX 고점 대비 |")
    md.append("|---|---|---|---|")
    ath = close.cummax()
    for d, r in hist.loc["2025-11-01":].iterrows():
        on_ids = [i for i in sig_ids if _on(r[i])]
        c = close.loc[:d]
        md.append(
            f"| {d:%Y-%m} | {int(r['n_on_alt'])} | {', '.join(on_ids) or '없음'} | {(c.iloc[-1] / ath.loc[:d].iloc[-1] - 1) * 100:+.0f}% |"
        )
    md.append(
        f"- SOX 사상 최고 {close.idxmax():%Y-%m-%d} ({close.max():,.0f}), {close.index[-1]:%Y-%m-%d} 종가 {close.iloc[-1]:,.0f} = 고점 대비 {(close.iloc[-1] / close.max() - 1) * 100:.1f}% (30% 기준 주요 정점으로는 아직 미확정)."
    )
    md.append("")

    # 오늘
    md.append(f"## 오늘({TODAY})")
    md.append("")
    on_today = [s.id for s in evaluate(pd.Timestamp(TODAY), series, close) if s.on]
    none_today = [s.id for s in evaluate(pd.Timestamp(TODAY), series, close) if s.on is None]
    md.append(
        f"- 원 규칙: 켜짐 {int(today['n_on'])} / 평가 가능 {int(today['n_avail'])} (켜진 것: {', '.join(on_today)}; "
        f"데이터 없음: {', '.join(none_today) or '없음'}). 묶음 **{today['bucket']}**."
    )
    md.append(
        f"- 대체 규칙: 켜짐 {int(today['n_on_alt'])} / {int(today['n_avail_alt'])} "
        f"(BAA10Y={today['crisis_hy_alt_value']:.2f}%p, 임계 {BAA_THRESHOLD}). 묶음 **{today['bucket_alt']}**."
    )
    g = comp[comp["bucket_alt"] == today["bucket_alt"]]
    q1, q2 = quantile_row(g["months_to_peak_24m"]), quantile_row(g["dd_after_peak"])
    md.append(
        f"- 같은 묶음({today['bucket_alt']})의 과거 {len(g)}개월(에피소드 {len(episodes(hist['bucket_alt'] == today['bucket_alt']))}개): "
        f"2년 내 최고가까지 {q1['p10']:.0f}~{q1['p90']:.0f}개월(중간 {q1['p50']:.0f}), "
        f"그 최고가 이후 낙폭 {q2['p10'] * 100:.0f}%~{q2['p90'] * 100:.0f}%(중간 {q2['p50'] * 100:.0f}%)."
    )
    md.append("")
    (OUT / "maturity_buckets.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))

    # 그림
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.family"] = ["Apple SD Gothic Neo", "AppleGothic", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(13, 5.5))
    ax.step(
        hist.index,
        hist["n_on_alt"],
        where="post",
        color="#c0392b",
        lw=1.4,
        label="켜진 개수 (대체 규칙: 위기=BAA10Y≥3)",
    )
    ax.step(
        hist.index,
        hist["n_on"],
        where="post",
        color="#7f8c8d",
        lw=0.9,
        ls="--",
        label="켜진 개수 (원 규칙)",
    )
    ax.set_ylabel("켜진 신호 개수")
    ax.set_ylim(0, 9)
    ax.set_yticks(range(0, 10))
    ax2 = ax.twinx()
    sox = close.loc["1994":END]
    ax2.plot(sox.index, sox, color="#2c3e50", lw=0.8, alpha=0.7, label="SOX (로그)")
    ax2.set_yscale("log")
    ax2.set_ylabel("SOX")
    for t in tops:
        ax2.axvline(t, color="#2c3e50", lw=0.6, alpha=0.5)
    for a, b in SHADE:
        ax.axvspan(pd.Timestamp(a), pd.Timestamp(b), color="#f1c40f", alpha=0.18)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8)
    ax.set_title(
        "체크리스트 켜진 개수(월말)와 SOX. 세로선=주요 정점(이후 30%+ 하락). 음영=1999–2000, 2007–2008, 2021–2022"
    )
    fig.tight_layout()
    fig.savefig(OUT / "maturity_timeseries.png", dpi=130)
    print("saved", OUT / "maturity_timeseries.png")


if __name__ == "__main__":
    main()

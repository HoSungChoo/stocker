"""계산 2 — AI 설비투자 대비 매출 (닷컴의 '광케이블 이용률'에 해당하는 과잉 건설 지표 후보 평가).

실행: uv run --with matplotlib python analysis/capex.py   (DB 불필요. analysis/fetch_data.py를 먼저 실행)

산출물(analysis/out/):
- capex_quarterly.csv : 빅테크 4사 분기 설비투자·매출(백만$), 엔비디아 데이터센터 매출, 비율들
- capex_1990s.csv     : 1990~2003 통신장비 투자/GDP(연간), 정보처리장비 투자/GDP(분기)
- capex_tables.md     : 보고서용 표
- capex_ratio.png     : 두 사이클 비교 그림

분기화 방법 (EDGAR companyfacts의 현금흐름 항목은 회계연도 누적값이다):
- 기간 길이가 100일 이하인 행은 그대로 분기값.
- 그보다 긴 행(반기·3분기·연간 누적)은 같은 시작일(회계연도 시작)이고 끝이 3개월 전인 행을 빼서 분기값으로 만든다.
- 같은 (시작, 끝) 기간이 여러 공시에 중복되면 가장 나중에 제출된 값을 쓴다. 8-K는 제외.
룩어헤드: 없음(공시된 과거 값). 단, 분기 값은 분기 종료 후 약 1개월 뒤에야 공시된다(지연).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "analysis" / "data"
OUT = ROOT / "analysis" / "out"

CAPEX_TAGS = {
    "MSFT": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "GOOGL": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "AMZN": ["PaymentsToAcquireProductiveAssets", "PaymentsToAcquirePropertyPlantAndEquipment"],
    "META": ["PaymentsToAcquirePropertyPlantAndEquipment"],
}
REV_TAGS = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]


def _rows(facts: dict, tag: str) -> pd.DataFrame:
    u = facts.get(tag, {}).get("units", {}).get("USD", [])
    df = pd.DataFrame(u)
    if df.empty:
        return df
    df = df[df["form"].isin(["10-K", "10-Q", "10-K/A", "10-Q/A"])].copy()
    df["start"] = pd.to_datetime(df["start"])
    df["end"] = pd.to_datetime(df["end"])
    df["filed"] = pd.to_datetime(df["filed"])
    df = df.sort_values("filed").drop_duplicates(["start", "end"], keep="last")
    df["days"] = (df["end"] - df["start"]).dt.days
    return df[["start", "end", "val", "days", "filed"]]


def quarterize(df: pd.DataFrame) -> pd.Series:
    """누적값 → 분기값. index=분기 말, 단위 백만$."""
    out: dict[pd.Timestamp, float] = {}
    by_start = {s: g.set_index("end")["val"] for s, g in df.groupby("start")}
    for _, r in df.iterrows():
        if r["days"] <= 100:
            out[r["end"]] = r["val"]
            continue
        prev_target = r["end"] - pd.DateOffset(months=3)
        ser = by_start[r["start"]]
        cand = ser[(abs((ser.index - prev_target).days) <= 10)]
        if len(cand) == 1 and r["end"] not in out:
            out[r["end"]] = r["val"] - float(cand.iloc[0])
    s = pd.Series(out).sort_index() / 1e6
    # 분기 말을 달력 분기 말로 정규화 (예: 2025-09-28 → 2025-09-30)
    s.index = s.index.to_period("Q").to_timestamp("Q")
    return s[~s.index.duplicated(keep="last")]


def load_company(tic: str) -> tuple[pd.Series, pd.Series]:
    facts = json.loads((DATA / "edgar" / f"{tic}.json").read_text())["facts"]["us-gaap"]
    capex = pd.concat(
        [quarterize(_rows(facts, t)) for t in CAPEX_TAGS[tic] if not _rows(facts, t).empty]
    )
    capex = capex[~capex.index.duplicated(keep="first")].sort_index()
    rev = pd.concat([quarterize(_rows(facts, t)) for t in REV_TAGS if not _rows(facts, t).empty])
    rev = rev[~rev.index.duplicated(keep="first")].sort_index()
    return capex.rename(f"capex_{tic}"), rev.rename(f"rev_{tic}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    parts = []
    for tic in CAPEX_TAGS:
        c, r = load_company(tic)
        parts += [c, r]
    q = pd.concat(parts, axis=1, sort=True).loc["2015-01-01":]
    q["capex_big4"] = q[[f"capex_{t}" for t in CAPEX_TAGS]].sum(axis=1, min_count=4)
    q["rev_big4"] = q[[f"rev_{t}" for t in CAPEX_TAGS]].sum(axis=1, min_count=4)

    nv = pd.read_csv(DATA / "nvda_datacenter.csv", parse_dates=["quarter_end", "release_date"])
    # 엔비디아 분기(1·4·7·10월 말)는 달력 분기(3·6·9·12월 말)보다 약 1개월 늦게 끝난다 → 가장 가까운 달력 분기에 맞춘다
    nv["cal_q"] = (
        (nv["quarter_end"] - pd.DateOffset(months=1)).dt.to_period("Q").dt.to_timestamp("Q")
    )
    nv = nv.set_index("cal_q")
    q["nvda_dc_rev"] = nv["datacenter_revenue_musd"]
    q["nvda_total_rev"] = nv["total_revenue_musd"]

    gdp = pd.read_csv(DATA / "fred_GDP.csv", index_col=0, parse_dates=True)["value"]
    gdp.index = gdp.index.to_period("Q").to_timestamp("Q")
    q["gdp_q_musd"] = gdp * 1000 / 4  # 10억$ SAAR → 분기 백만$

    q["capex_to_rev"] = q["capex_big4"] / q["rev_big4"]
    q["capex_to_nvda_dc"] = q["capex_big4"] / q["nvda_dc_rev"]
    q["capex_to_gdp"] = q["capex_big4"] / q["gdp_q_musd"]
    q["capex_yoy"] = q["capex_big4"].pct_change(4)
    q["nvda_dc_yoy"] = q["nvda_dc_rev"].pct_change(4)
    q.round(4).to_csv(OUT / "capex_quarterly.csv")

    # 1990년대 비교군
    comm = pd.read_csv(DATA / "fred_C275RC1A027NBEA.csv", index_col=0, parse_dates=True)["value"]
    gdp_a = gdp.resample("YE").mean()
    gdp_a.index = gdp_a.index.year
    comm.index = comm.index.year
    ipe = pd.read_csv(DATA / "fred_Y034RC1Q027SBEA.csv", index_col=0, parse_dates=True)["value"]
    ipe.index = ipe.index.to_period("Q").to_timestamp("Q")
    old_a = pd.DataFrame({"comm_eq_bil": comm, "gdp_bil": gdp_a}).loc[1990:2005]
    old_a["comm_eq_to_gdp"] = old_a["comm_eq_bil"] / old_a["gdp_bil"]
    old_q = pd.DataFrame({"info_eq_bil_saar": ipe, "gdp_bil_saar": gdp}).loc[
        "1990-01-01":"2005-12-31"
    ]
    old_q["info_eq_to_gdp"] = old_q["info_eq_bil_saar"] / old_q["gdp_bil_saar"]
    old_a.round(4).to_csv(OUT / "capex_1990s_annual.csv")
    old_q.round(4).to_csv(OUT / "capex_1990s_quarterly.csv")

    md = ["# 계산 2 — AI 설비투자 대비 매출 (자동 생성: analysis/capex.py)", ""]
    md.append("## 빅테크 4사(MSFT·GOOGL·AMZN·META) 분기 설비투자와 비율 (백만$)")
    md.append("")
    md.append(
        "| 분기 말 | 설비투자 합 | 전년비 | 4사 매출 합 | 설비투자/매출 | 엔비디아 DC 매출 | 설비투자/DC매출 | 설비투자/GDP |"
    )
    md.append("|---|---|---|---|---|---|---|---|")
    for d, r in q.loc["2022-03-31":].iterrows():
        f = lambda x, p="{:,.0f}": "–" if pd.isna(x) else p.format(x)  # noqa: E731
        md.append(
            f"| {d:%Y-%m} | {f(r['capex_big4'])} | {f(r['capex_yoy'] * 100, '{:+.0f}%')} | {f(r['rev_big4'])} | "
            f"{f(r['capex_to_rev'] * 100, '{:.1f}%')} | {f(r['nvda_dc_rev'])} | {f(r['capex_to_nvda_dc'], '{:.2f}')} | "
            f"{f(r['capex_to_gdp'] * 100, '{:.2f}%')} |"
        )
    md.append("")
    md.append("## 연간 요약 (설비투자/매출, 설비투자/GDP)")
    md.append("")
    ann = q.resample("YE").agg(
        {"capex_big4": "sum", "rev_big4": "sum", "gdp_q_musd": "sum", "nvda_dc_rev": "sum"}
    )
    ann = ann[ann.index.year >= 2015]
    md.append("| 연도 | 설비투자 합 | 설비투자/매출 | 설비투자/GDP | 분기 수 |")
    md.append("|---|---|---|---|---|")
    for d, r in ann.iterrows():
        n = q.loc[str(d.year), "capex_big4"].notna().sum()
        md.append(
            f"| {d.year} | {r['capex_big4']:,.0f} | {r['capex_big4'] / r['rev_big4'] * 100:.1f}% | {r['capex_big4'] / r['gdp_q_musd'] * 100:.2f}% | {n} |"
        )
    md.append("")
    md.append("## 1990년대 비교군: 통신장비 투자/GDP (연간, BEA 명목, FRED C275RC1A027NBEA)")
    md.append("")
    md.append("| 연도 | 통신장비 투자(10억$) | GDP(10억$) | 비율 |")
    md.append("|---|---|---|---|")
    for y, r in old_a.loc[1994:2003].iterrows():
        md.append(
            f"| {y} | {r['comm_eq_bil']:.1f} | {r['gdp_bil']:,.0f} | {r['comm_eq_to_gdp'] * 100:.2f}% |"
        )
    md.append("")
    peak_y = old_a["comm_eq_to_gdp"].idxmax()
    md.append(
        f"- 통신장비 투자/GDP 정점: {peak_y}년 {old_a.loc[peak_y, 'comm_eq_to_gdp'] * 100:.2f}% (1994년 {old_a.loc[1994, 'comm_eq_to_gdp'] * 100:.2f}% → 2002년 {old_a.loc[2002, 'comm_eq_to_gdp'] * 100:.2f}%)."
    )
    pk = old_q["info_eq_to_gdp"].loc["1995":"2003"].idxmax()
    md.append(
        f"- 정보처리장비 투자/GDP(분기, Y034RC1Q027SBEA) 정점: {pk:%Y-%m} {old_q.loc[pk, 'info_eq_to_gdp'] * 100:.2f}%."
    )
    last = q["capex_to_gdp"].dropna()
    md.append(
        f"- 빅테크 4사 설비투자/GDP 최신: {last.index[-1]:%Y-%m} {last.iloc[-1] * 100:.2f}% (2019년 평균 {q.loc['2019', 'capex_to_gdp'].mean() * 100:.2f}%)."
    )
    md.append("")
    (OUT / "capex_tables.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.family"] = ["Apple SD Gothic Neo", "AppleGothic", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
    ax = axes[0]
    ax.plot(
        old_a.index,
        old_a["comm_eq_to_gdp"] * 100,
        marker="o",
        color="#2c3e50",
        label="통신장비 투자/GDP (연간)",
    )
    ax.plot(
        old_q.index.year + old_q.index.month / 12,
        old_q["info_eq_to_gdp"] * 100,
        color="#7f8c8d",
        lw=1,
        label="정보처리장비 투자/GDP (분기)",
    )
    ax.axvline(2000.2, color="#c0392b", lw=0.8, ls="--")
    ax.set_title("1990~2005: 통신·정보장비 투자 / GDP (%) — 점선=SOX 정점 2000-03")
    ax.set_xlim(1990, 2005)
    ax.legend(fontsize=8)
    ax = axes[1]
    ax.plot(q.index, q["capex_to_gdp"] * 100, color="#2c3e50", label="빅테크 4사 설비투자/GDP")
    ax.plot(q.index, q["capex_to_rev"] * 100, color="#c0392b", label="설비투자/4사 매출")
    ax.set_ylabel("%")
    ax2 = ax.twinx()
    ax2.plot(
        q.index,
        q["capex_to_nvda_dc"],
        color="#27ae60",
        ls="--",
        label="설비투자/엔비디아 DC매출 (배, 우축)",
    )
    ax2.set_ylim(0, 12)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=8, loc="upper left")
    ax.set_title("2015~2026: 빅테크 설비투자 비율")
    fig.tight_layout()
    fig.savefig(OUT / "capex_ratio.png", dpi=130)
    print("saved", OUT / "capex_ratio.png")


if __name__ == "__main__":
    main()

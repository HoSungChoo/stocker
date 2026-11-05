"""계산 3 — 신용 단계 지표. BAA10Y(FRED, 1986~)와 CCC 스프레드(DB, 2023-11~).

실행: uv run python analysis/credit.py   (DB 필요: BAMLH0A3HYC·BAMLH0A0HYM2)
산출물: analysis/out/credit_tables.md
룩어헤드: 없음(각 날짜의 값과 그 날짜까지의 역사 안 백분위, 그리고 전체 역사 안 백분위를 둘 다 적는다).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from stocker.db import read_macro  # noqa: E402

DATA = ROOT / "analysis" / "data"
OUT = ROOT / "analysis" / "out"
DATES = [
    "1998-10-16",
    "2000-03-10",
    "2000-03-31",
    "2002-10-09",
    "2007-07-17",
    "2007-07-31",
    "2008-11-28",
    "2008-12-04",
    "2020-03-23",
    "2020-03-31",
    "2026-11-03",
]


def pct(hist: pd.Series, v: float) -> float:
    return float((hist < v).mean() * 100)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    baa = pd.read_csv(DATA / "fred_BAA10Y.csv", index_col=0, parse_dates=True)["value"].dropna()
    md = ["# 계산 3 — 신용 단계 (자동 생성: analysis/credit.py)", ""]
    md.append(
        f"## BAA10Y (무디스 Baa 회사채 수익률 − 10년물 국채, %p). 일간 {baa.index[0].date()}~{baa.index[-1].date()}, n={len(baa)}"
    )
    md.append("")
    md.append("| 날짜 | 값(%p) | 그날까지 역사 안 백분위 | 전체 역사 안 백분위 | 비고 |")
    md.append("|---|---|---|---|---|")
    notes = {
        "1998-10-16": "LTCM 위기 최고",
        "2000-03-10": "SOX 정점",
        "2002-10-09": "닷컴 붕괴 신용 최고",
        "2007-07-17": "SOX 정점",
        "2008-12-04": "금융위기 최고",
        "2020-03-23": "코로나 최고",
        "2026-11-03": "최신",
    }
    for d in DATES:
        upto = baa.loc[:d]
        v = float(upto.iloc[-1])
        md.append(
            f"| {upto.index[-1].date()} | {v:.2f} | {pct(upto, v):.0f}% | {pct(baa, v):.0f}% | {notes.get(d, '')} |"
        )
    md.append("")
    md.append(
        f"- 전체 역사에서 3.0%p 이상이었던 날 비율 {(baa >= 3.0).mean() * 100:.1f}%, 3.5%p 이상 {(baa >= 3.5).mean() * 100:.1f}%, 2.5%p 이상 {(baa >= 2.5).mean() * 100:.1f}%."
    )
    for a, b in [
        ("2000-01-01", "2003-12-31"),
        ("2007-01-01", "2009-12-31"),
        ("2011-06-01", "2012-06-30"),
        ("2015-06-01", "2016-06-30"),
        ("2020-01-01", "2020-12-31"),
        ("2022-01-01", "2023-12-31"),
        ("2025-01-01", "2026-12-31"),
    ]:
        w = baa.loc[a:b]
        md.append(f"- {a[:4]}~{b[:4]} 구간 최고 {w.max():.2f}%p ({w.idxmax().date()})")
    md.append("")

    ccc = read_macro("BAMLH0A3HYC").dropna()
    hy = read_macro("BAMLH0A0HYM2").dropna()
    md.append(
        f"## CCC 이하 스프레드 (BAMLH0A3HYC, %p). DB 보유 {ccc.index[0].date()}~{ccc.index[-1].date()}, n={len(ccc)} — 역사 3년뿐"
    )
    md.append("")
    v = float(ccc.iloc[-1])
    md.append(
        f"- 최신 {ccc.index[-1].date()}: {v:.2f}%p. 보유 역사 안 백분위 {pct(ccc, v):.0f}% (최저 {ccc.min():.2f}, 중간 {ccc.median():.2f}, 최고 {ccc.max():.2f} {ccc.idxmax().date()})."
    )
    hv = float(hy.iloc[-1])
    md.append(
        f"- 하이일드 전체(BAMLH0A0HYM2) 최신 {hy.index[-1].date()}: {hv:.2f}%p. 보유 역사 안 백분위 {pct(hy, hv):.0f}% (최저 {hy.min():.2f}, 최고 {hy.max():.2f})."
    )
    ratio = (ccc / hy).dropna()
    md.append(
        f"- CCC/하이일드 비율: 최신 {ratio.iloc[-1]:.2f}배 (보유 역사 중간 {ratio.median():.2f}, 최고 {ratio.max():.2f}). 비율이 높을수록 '가장 약한 기업'만 골라서 벌 받는 상태."
    )
    md.append(
        f"- BAA10Y 최신 {baa.iloc[-1]:.2f}%p는 전체 역사 하위 {pct(baa, float(baa.iloc[-1])):.0f}%: 투자등급 시장은 위기 신호와 거리가 멀다. CCC 12.97%p는 체크리스트 임계(12.0)를 넘지만 2023~2026의 3년 안에서만 비교할 수 있다. 2001·2008과 비교하려면 ICE BofA CCC 지수 전체 역사(FRED에는 1996-12~ 있음: BAMLH0A3HYC의 FRED 원본 시작일은 1996-12-31)를 받아야 한다. DB에 2023-11 이후만 있는 것은 수집 범위 설정 때문이다(이 보고서에서는 DB를 바꾸지 않음)."
    )
    md.append("")
    md.append("## 하이일드 발행 중 기술·AI 비중")
    md.append("")
    md.append(
        "- 공개 무료 통계를 찾지 못했다(SIFMA·LSEG·PitchBook 자료는 유료 또는 비정형). **수작업 필요**: 분기별 보도(예: Bloomberg/FT 'AI-related bond issuance') 수치를 표로 옮겨야 한다."
    )
    (OUT / "credit_tables.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()

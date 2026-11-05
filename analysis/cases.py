"""계산 4 — 1997형 vs 1999형. explain_db로 두 끝 날짜의 '그때/지금' 근거, 체크리스트 3열 비교, 이후 SOX 경로.

실행: uv run python analysis/cases.py   (DB 필요)
산출물: analysis/out/cases_tables.md, analysis/out/cases_explain_{1997,1999}.json
룩어헤드: explain의 then/now 값은 각 날짜 이하 데이터만 쓴다. '이후 경로'는 미래 종가(표시용).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from stocker.checklist import RULES, evaluate_db  # noqa: E402
from stocker.db import read_prices  # noqa: E402
from stocker.similarity.config import SimilarityConfig  # noqa: E402
from stocker.similarity.explain import explain_db  # noqa: E402

OUT = ROOT / "analysis" / "out"
AS_OF = "2026-11-04"
CASES = {"1997형": "1997-03-31", "1999형": "1999-12-31"}


def fmt(x, nd=2):
    return "–" if x is None or (isinstance(x, float) and pd.isna(x)) else f"{x:.{nd}f}"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    close = read_prices("^SOX")["close"].dropna()
    cfg = SimilarityConfig()
    exps = {}
    for name, d in CASES.items():
        try:
            e = explain_db(AS_OF, d, cfg)
        except Exception as ex:  # 환경 우선 모드가 explain을 지원하지 않으면 모양 우선으로
            print("regime explain failed:", ex, "→ shape mode")
            e = explain_db(AS_OF, d, SimilarityConfig(mode="shape"))
        exps[name] = e
        (OUT / f"cases_explain_{d[:4]}.json").write_text(
            json.dumps(e.to_dict(), ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )

    md = ["# 계산 4 — 1997형 vs 1999형 (자동 생성: analysis/cases.py)", ""]
    md.append(
        f"기준일(지금) {AS_OF}. 과거 창은 각 끝 날짜로 끝나는 {cfg.window}거래일 창. explain 모드: {cfg.mode}."
    )
    md.append("")
    md.append("## 그때/지금 근거 (explain_db)")
    md.append("")
    md.append("| 항목 | 1997-03-31 그때 | 1999-12-31 그때 | 지금 |")
    md.append("|---|---|---|---|")
    e97, e99 = exps["1997형"], exps["1999형"]
    md.append(
        f"| 낙폭(고점 대비) | {fmt(e97.price.drawdown.then)} | {fmt(e99.price.drawdown.then)} | {fmt(e97.price.drawdown.now)} |"
    )
    md.append(
        f"| 고점 이후 거래일 | {fmt(e97.price.drawdown_days.then, 0)} | {fmt(e99.price.drawdown_days.then, 0)} | {fmt(e97.price.drawdown_days.now, 0)} |"
    )
    for m97, m99 in zip(e97.macro, e99.macro, strict=True):
        md.append(
            f"| {m97.label} ({m97.series}, {m97.unit}) | {fmt(m97.then)} (z {fmt(m97.z_then, 1)}) | {fmt(m99.then)} (z {fmt(m99.z_then, 1)}) | {fmt(m97.now)} (z {fmt(m97.z_now, 1)}) |"
        )
    for c97, c99 in zip(e97.cycle, e99.cycle, strict=True):
        md.append(f"| {c97.label} | {fmt(c97.then)} | {fmt(c99.then)} | {fmt(c97.now)} |")
    md.append(
        f"| 종합 점수(낮을수록 닮음) | {fmt(e97.match.combined, 3)} | {fmt(e99.match.combined, 3)} | |"
    )
    md.append("")

    md.append("## 체크리스트 3열 비교 (● 켜짐 ○ 꺼짐 – 데이터 없음, 괄호는 값)")
    md.append("")
    cols = {
        "1997-03-31": evaluate_db("1997-03-31"),
        "1999-12-31": evaluate_db("1999-12-31"),
        AS_OF: evaluate_db(AS_OF),
    }
    md.append("| 신호 | 1997-03-31 | 1999-12-31 | " + AS_OF + " |")
    md.append("|---|---|---|---|")
    diff = []
    for i, r in enumerate(RULES):
        cells = []
        for sigs in cols.values():
            s = sigs[i]
            mark = "–" if s.on is None else ("●" if s.on else "○")
            cells.append(f"{mark} ({fmt(s.value, 1)})" if s.value is not None else mark)
        md.append(f"| {r.label} (`{r.id}`) | " + " | ".join(cells) + " |")
        a, b = cols["1997-03-31"][i].on, cols["1999-12-31"][i].on
        if a is not None and b is not None and a != b:
            diff.append((r.label, r.id, a, b, cols[AS_OF][i].on))
    md.append("")
    md.append("## 두 사례를 가르는 신호 (1997-03과 1999-12에서 상태가 달랐던 것)")
    md.append("")
    for label, rid, a, b, now in diff:
        md.append(
            f"- {label} (`{rid}`): 1997 {'●' if a else '○'} / 1999 {'●' if b else '○'} / 지금 {'–' if now is None else ('●' if now else '○')}"
        )
    md.append("")
    md.append("## 이후 SOX 경로 (끝 날짜 종가 대비, 표시용·룩어헤드)")
    md.append("")
    md.append("| 끝 날짜 | +6개월 | +12개월 | +24개월 | 24개월 내 최고 | 24개월 내 최저 |")
    md.append("|---|---|---|---|---|---|")
    for d in list(CASES.values()):
        d0 = pd.Timestamp(d)
        base = close.loc[:d0].iloc[-1]
        vals = []
        for m in (6, 12, 24):
            vals.append(close.loc[: d0 + pd.DateOffset(months=m)].iloc[-1] / base - 1)
        w = close.loc[d0 : d0 + pd.DateOffset(months=24)]
        md.append(
            f"| {d} | "
            + " | ".join(f"{v * 100:+.0f}%" for v in vals)
            + f" | {(w.max() / base - 1) * 100:+.0f}% ({w.idxmax():%Y-%m}) | {(w.min() / base - 1) * 100:+.0f}% ({w.idxmin():%Y-%m}) |"
        )
    md.append("")
    (OUT / "cases_tables.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()

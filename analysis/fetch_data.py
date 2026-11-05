"""외부 자료 내려받기 (analysis/data/). 제품 코드·DB를 건드리지 않는다.

실행: uv run python analysis/fetch_data.py
- FRED: BAA10Y(무디스 Baa − 10년물), 통신장비 투자(연간, 명목), 정보처리장비 투자(분기), GDP(분기)
- SEC EDGAR companyfacts: MSFT·GOOGL·AMZN·META 설비투자(PaymentsToAcquirePropertyPlantAndEquipment)와 매출
키는 .env의 FRED_API_KEY만 쓴다(stocker.config). EDGAR는 키 불필요, User-Agent 필요.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import requests
from fredapi import Fred

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stocker.config import settings  # noqa: E402

DATA = Path(__file__).resolve().parent / "data"
EDGAR = DATA / "edgar"

FRED_SERIES = {
    "BAA10Y": "무디스 Baa 회사채 − 10년물 국채 (일간, %p, 1986~)",
    "C275RC1A027NBEA": "민간 고정투자: 통신장비 (연간, 명목, 10억$, 1929~)",
    "Y034RC1Q027SBEA": "민간 고정투자: 정보처리장비 (분기, 명목 SAAR, 10억$, 1947~)",
    "A679RC1Q027SBEA": "민간 고정투자: 정보처리장비+소프트웨어 (분기, 명목 SAAR, 10억$, 1947~)",
    "GDP": "명목 GDP (분기, SAAR, 10억$)",
}

# CIK: SEC 공식 (https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany)
CIKS = {"MSFT": 789019, "GOOGL": 1652044, "AMZN": 1018724, "META": 1326801}
UA = "stocker-research hosung (ypostplan@gmail.com)"


def fetch_fred() -> list[str]:
    fred = Fred(api_key=settings.fred_key)
    lines = []
    for sid, desc in FRED_SERIES.items():
        s = fred.get_series(sid)
        s.index.name = "date"
        s.rename("value").to_csv(DATA / f"fred_{sid}.csv")
        lines.append(f"- `fred_{sid}.csv` — {desc}. https://fred.stlouisfed.org/series/{sid}")
        print(sid, len(s), s.index.min().date(), s.index.max().date())
    return lines


def fetch_edgar() -> list[str]:
    lines = []
    for tic, cik in CIKS.items():
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"}, timeout=60)
        r.raise_for_status()
        (EDGAR / f"{tic}.json").write_text(json.dumps(r.json()), encoding="utf-8")
        lines.append(f"- `edgar/{tic}.json` — SEC XBRL companyfacts (CIK {cik}). {url}")
        print(tic, "ok", len(r.content), "bytes")
    return lines


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    EDGAR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    lines = [
        f"# 외부 자료 출처 (내려받은 날짜: {today})",
        "",
        "## FRED",
        *fetch_fred(),
        "",
        "## SEC EDGAR",
        *fetch_edgar(),
    ]
    lines += [
        "",
        "## 수작업 표",
        "- `nvda_datacenter.csv` — 엔비디아 분기 실적 보도자료(nvidianews.nvidia.com)에서 옮겨 적음. "
        "URL은 파일 안 `source` 열. 데이터센터 매출은 보도자료 본문의 '십억 달러 소수 1~2자리' 표기를 백만 달러로 환산한 값.",
    ]
    (DATA / "SOURCES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

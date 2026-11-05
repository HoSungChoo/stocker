"""제이 리터(플로리다대) 공개 IPO 통계 `IPOALL.xlsx`를 seed/indicators/ritter_ipo.csv로 변환.

실행 (openpyxl은 런타임 의존성이 아니므로 --with로 임시 설치):

    uv run --with openpyxl python scripts/parse_ritter.py
    uv run --with openpyxl python scripts/parse_ritter.py --xlsx 받은파일.xlsx --source-url <URL>

동작:
- https://site.warrington.ufl.edu/ritter/ipo-data/ 페이지에서 `IPOALL.xlsx` 링크를 찾아 내려받는다.
- 시트 `IPOALL`은 월별 표다. 열: 0=월, 1=연도(두 자리), 2=그달 IPO 평균 첫날 수익률(%),
  3=총 건수, 4=순 건수(SPAC·소형주 등 제외), 5=공모가 범위 중간값 초과 비율(%).
  연도는 60 이상이면 1900년대, 미만이면 2000년대로 푼다. 값이 '.'(그달 IPO 없음)이면 그 달을 뺀다.
  하단 설명 행(월 열이 숫자가 아님)은 무시한다.
- 저장 열: `date`(월말), `value`(첫날 평균 수익률 %), `freq`(`monthly` 또는 `annual`).
  기본으로 1980-01 이후만 저장한다(`--start-year`).
- 월간 표가 없고 연간 표만 있을 때(예: IPOs-Underpricing.pdf의 연간 표를 손으로 옮긴 경우)는
  `freq=annual`, `date`는 그 해 아무 날짜(예: YYYY-12-31)로 CSV를 만든다.
  `stocker.ingest.indicators`가 이를 그 해 각 월말로 복제하고 `source`에 `ritter_annual`을 적는다.
- 첫 줄에 `# source: ..., fetched: YYYY-MM-DD` 주석. 읽을 때는 `comment="#"`.

주의: 리터는 해마다 1~2월에 전년도를 갱신한다. 파일은 직전 12월까지만 있다.

자동 다운로드가 막힌 경우(수동 절차):
1. 브라우저로 https://site.warrington.ufl.edu/ritter/ipo-data/ 를 열고
   "IPOALL.xlsx" 링크의 파일을 받는다.
2. `--xlsx <경로> --source-url <받은 URL>` 로 이 스크립트를 다시 실행한다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import tempfile
import urllib.request
from collections.abc import Iterable, Sequence
from pathlib import Path

import pandas as pd

PAGE_URL = "https://site.warrington.ufl.edu/ritter/ipo-data/"
DEFAULT_XLSX_URL = "https://site.warrington.ufl.edu/ritter/files/IPOALL.xlsx"
OUT = Path(__file__).resolve().parents[1] / "seed" / "indicators" / "ritter_ipo.csv"
UA = {"User-Agent": "Mozilla/5.0"}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def find_xlsx_url() -> str:
    html = _get(PAGE_URL).decode("utf-8", errors="replace")
    m = re.search(r'href="([^"]*IPOALL\.xlsx)"', html, flags=re.IGNORECASE)
    return m.group(1) if m else DEFAULT_XLSX_URL


def _int(v) -> int | None:
    if isinstance(v, bool) or not isinstance(v, int | float):
        return None
    return int(v) if v == int(v) else None


def _year(yy: int) -> int:
    return yy if yy >= 1000 else (1900 + yy if yy >= 60 else 2000 + yy)


def parse_ipoall(rows: Iterable[Sequence], start_year: int = 1980) -> pd.DataFrame:
    """IPOALL 시트의 행들 → DataFrame(date 월말, value %, freq='monthly')."""
    out = []
    for row in rows:
        month, yy = _int(row[0]), _int(row[1]) if len(row) > 2 else None
        if month is None or yy is None or not 1 <= month <= 12:
            continue
        val = row[2]
        if isinstance(val, bool) or not isinstance(val, int | float):
            continue  # '.' 등: 그달 IPO 없음
        year = _year(yy)
        if year < start_year:
            continue
        date = pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)
        out.append((date, float(val)))
    df = pd.DataFrame(out, columns=["date", "value"]).sort_values("date")
    if df.empty:
        raise RuntimeError("IPOALL 시트에서 월별 첫날 수익률을 읽지 못했다")
    df["freq"] = "monthly"
    return df.reset_index(drop=True)


def read_sheet_rows(xlsx_path: Path) -> list[tuple]:
    import openpyxl

    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
    return list(wb.active.iter_rows(values_only=True))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xlsx", type=Path, help="이미 받은 xlsx 경로(없으면 자동 다운로드)")
    ap.add_argument("--source-url", help="--xlsx 사용 시 CSV 주석에 적을 출처 URL")
    ap.add_argument("--start-year", type=int, default=1980)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    if args.xlsx:
        df = parse_ipoall(read_sheet_rows(args.xlsx), args.start_year)
        url = args.source_url or DEFAULT_XLSX_URL
    else:
        url = find_xlsx_url()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "IPOALL.xlsx"
            path.write_bytes(_get(url))
            df = parse_ipoall(read_sheet_rows(path), args.start_year)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fetched = dt.date.today().isoformat()
    with args.out.open("w", encoding="utf-8") as f:
        f.write(
            f"# source: {url} (Jay Ritter, sheet IPOALL, average first-day return %, "
            f"monthly), fetched: {fetched}\n"
        )
        df.assign(date=df["date"].dt.strftime("%Y-%m-%d")).to_csv(
            f, index=False, float_format="%.4f"
        )
    print(
        f"{len(df)}행, {df['date'].min():%Y-%m} ~ {df['date'].max():%Y-%m} -> {args.out}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()

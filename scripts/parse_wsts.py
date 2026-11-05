"""WSTS "Historical Billings Report"(공개 Excel)를 seed/wsts_sales_world.csv로 변환.

실행 (openpyxl은 런타임 의존성이 아니므로 --with로 임시 설치):

    uv run --with openpyxl python scripts/parse_wsts.py
    uv run --with openpyxl python scripts/parse_wsts.py --xlsx 받은파일.xlsx  # 수동 다운로드 파일

동작:
- https://www.wsts.org/67/Historical-Billings-Report 페이지에서 .xlsx 링크를 찾아 내려받는다.
  파일명에 발표 월이 들어 있어(예: ...-Aug_2026.xlsx) URL이 매달 바뀌므로 페이지에서 링크를 찾는다.
- 시트 `3MMA`(3-month moving average)의 `Worldwide` 행을 읽는다. 원본 단위는 천 달러(1000 US$)이고
  3MMA 값은 해당 월을 마지막으로 하는 직전 3개월 평균이다. 백만 달러로 환산해 월말 날짜로 저장한다.
- 아직 발표되지 않은 달(값이 0 또는 빈칸)은 버린다.
- CSV 첫 줄에 `# source: ..., fetched: YYYY-MM-DD` 주석을 남긴다. 읽을 때는 `comment="#"`.

자동 다운로드가 막히거나 페이지 구조가 바뀐 경우(수동 절차):
1. 브라우저로 https://www.wsts.org/67/Historical-Billings-Report 를 연다 (로그인 불필요).
2. "Historical Billings Report (XLSX)" 링크의 파일을 받는다.
3. `--xlsx <경로> --source-url <받은 URL>` 로 이 스크립트를 다시 실행한다.

참고: WSTS 페이지는 무단 복제를 금지한다고 적고 있으나 이 보고서는 무료 공개 자료다.
공개 저장소에 올릴 때는 출처를 CSV 주석에 남기고 재배포 조건을 확인할 것.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

import pandas as pd

PAGE_URL = "https://www.wsts.org/67/Historical-Billings-Report"
SHEET = "3MMA"
REGION = "Worldwide"
MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
OUT = Path(__file__).resolve().parents[1] / "seed" / "wsts_sales_world.csv"
UA = {"User-Agent": "Mozilla/5.0"}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def find_xlsx_url() -> str:
    html = _get(PAGE_URL).decode("utf-8", errors="replace")
    links = re.findall(r'href="([^"]+\.xlsx)"', html, flags=re.IGNORECASE)
    if not links:
        raise RuntimeError(
            f"{PAGE_URL} 에서 .xlsx 링크를 찾지 못했다. 모듈 docstring의 수동 절차를 따를 것."
        )
    return links[0]


def parse(xlsx_path: Path) -> pd.DataFrame:
    import openpyxl

    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
    ws = wb[SHEET]
    rows = []
    year = None
    for row in ws.iter_rows(values_only=True):
        label = row[0]
        if isinstance(label, int | float) and 1980 < label < 2100:
            year = int(label)
        elif label == REGION and year is not None:
            for m, val in enumerate(row[1:13], start=1):
                if val is None or val == 0:
                    continue  # 미발표 월
                date = pd.Timestamp(year=year, month=m, day=1) + pd.offsets.MonthEnd(0)
                rows.append((date, float(val) / 1000.0))  # 천 달러 → 백만 달러
    df = pd.DataFrame(rows, columns=["date", "value"]).sort_values("date")
    if df.empty:
        raise RuntimeError(f"시트 {SHEET}의 {REGION} 행에서 값을 읽지 못했다")
    return df.reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xlsx", type=Path, help="이미 받은 xlsx 경로(없으면 자동 다운로드)")
    ap.add_argument("--source-url", help="--xlsx 사용 시 CSV 주석에 적을 출처 URL")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    if args.xlsx:
        path, url = args.xlsx, args.source_url or PAGE_URL
    else:
        url = find_xlsx_url()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / Path(url).name
            path.write_bytes(_get(url))
            df = parse(path)
    if args.xlsx:
        df = parse(path)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fetched = dt.date.today().isoformat()
    with args.out.open("w", encoding="utf-8") as f:
        f.write(
            f"# source: {url} (sheet {SHEET}, {REGION}, 3-month moving average, USD millions), "
            f"fetched: {fetched}\n"
        )
        df.assign(date=df["date"].dt.strftime("%Y-%m-%d")).to_csv(
            f, index=False, float_format="%.6f"
        )
    print(
        f"{len(df)}행, {df['date'].min():%Y-%m} ~ {df['date'].max():%Y-%m} -> {args.out}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()

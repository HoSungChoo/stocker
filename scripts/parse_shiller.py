"""로버트 실러의 공개 엑셀(ie_data.xls)을 seed/indicators/shiller.csv로 변환.

실행 (xlrd는 런타임 의존성이 아니므로 --with로 임시 설치; .xls는 openpyxl로 못 읽는다):

    uv run --with xlrd python scripts/parse_shiller.py
    uv run --with xlrd python scripts/parse_shiller.py --xls 받은파일.xls --source-url <URL>

동작:
- https://shillerdata.com/ 페이지에서 `ie_data.xls` 링크를 찾아 내려받는다. 링크가 파일 해시와
  버전 쿼리(`?ver=...`)를 포함해 바뀔 수 있으므로 페이지에서 매번 찾는다.
  (옛 주소 http://www.econ.yale.edu/~shiller/data/ie_data.xls 는 2023-09에서 멈춘 사본이라
  최신 데이터가 필요하면 쓰지 않는다. 페이지에서 링크를 못 찾을 때만 마지막 수단으로 쓰고 경고한다.)
- 시트 `Data`를 읽는다. 열: 0=Date(1871.01 형식; 10월은 1871.1), 7=Real Price, 10=Real Earnings,
  12=CAPE(P/E10). 월은 소수부×100을 반올림해 구한다.
- 저장 열: `date`(월말), `cape`, `ep`.
  - `ep` = 후행 12개월 실질 이익 / 실질 주가(소수; 0.04 = 4%). 같은 CPI로 디플레이트하므로
    명목 E/P와 같다. 이익은 S&P가 분기 실적을 발표한 뒤에야 채워지므로 최근 3~4개월은 `ep`가 비고,
    그 행은 `ep`만 생략한다(CAPE는 10년 평균이라 최신월까지 있다). 값을 채워 넣지 않는다.
  - 가격은 월평균 종가이고 마지막 달만 그달 초 종가다(엑셀 하단 주석).
- CSV 첫 줄에 `# source: ..., fetched: YYYY-MM-DD` 주석을 남긴다. 읽을 때는 `comment="#"`.

자동 다운로드가 막히거나 페이지 구조가 바뀐 경우(수동 절차):
1. 브라우저로 https://shillerdata.com/ 를 열고 "ie_data (xls)" 다운로드 링크의 파일을 받는다.
2. `--xls <경로> --source-url https://shillerdata.com/` 로 이 스크립트를 다시 실행한다.
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

PAGE_URL = "https://shillerdata.com/"
STALE_URL = "http://www.econ.yale.edu/~shiller/data/ie_data.xls"
SHEET = "Data"
COL_DATE, COL_REAL_PRICE, COL_REAL_EARNINGS, COL_CAPE = 0, 7, 10, 12
OUT = Path(__file__).resolve().parents[1] / "seed" / "indicators" / "shiller.csv"
UA = {"User-Agent": "Mozilla/5.0"}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def find_xls_url() -> str:
    html = _get(PAGE_URL).decode("utf-8", errors="replace")
    m = re.search(r"""(?:https?:)?(//[^"'\s)]*ie_data\.xls[^"'\s)]*)""", html)
    if m:
        return "https:" + m.group(1)
    print(
        f"경고: {PAGE_URL} 에서 ie_data.xls 링크를 찾지 못해 2023-09에서 멈춘 옛 주소를 쓴다",
        file=sys.stderr,
    )
    return STALE_URL


def _num(v) -> float | None:
    """엑셀 셀 → float. 빈칸·'NA'·문자열은 None."""
    if isinstance(v, bool) or not isinstance(v, int | float):
        return None
    return float(v) if v == v else None


def parse_ie_data(rows: Iterable[Sequence]) -> pd.DataFrame:
    """Data 시트의 행들 → DataFrame(date 월말, cape, ep). 날짜 열이 숫자인 행만 쓴다."""
    out = []
    for row in rows:
        d = _num(row[COL_DATE]) if len(row) > COL_CAPE else None
        if d is None or not 1800 < d < 2200:
            continue
        year = int(d)
        month = round((d - year) * 100)
        if not 1 <= month <= 12:
            continue
        cape = _num(row[COL_CAPE])
        price, earn = _num(row[COL_REAL_PRICE]), _num(row[COL_REAL_EARNINGS])
        ep = earn / price if price and earn is not None else None
        if cape is None and ep is None:
            continue
        date = pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)
        out.append((date, cape, ep))
    df = pd.DataFrame(out, columns=["date", "cape", "ep"]).sort_values("date")
    if df.empty:
        raise RuntimeError("Data 시트에서 CAPE/E-P 값을 읽지 못했다")
    return df.reset_index(drop=True)


def read_sheet_rows(xls_path: Path) -> list[list]:
    import xlrd

    wb = xlrd.open_workbook(xls_path)
    ws = wb.sheet_by_name(SHEET)
    return [ws.row_values(i) for i in range(ws.nrows)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xls", type=Path, help="이미 받은 xls 경로(없으면 자동 다운로드)")
    ap.add_argument("--source-url", help="--xls 사용 시 CSV 주석에 적을 출처 URL")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    if args.xls:
        df, url = parse_ie_data(read_sheet_rows(args.xls)), args.source_url or PAGE_URL
    else:
        url = find_xls_url()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ie_data.xls"
            path.write_bytes(_get(url))
            df = parse_ie_data(read_sheet_rows(path))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fetched = dt.date.today().isoformat()
    with args.out.open("w", encoding="utf-8") as f:
        f.write(
            f"# source: {url} (Robert Shiller, sheet {SHEET}; cape=P/E10, "
            f"ep=real trailing-12m earnings / real price, decimal), fetched: {fetched}\n"
        )
        df.assign(date=df["date"].dt.strftime("%Y-%m-%d")).to_csv(
            f, index=False, float_format="%.8f"
        )
    print(
        f"{len(df)}행, {df['date'].min():%Y-%m} ~ {df['date'].max():%Y-%m} -> {args.out}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()

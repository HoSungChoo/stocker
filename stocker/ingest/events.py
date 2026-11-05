"""이벤트 데이터: FOMC·CPI·고용보고서 일정, 반도체 실적일, 수작업 정책·전쟁·선거 목록.

이벤트는 차트에 맥락을 보여 주는 용도다. 인과를 주장하지 않고 유사도 점수에도 쓰지 않는다.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd
import sqlalchemy as sa

logger = logging.getLogger(__name__)

EARNINGS_SYMBOLS = ["NVDA", "TSM", "MU", "AMD", "ASML", "AVGO", "INTC"]
VALID_KINDS = ["fomc", "cpi", "jobs", "earnings", "policy", "war", "election"]
COLUMNS = ["date", "kind", "title", "symbol", "scheduled", "source", "meta"]

SEED_DIR = Path(__file__).resolve().parents[2] / "seed" / "events"
POLICY_SEED_PATH = SEED_DIR / "policy.json"

FOMC_CURRENT_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FOMC_HISTORICAL_URL = "https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm"
FOMC_FIRST_YEAR = 1994

# BLS는 자동 접속(봇)을 403으로 막는다. 같은 발표일을 FRED 릴리스 일정 API가 공개하므로 그쪽을 쓴다.
FRED_RELEASE_DATES_URL = "https://api.stlouisfed.org/fred/release/dates"
FRED_RELEASES = {
    "cpi": (10, "소비자물가지수(CPI) 발표"),
    "jobs": (50, "고용보고서(Employment Situation) 발표"),
}
BLS_FIRST_DATE = "1994-01-01"

USER_AGENT = "stocker-research/0.1 (personal research; https://github.com/HoSungChoo/stocker)"

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}


def _empty() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in COLUMNS})


def _frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return _empty()
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values(["date", "kind", "title"]).reset_index(drop=True)


def _http_get(url: str, retries: int = 2, timeout: int = 30) -> str:
    """stdlib urllib GET. 실패하면 짧게 재시도 후 예외를 그대로 던진다."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise AssertionError("unreachable")


# --------------------------------------------------------------------------- FOMC


def _month_of(token: str) -> int | None:
    return _MONTHS.get(token[:3].lower())


def _fomc_row(year: int, month: int, day: int, flavor: str, source: str) -> dict:
    titles = {
        "meeting": ("FOMC 회의 (금리 결정)", True),
        "unscheduled": ("FOMC 임시 회의", False),
        "call": ("FOMC 화상회의 (비정기)", False),
    }
    title, scheduled = titles[flavor]
    return {
        "date": dt.date(year, month, day),
        "kind": "fomc",
        "title": title,
        "symbol": None,
        "scheduled": scheduled,
        "source": source,
        "meta": {"source": source, "flavor": flavor},
    }


def parse_fomc_historical(html: str, source: str) -> list[dict]:
    """과거 연도 페이지. `<h5>` 제목 예: 'January 31-February 1 Meeting - 1995'.

    날짜는 회의 마지막 날(결정 발표일). 취소·서면표결(notation vote)은 건너뛴다.
    """
    rows = []
    for raw in re.findall(r"<h5[^>]*>(.*?)</h5>", html, re.S):
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", raw)).strip()
        m = re.search(r"-\s*(\d{4})\s*$", text)
        if not m:
            continue
        lowered = text.lower()
        if "cancel" in lowered or "notation vote" in lowered:
            continue
        year = int(m.group(1))
        body = re.sub(r"\([^)]*\)", " ", text[: m.start()])
        months = [_month_of(t) for t in re.findall(r"[A-Z][a-z]+", body)]
        months = [x for x in months if x]
        days = re.findall(r"\d+", body)
        if not months or not days:
            continue
        if "conference call" in lowered:
            flavor = "call"
        elif "unscheduled" in lowered:
            flavor = "unscheduled"
        else:
            flavor = "meeting"
        try:
            rows.append(_fomc_row(year, months[-1], int(days[-1]), flavor, source))
        except ValueError:
            logger.warning("FOMC 날짜 해석 실패: %s", text)
    return rows


def parse_fomc_current(html: str, source: str) -> list[dict]:
    """현재 일정 페이지(최근 ~6년 + 내년 예정). 연도 구간은 '2026 FOMC Meetings' 제목으로 나뉜다."""
    rows = []
    sections = re.split(r"<h4><a[^>]*>(\d{4}) FOMC Meetings</a></h4>", html)
    for year_s, chunk in zip(sections[1::2], sections[2::2], strict=True):
        year = int(year_s)
        pairs = re.findall(
            r"fomc-meeting__month[^>]*>\s*<strong>([^<]*)</strong>.*?fomc-meeting__date[^>]*>([^<]*)<",
            chunk,
            re.S,
        )
        for month_label, date_label in pairs:
            lowered = date_label.lower()
            if "notation vote" in lowered or "cancel" in lowered:
                continue
            months = [_month_of(t) for t in re.split(r"[/\s]+", month_label.strip()) if t]
            months = [x for x in months if x]
            days = re.findall(r"\d+", re.sub(r"\([^)]*\)", " ", date_label))
            if not months or not days:
                continue
            flavor = "unscheduled" if "unscheduled" in lowered else "meeting"
            try:
                rows.append(_fomc_row(year, months[-1], int(days[-1]), flavor, source))
            except ValueError:
                logger.warning("FOMC 날짜 해석 실패: %s %s", month_label, date_label)
    return rows


def fetch_fomc() -> pd.DataFrame:
    """연준 FOMC 일정 (1994~내년). 현재 페이지가 덮는 연도 이전은 연도별 과거 페이지에서."""
    rows: list[dict] = []
    current = parse_fomc_current(_http_get(FOMC_CURRENT_URL), FOMC_CURRENT_URL)
    rows.extend(current)
    first_current = min((r["date"].year for r in current), default=dt.date.today().year)
    for year in range(FOMC_FIRST_YEAR, first_current):
        url = FOMC_HISTORICAL_URL.format(year=year)
        try:
            rows.extend(parse_fomc_historical(_http_get(url), url))
        except (urllib.error.URLError, TimeoutError) as exc:
            logger.warning("FOMC %d년 페이지를 받지 못했다: %s", year, exc)
    return _frame(rows)


# --------------------------------------------------------------------------- CPI / 고용보고서


def parse_release_dates(payload: dict, kind: str, release_id: int, title: str) -> list[dict]:
    """FRED `release/dates` 응답 → 이벤트 행."""
    source = f"https://fred.stlouisfed.org/release?rid={release_id}"
    rows = []
    for item in payload.get("release_dates", []):
        date = item["date"]
        if date < BLS_FIRST_DATE:
            continue
        rows.append(
            {
                "date": dt.date.fromisoformat(date),
                "kind": kind,
                "title": title,
                "symbol": None,
                "scheduled": True,
                "source": source,
                "meta": {"source": source, "publisher": "BLS", "fred_release_id": release_id},
            }
        )
    return rows


def fetch_bls(api_key: str | None = None) -> pd.DataFrame:
    """CPI·고용보고서 발표일. BLS 사이트가 봇을 막아 FRED 릴리스 일정 API(발표 주체는 BLS)를 쓴다.

    FRED 키가 없으면 경고 후 빈 결과. 과거 일정이 없다고 근사해서 채우지 않는다.
    """
    if api_key is None:
        from stocker.config import settings

        api_key = settings.fred_key
    if not api_key:
        logger.warning("FRED_API_KEY가 없어 CPI·고용보고서 일정을 건너뛴다")
        return _empty()
    rows: list[dict] = []
    for kind, (release_id, title) in FRED_RELEASES.items():
        query = urllib.parse.urlencode(
            {
                "release_id": release_id,
                "api_key": api_key,
                "file_type": "json",
                "include_release_dates_with_no_data": "true",
                "realtime_start": "1776-07-04",
                "realtime_end": "9999-12-31",
                "sort_order": "asc",
                "limit": 10000,
            }
        )
        try:
            payload = json.loads(_http_get(f"{FRED_RELEASE_DATES_URL}?{query}"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            # URL에 키가 들어 있으므로 예외 본문 대신 종류만 남긴다
            logger.warning("%s 일정을 받지 못했다 (%s)", kind, type(exc).__name__)
            continue
        if "release_dates" not in payload:
            logger.warning("%s 일정 응답이 비정상이다: %s", kind, payload.get("error_message", "?"))
            continue
        rows.extend(parse_release_dates(payload, kind, release_id, title))
    return _frame(rows)


# --------------------------------------------------------------------------- 실적일


def _num(value) -> float | None:
    return None if value is None or pd.isna(value) else float(value)


def earnings_rows(symbol: str, dates: pd.DataFrame | None) -> list[dict]:
    """yfinance `earnings_dates` 한 종목분 → 이벤트 행. 날짜는 거래소 현지(뉴욕) 기준일."""
    if dates is None or dates.empty:
        return []
    source = f"https://finance.yahoo.com/quote/{symbol}/"
    rows = []
    for ts, rec in dates.iterrows():
        ts = pd.Timestamp(ts)
        day = ts.tz_localize(None).date() if ts.tzinfo is not None else ts.date()
        reported = _num(rec.get("Reported EPS"))
        rows.append(
            {
                "date": day,
                "kind": "earnings",
                "title": f"{symbol} 실적 발표",
                "symbol": symbol,
                "scheduled": True,
                "source": source,
                "meta": {
                    "source": source,
                    "eps_estimate": _num(rec.get("EPS Estimate")),
                    "reported_eps": reported,
                    "surprise_pct": _num(rec.get("Surprise(%)")),
                    "confirmed": reported is not None,  # 미발표분은 추정 일정
                },
            }
        )
    return rows


def fetch_earnings(symbols: list[str] = EARNINGS_SYMBOLS) -> pd.DataFrame:
    """yfinance 실적 발표일(과거 수 년 + 예정). 못 받은 종목은 경고 후 건너뛴다."""
    import yfinance as yf

    rows: list[dict] = []
    for sym in symbols:
        try:
            dates = yf.Ticker(sym).get_earnings_dates(limit=60)
        except Exception as exc:  # yfinance는 다양한 예외를 낸다
            logger.warning("%s 실적일을 받지 못했다: %s", sym, exc)
            continue
        got = earnings_rows(sym, dates)
        if not got:
            logger.warning("%s 실적일이 비어 있다", sym)
        rows.extend(got)
    return _frame(rows)


# --------------------------------------------------------------------------- 수작업 목록

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_seed_items(items: list[dict], allowed_kinds: list[str] = VALID_KINDS) -> None:
    """필수 키·날짜 형식·kind 허용값·meta.source를 검사한다. 문제가 있으면 ValueError."""
    if not isinstance(items, list):
        raise ValueError("시드 파일 최상위는 리스트여야 한다")
    for i, item in enumerate(items):
        for key in ("date", "kind", "title", "meta"):
            if key not in item:
                raise ValueError(f"항목 {i}: '{key}' 누락")
        if not _DATE_RE.match(item["date"]):
            raise ValueError(f"항목 {i}: 날짜 형식 오류 {item['date']!r}")
        dt.date.fromisoformat(item["date"])
        if item["kind"] not in allowed_kinds:
            raise ValueError(f"항목 {i}: 허용되지 않은 kind {item['kind']!r}")
        if not isinstance(item["meta"], dict) or not item["meta"].get("source"):
            raise ValueError(f"항목 {i}: meta.source 누락")


def load_policy_seed(path: Path | str = POLICY_SEED_PATH) -> pd.DataFrame:
    """seed/events/policy.json → 이벤트 행. 사후 기록이므로 scheduled=False."""
    items = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_seed_items(items, ["policy", "war", "election"])
    rows = [
        {
            "date": dt.date.fromisoformat(it["date"]),
            "kind": it["kind"],
            "title": it["title"],
            "symbol": None,
            "scheduled": False,
            "source": it["meta"]["source"],
            "meta": it["meta"],
        }
        for it in items
    ]
    return _frame(rows)


# --------------------------------------------------------------------------- 적재

_UPSERT_SQL = sa.text(
    """
    INSERT INTO events (date, kind, title, symbol, scheduled, source, meta)
    VALUES (:date, :kind, :title, :symbol, :scheduled, :source, CAST(:meta AS jsonb))
    ON CONFLICT (date, kind, (coalesce(symbol, '')), title)
    DO UPDATE SET scheduled = EXCLUDED.scheduled, source = EXCLUDED.source, meta = EXCLUDED.meta
    """
)


def store_events(df: pd.DataFrame, engine: sa.Engine | None = None) -> int:
    """유니크 인덱스(date, kind, coalesce(symbol,''), title) 기준 upsert. 반환: 처리 행 수.

    `upsert_df`는 PK 컬럼으로 충돌을 잡는데 events의 PK는 bigserial이라 맞지 않아 직접 적재한다.
    """
    if df.empty:
        return 0
    from stocker.db import get_engine

    engine = engine or get_engine()
    df = df.drop_duplicates(subset=["date", "kind", "symbol", "title"], keep="last")
    params = [
        {
            "date": pd.Timestamp(r.date).date(),
            "kind": r.kind,
            "title": r.title,
            "symbol": None if r.symbol is None or pd.isna(r.symbol) else r.symbol,
            "scheduled": bool(r.scheduled),
            "source": r.source,
            "meta": json.dumps(r.meta, ensure_ascii=False),
        }
        for r in df.itertuples(index=False)
    ]
    with engine.begin() as conn:
        conn.execute(_UPSERT_SQL, params)
    return len(params)


def collect_events(only: list[str] | None = None) -> pd.DataFrame:
    """소스별로 모은다. 한 소스가 실패해도 경고 후 나머지를 계속한다."""
    sources = {
        "fomc": (["fomc"], fetch_fomc),
        "bls": (["cpi", "jobs"], fetch_bls),
        "earnings": (["earnings"], fetch_earnings),
        "policy": (["policy", "war", "election"], load_policy_seed),
    }
    wanted = set(only) if only else set(VALID_KINDS)
    frames = []
    for name, (kinds, fn) in sources.items():
        if not wanted & set(kinds):
            continue
        try:
            frames.append(fn())
        except Exception as exc:
            logger.warning("%s 수집 실패, 건너뜀: %s", name, exc)
    frames = [f for f in frames if not f.empty]
    if not frames:
        return _empty()
    df = pd.concat(frames, ignore_index=True)
    return df[df["kind"].isin(wanted)].reset_index(drop=True)


def ingest_events(engine: sa.Engine | None = None, only: list[str] | None = None) -> int:
    """FOMC·BLS·실적·정책 시드를 합쳐 events에 upsert. 반환: 처리 행 수."""
    return store_events(collect_events(only), engine)

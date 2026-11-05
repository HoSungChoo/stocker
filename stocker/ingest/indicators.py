"""환경 지표 수집: 설비투자/GDP(FRED 두 시리즈의 비), 실러 CAPE·E/P, 리터 공모주 첫날 수익률.

모두 macro_series(series_id, date, value, source)에 넣는다. 원 단위 그대로 저장한다.
실러·리터는 `make seed-indicators`가 만드는 seed/indicators/*.csv(git 미추적)에서 읽는다.
한 시리즈가 실패해도 경고만 남기고 나머지를 계속한다.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from stocker.ingest.fred import COLUMNS, fetch_fred

logger = logging.getLogger(__name__)

SEED_DIR = Path(__file__).resolve().parents[2] / "seed" / "indicators"
SHILLER_PATH = SEED_DIR / "shiller.csv"
RITTER_PATH = SEED_DIR / "ritter_ipo.csv"

# 정보처리장비·소프트웨어 민간고정투자(BEA, 명목, 분기, 연율 SAAR, 십억 달러) / 명목 GDP(같은 단위)
CAPEX_NUMERATOR = "A679RC1Q027SBEA"
CAPEX_DENOMINATOR = "GDP"

CAPEX_ID = "CAPEX_IPS_GDP"
CAPE_ID = "SHILLER_CAPE"
EP_ID = "SHILLER_EP"
IPO_ID = "IPO_FIRSTDAY"
INDICATOR_IDS = [CAPEX_ID, CAPE_ID, EP_ID, IPO_ID]


def _empty() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in COLUMNS})


def _long(series_id: str, date, value, source: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "series_id": series_id,
            "date": pd.DatetimeIndex(date),
            "value": pd.Series(value).astype("float64").to_numpy(),
            "source": source,
        }
    )[COLUMNS]


def compute_capex_ratio(fred_long: pd.DataFrame) -> pd.DataFrame:
    """fetch_fred 결과(long)에서 CAPEX_IPS_GDP = 분자 / GDP. 날짜는 분기말.

    FRED의 분기 시리즈는 분기 첫날(1947-01-01 = 1분기)로 찍혀 있어 분기말로 옮긴다.
    두 시리즈가 모두 있는 분기만 남긴다.
    """
    wide = fred_long.pivot(index="date", columns="series_id", values="value")
    missing = {CAPEX_NUMERATOR, CAPEX_DENOMINATOR} - set(wide.columns)
    if missing:
        raise RuntimeError(f"FRED에서 받지 못한 시리즈: {sorted(missing)}")
    ratio = (wide[CAPEX_NUMERATOR] / wide[CAPEX_DENOMINATOR]).dropna()
    if ratio.empty:
        raise RuntimeError("설비투자/GDP를 계산할 공통 분기가 없다")
    quarter_end = pd.DatetimeIndex(ratio.index) + pd.offsets.QuarterEnd(0)
    return _long(CAPEX_ID, quarter_end, ratio.to_numpy(), "fred")


def fetch_capex_ratio() -> pd.DataFrame:
    return compute_capex_ratio(fetch_fred([CAPEX_NUMERATOR, CAPEX_DENOMINATOR], start="1947-01-01"))


def _read_seed(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"{path} 가 없다. `make seed-indicators` 로 먼저 만들 것")
    return pd.read_csv(path, comment="#", parse_dates=["date"])


def load_shiller(path: Path | str = SHILLER_PATH) -> pd.DataFrame:
    """shiller.csv(date,cape,ep) → SHILLER_CAPE, SHILLER_EP long. 날짜는 월말, 빈 값은 행 생략."""
    raw = _read_seed(Path(path))
    date = pd.DatetimeIndex(raw["date"]).to_period("M").to_timestamp(how="end").normalize()
    parts = []
    for sid, col in ((CAPE_ID, "cape"), (EP_ID, "ep")):
        ok = raw[col].notna().to_numpy()
        parts.append(_long(sid, date[ok], raw.loc[ok, col], "shiller"))
    return pd.concat(parts, ignore_index=True).sort_values(["series_id", "date"], ignore_index=True)


def expand_annual_to_monthly(annual: pd.DataFrame) -> pd.DataFrame:
    """연간 값(date의 연도, value)을 그 해 1~12월 각 월말로 복제. 반환 열: date, value."""
    rows = []
    for d, v in zip(pd.DatetimeIndex(annual["date"]), annual["value"], strict=True):
        for m in pd.date_range(f"{d.year}-01-31", periods=12, freq="ME"):
            rows.append((m, v))
    return pd.DataFrame(rows, columns=["date", "value"])


def load_ritter(path: Path | str = RITTER_PATH) -> pd.DataFrame:
    """ritter_ipo.csv(date,value,freq) → IPO_FIRSTDAY long.

    freq=monthly는 월말 날짜로, freq=annual은 각 월말로 복제하고 source를 `ritter_annual`로
    표시한다. 같은 달에 월간·연간이 겹치면 월간이 이긴다. freq 열이 없으면 월간으로 본다.
    """
    raw = _read_seed(Path(path)).dropna(subset=["value"])
    if "freq" not in raw.columns:
        raw = raw.assign(freq="monthly")
    parts = []
    monthly = raw[raw["freq"] == "monthly"]
    if not monthly.empty:
        date = pd.DatetimeIndex(monthly["date"]).to_period("M").to_timestamp(how="end").normalize()
        parts.append(_long(IPO_ID, date, monthly["value"], "ritter_monthly"))
    annual = raw[raw["freq"] == "annual"]
    if not annual.empty:
        m = expand_annual_to_monthly(annual)
        parts.append(_long(IPO_ID, m["date"], m["value"], "ritter_annual"))
    if not parts:
        raise RuntimeError(f"{path} 에 usable한 행이 없다")
    df = pd.concat(parts, ignore_index=True)
    df = df.drop_duplicates(subset=["date"], keep="first")  # 월간(앞쪽)이 우선
    return df.sort_values("date", ignore_index=True)


def fetch_indicators() -> pd.DataFrame:
    """네 지표를 하나의 long format DataFrame(series_id,date,value,source)으로.

    소스 단위(CAPEX / 실러 / 리터)로 실패를 격리한다. 실패한 소스는 경고하고 건너뛴다.
    """
    sources = {
        CAPEX_ID: fetch_capex_ratio,
        "shiller": load_shiller,
        "ritter": load_ritter,
    }
    frames = []
    for name, fn in sources.items():
        try:
            frames.append(fn())
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s 수집 실패, 건너뜀: %s", name, exc)
    if not frames:
        return _empty()
    return pd.concat(frames, ignore_index=True)[COLUMNS]


def store_indicators(df: pd.DataFrame, engine=None) -> int:
    """fetch_indicators 결과를 macro_series에 upsert. 영향 행 수 반환."""
    from stocker.db import upsert_df  # 지연 import

    return upsert_df(df, "macro_series", pk=["series_id", "date"], engine=engine)


def ingest_indicators(engine=None) -> int:
    return store_indicators(fetch_indicators(), engine=engine)

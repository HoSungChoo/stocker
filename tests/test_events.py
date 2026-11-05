import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest

import stocker.db
from stocker.ingest import events as ev

FIXTURES = Path(__file__).parent / "fixtures"
COLUMNS = ["date", "kind", "title", "symbol", "scheduled", "source", "meta"]


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _dates(rows):
    return [r["date"] for r in rows]


# --- FOMC 파서


def test_parse_fomc_current_skips_notation_vote_and_uses_last_day():
    rows = ev.parse_fomc_current(_read("events_fomc_current.html"), "http://x/cur")
    assert _dates(rows) == [
        dt.date(2026, 1, 28),
        dt.date(2026, 3, 18),  # 별표(*)는 무시
        dt.date(2026, 5, 1),  # Apr/May 30-1 → 회의 마지막 날은 5월 1일
        dt.date(2025, 2, 1),  # Jan/Feb 31-1
        dt.date(2025, 12, 10),
    ]  # 8월 22일 서면표결은 제외
    assert all(r["kind"] == "fomc" and r["scheduled"] for r in rows)
    assert all(r["meta"]["source"] == "http://x/cur" for r in rows)


def test_parse_fomc_historical_variants():
    rows = ev.parse_fomc_historical(_read("events_fomc_historical.html"), "http://x/hist")
    got = {(r["date"], r["meta"]["flavor"], r["scheduled"]) for r in rows}
    assert got == {
        (dt.date(1995, 2, 1), "meeting", True),  # January 31-February 1
        (dt.date(1995, 3, 10), "call", False),
        (dt.date(2012, 8, 1), "meeting", True),  # 공백이 둘인 제목
        (dt.date(2020, 3, 2), "unscheduled", False),
        (dt.date(2020, 12, 16), "meeting", True),
    }  # 취소·서면표결은 제외


# --- CPI/고용보고서 (FRED 릴리스 일정)


def test_parse_release_dates_filters_before_1994():
    payload = json.loads(_read("events_fred_release_dates.json"))
    rows = ev.parse_release_dates(payload, "cpi", 10, "CPI 발표")
    assert _dates(rows) == [dt.date(1994, 1, 14), dt.date(2025, 10, 24), dt.date(2026, 12, 10)]
    assert all(r["kind"] == "cpi" and r["scheduled"] for r in rows)
    assert rows[0]["meta"]["source"].endswith("rid=10")


def test_fetch_bls_without_key_returns_empty(monkeypatch):
    monkeypatch.setattr("stocker.config.settings.fred_api_key", None, raising=False)
    df = ev.fetch_bls(api_key="")
    assert df.empty and list(df.columns) == COLUMNS


def test_fetch_bls_uses_api_and_survives_one_failure(monkeypatch):
    payload = _read("events_fred_release_dates.json")
    calls = []

    def fake_get(url, **_):
        calls.append(url)
        if "release_id=50" in url:
            raise TimeoutError("boom")
        return payload

    monkeypatch.setattr(ev, "_http_get", fake_get)
    df = ev.fetch_bls(api_key="k" * 32)
    assert len(calls) == 2
    assert set(df["kind"]) == {"cpi"}  # jobs는 실패 → 건너뜀
    assert len(df) == 3


# --- 실적일


def test_earnings_rows_converts_new_york_date_and_meta(monkeypatch):
    idx = pd.DatetimeIndex(
        pd.to_datetime(["2026-08-26 16:00:00", "2026-11-17 16:00:00"]).tz_localize(
            "America/New_York"
        )
    )
    raw = pd.DataFrame(
        {
            "EPS Estimate": [2.09, 2.47],
            "Reported EPS": [2.22, float("nan")],
            "Surprise(%)": [6.16, float("nan")],
        },
        index=idx,
    )

    class FakeTicker:
        def __init__(self, sym):
            self.sym = sym

        def get_earnings_dates(self, limit=12):
            if self.sym == "BAD":
                raise RuntimeError("no data")
            return raw

    monkeypatch.setattr("yfinance.Ticker", FakeTicker)
    df = ev.fetch_earnings(["NVDA", "BAD"])

    assert list(df.columns) == COLUMNS
    assert len(df) == 2 and set(df["symbol"]) == {"NVDA"}
    assert df["date"].tolist() == [pd.Timestamp("2026-08-26"), pd.Timestamp("2026-11-17")]
    assert (df["kind"] == "earnings").all() and df["scheduled"].all()
    past, future = df["meta"].tolist()
    assert past["reported_eps"] == 2.22 and past["confirmed"] is True
    assert future["reported_eps"] is None and future["confirmed"] is False
    json.dumps(df["meta"].tolist())  # JSON 직렬화 가능


# --- 정책 시드


def test_policy_seed_loads_and_has_required_items():
    df = ev.load_policy_seed()
    assert list(df.columns) == COLUMNS
    assert set(df["kind"]) <= {"policy", "war", "election"}
    assert not df["scheduled"].any()
    assert df["date"].is_monotonic_increasing
    assert df["meta"].map(lambda m: m["source"].startswith("http")).all()
    json.dumps(df["meta"].tolist())
    dates = set(df["date"].dt.strftime("%Y-%m-%d"))
    for d in ["2001-09-11", "2008-09-15", "2011-03-11", "2022-02-24", "2022-10-07", "2023-10-17"]:
        assert d in dates
    elections = df[df["kind"] == "election"]["date"].dt.year.tolist()
    assert {2008, 2012, 2016, 2020, 2024} <= set(elections)
    assert not df.duplicated(["date", "kind", "title"]).any()


def _item(**over):
    base = {"date": "2020-03-11", "kind": "policy", "title": "t", "meta": {"source": "http://x"}}
    return {**base, **over}


@pytest.mark.parametrize(
    "bad",
    [
        {"date": "2020/03/11"},
        {"date": "2020-13-40"},
        {"kind": "bogus"},
        {"meta": {}},
        {"meta": "http://x"},
    ],
)
def test_validate_seed_items_rejects_bad(bad):
    ev.validate_seed_items([_item()])
    with pytest.raises(ValueError):
        ev.validate_seed_items([_item(**bad)])


def test_validate_seed_items_missing_key():
    item = _item()
    del item["title"]
    with pytest.raises(ValueError, match="title"):
        ev.validate_seed_items([item])


# --- 수집 조합


def test_collect_events_skips_failing_source(monkeypatch):
    def boom():
        raise OSError("down")

    monkeypatch.setattr(ev, "fetch_fomc", boom)
    monkeypatch.setattr(ev, "fetch_bls", lambda: ev._empty())
    monkeypatch.setattr(ev, "fetch_earnings", lambda: ev._empty())
    df = ev.collect_events()
    assert set(df["kind"]) <= {"policy", "war", "election"} and not df.empty


def test_collect_events_only_filters_kind(monkeypatch):
    df = ev.collect_events(["election"])
    assert set(df["kind"]) == {"election"}


# --- 네트워크 / DB


@pytest.mark.network
def test_fetch_fomc_live_has_about_eight_meetings_this_year():
    df = ev.fetch_fomc()
    this_year = dt.date.today().year
    n = int((df["date"].dt.year == this_year).sum())
    assert 7 <= n <= 11
    assert df["date"].min().year == 1994


@pytest.mark.db
def test_store_policy_events_idempotent_and_readable(engine):
    stocker.db.init_db(engine)
    seed = ev.load_policy_seed()
    ev.store_events(seed, engine)
    first = stocker.db.read_events(kinds=["policy"], engine=engine)
    ev.store_events(seed, engine)
    second = stocker.db.read_events(kinds=["policy"], engine=engine)

    assert len(first) == len(second) == int((seed["kind"] == "policy").sum())
    assert list(second.columns) == COLUMNS
    assert second["date"].is_monotonic_increasing
    assert second["date"].dt.tz is None
    assert isinstance(second["meta"].iloc[0], dict) and second["meta"].iloc[0]["source"]
    lehman = stocker.db.read_events("2008-09-01", "2008-09-30", ["policy"], engine)
    assert lehman["date"].dt.strftime("%Y-%m-%d").tolist() == ["2008-09-15"]

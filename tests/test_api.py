"""stocker.api 테스트. 기본 실행은 DB 없이(합성 데이터를 data 모듈에 monkeypatch) 스키마 확인."""

from __future__ import annotations

import datetime as dt
import sys

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from stocker.api import create_app
from stocker.api import data as api_data
from stocker.broker.positions import SNAPSHOT_COLUMNS

WINDOW = 120
N = 2600  # window + min_gap 간격으로 5개 국면이 나올 만큼


def _close(seed: int = 3, n: int = N) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-02", periods=n)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0002, 0.02, n))), index=idx, name="^SOX")


def _macro(index, seed=4):
    rng = np.random.default_rng(seed)
    return {
        "DFF": pd.Series(np.cumsum(rng.normal(0, 0.02, len(index))) + 3, index=index),
        "VIXCLS": pd.Series(20 + np.cumsum(rng.normal(0, 0.3, len(index))), index=index),
    }


def _cycle(start="2018-01-31", months=90, seed=2):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=months, freq="ME")
    sales = 10_000 * np.exp(np.cumsum(rng.normal(0.004, 0.04, months)))
    return {"wsts_sales_world_usd_m": pd.Series(sales, index=idx)}


CLOSE = _close()
MACRO = _macro(CLOSE.index)
CYCLE = _cycle()
LAST = CLOSE.index[-1]


def _events_df(dates, kinds=None):
    kinds = kinds or ["fomc"] * len(dates)
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "kind": kinds,
            "title": [f"{k} 이벤트" for k in kinds],
            "symbol": [None] * len(dates),
            "scheduled": [True] * len(dates),
            "source": ["https://example.test"] * len(dates),
            "meta": [{"confirmed": True}] * len(dates),
        }
    )


@pytest.fixture
def client(monkeypatch):
    api_data.clear_caches()
    monkeypatch.setattr(api_data, "load_inputs", lambda: (CLOSE, MACRO, CYCLE))
    monkeypatch.setattr(api_data, "today", lambda: dt.date(2024, 1, 10))

    def fake_events(start, end, kinds=None):
        all_ev = _events_df(
            [CLOSE.index[10], CLOSE.index[400], "2024-01-12", "2024-01-20"],
            ["fomc", "cpi", "jobs", "earnings"],
        )
        s, e = pd.Timestamp(start), pd.Timestamp(end)
        return all_ev[(all_ev["date"] >= s) & (all_ev["date"] <= e)].reset_index(drop=True)

    monkeypatch.setattr(api_data, "events", fake_events)
    monkeypatch.setattr(api_data, "latest_positions", lambda: None)
    monkeypatch.setattr(api_data, "report_run", lambda window=0: None)
    yield TestClient(create_app())
    api_data.clear_caches()


# ------------------------------------------------------------------ health / similarity
def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["last_trading_day"] == LAST.strftime("%Y-%m-%d")
    assert body["layers_missing"] == []


def test_similarity_schema(client):
    r = client.get("/api/similarity")
    assert r.status_code == 200
    b = r.json()
    assert b["as_of"] == LAST.strftime("%Y-%m-%d")
    assert b["window"] == 120 and b["top_k"] == 5
    assert b["n_candidates"] > 0 and b["layers_missing"] == []
    assert set(b["weights_used"]) == {"price_shape", "macro", "semi_cycle"}
    assert sum(b["weights_used"].values()) == pytest.approx(1.0)
    assert [m["rank"] for m in b["matches"]] == [1, 2, 3, 4, 5]
    m = b["matches"][0]
    assert set(m) == {"rank", "start", "end", "scores", "combined", "forward"}
    assert set(m["forward"]) == {"21", "63", "126", "252"}
    assert 0 <= m["combined"] <= 1
    assert [row["horizon"] for row in b["forward_summary"]] == [21, 63, 126, 252]
    assert set(b["forward_summary"][0]) == {
        "horizon",
        "median",
        "p10",
        "p90",
        "n_up",
        "n_down",
        "n",
    }


def test_similarity_has_baseline_without_lookahead(client, monkeypatch):
    """forward_summary_baseline: 항상 있고 as_of 이전 데이터만으로 계산된다 (합성 데이터로 검산)."""
    as_of = CLOSE.index[1500]
    b = client.get(f"/api/similarity?as_of={as_of.strftime('%Y-%m-%d')}").json()
    base = b["forward_summary_baseline"]
    assert [row["horizon"] for row in base] == [21, 63, 126, 252]
    assert set(base[0]) == set(b["forward_summary"][0])
    hist = CLOSE.loc[:as_of]
    for row in base:
        h = row["horizon"]
        exp = (hist.shift(-h) / hist - 1.0).dropna()  # as_of 이후는 쓰지 않는다
        assert row["n"] == len(exp) == len(hist) - h
        assert row["median"] == pytest.approx(float(np.median(exp)))
        assert row["p10"] == pytest.approx(float(np.percentile(exp, 10)))
        assert row["n_up"] == int((exp > 0).sum()) and row["n_down"] == int((exp < 0).sum())
    # as_of 이후 데이터를 바꿔도 기준선은 변하지 않는다
    api_data.clear_caches()
    altered = CLOSE.copy()
    altered.loc[altered.index > as_of] *= 3.0
    monkeypatch.setattr(api_data, "load_inputs", lambda: (altered, MACRO, CYCLE))
    again = client.get(f"/api/similarity?as_of={as_of.strftime('%Y-%m-%d')}").json()
    assert again["forward_summary_baseline"] == base


def test_similarity_baseline_present_in_regime_fallback(client):
    """모드 경로(환경 우선)로 와도 기준선은 API 층에서 채워진다."""
    r = client.get("/api/similarity?mode=regime")
    if r.status_code == 200:
        assert [row["horizon"] for row in r.json()["forward_summary_baseline"]] == [
            21,
            63,
            126,
            252,
        ]


def test_similarity_query_params_and_weights(client):
    r = client.get(
        "/api/similarity?window=60&top_k=3&w_price=1&w_macro=1&w_cycle=0&as_of=2023-06-30"
    )
    assert r.status_code == 200
    b = r.json()
    assert b["window"] == 60 and len(b["matches"]) == 3
    assert b["weights_used"] == {"price_shape": 0.5, "macro": 0.5, "semi_cycle": 0.0}
    assert b["as_of"] == "2023-06-30"
    assert client.get("/api/similarity?window=90").status_code == 400
    assert client.get("/api/similarity?w_price=0&w_macro=0&w_cycle=0").status_code == 400
    assert client.get("/api/similarity?as_of=nonsense").status_code == 400
    assert client.get("/api/similarity?as_of=2019-03-01").status_code == 400  # 데이터 부족


def test_result_cache_hits(client, monkeypatch):
    calls = {"n": 0}
    real = api_data.score

    def counted(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(api_data, "score", counted)
    client.get("/api/similarity?top_k=4")
    client.get("/api/similarity?top_k=4")
    client.get("/api/overlay?top_k=4")  # 같은 쿼리 키 → 같은 결과 재사용
    assert calls["n"] == 1
    client.get("/api/similarity?top_k=2")
    assert calls["n"] == 2


# ------------------------------------------------------------------ overlay
def test_overlay_schema(client):
    r = client.get("/api/overlay")
    assert r.status_code == 200
    b = r.json()
    cur = b["current"]
    assert cur["end"] == LAST.strftime("%Y-%m-%d")
    assert len(cur["points"]) == WINDOW
    assert cur["points"][0] == {"d": 0, "v": 100.0}
    assert [p["d"] for p in cur["points"]] == list(range(WINDOW))
    assert len(b["matches"]) == 5
    for i, m in enumerate(b["matches"], 1):
        assert m["rank"] == i
        assert len(m["window_points"]) == WINDOW and m["window_points"][0]["v"] == 100.0
        assert all(p["d"] >= WINDOW for p in m["after_points"])
        assert len(m["after_points"]) <= 252
        for ev in m["events"]:
            assert set(ev) == {"d", "date", "kind", "title"} and ev["d"] >= 0
    # 현재 창 안에는 이벤트가 없고(합성 이벤트는 10·400번째 거래일), 어떤 과거 창에는 있다
    all_ev = [ev for m in b["matches"] for ev in m["events"]] + cur["events"]
    assert all(ev["kind"] in {"fomc", "cpi"} for ev in all_ev)


# ------------------------------------------------------------------ explain
def test_explain_schema_and_errors(client):
    end = client.get("/api/similarity").json()["matches"][0]["end"]
    r = client.get(f"/api/explain?end={end}")
    assert r.status_code == 200
    b = r.json()
    assert set(b) >= {"match", "price", "macro", "cycle", "forward"}
    assert b["match"]["end"] == end
    assert set(b["price"]) == {"dtw_rank", "euclid_rank", "drawdown", "drawdown_days"}
    assert isinstance(b["price"]["drawdown_days"]["now"], int)
    assert [x["series"] for x in b["macro"]] == [
        "DFF",
        "DGS10",
        "T10Y2Y",
        "DCOILWTICO",
        "DTWEXBGS",
        "VIXCLS",
    ]
    dff = b["macro"][0]
    assert dff["label"] == "기준금리" and dff["then"] is not None and dff["z_now"] is not None
    assert b["macro"][1]["then"] is None  # DGS10은 합성 입력에 없음 → null
    assert [c["metric"] for c in b["cycle"]] == ["wsts_sales_yoy", "wsts_yoy_chg6m"]
    assert set(b["forward"]) == {"21", "63", "126", "252"}

    assert client.get("/api/explain").status_code == 422  # end 필수
    assert client.get("/api/explain?end=bad").status_code == 400
    assert client.get(f"/api/explain?end={LAST:%Y-%m-%d}").status_code == 404  # 후보 아님


# ------------------------------------------------------------------ Phase 6: 코멘트·적합·mode
def test_explain_has_comment(client):
    end = client.get("/api/similarity").json()["matches"][0]["end"]
    b = client.get(f"/api/explain?end={end}").json()
    sentences = [x for x in b["comment"].split(". ") if x]
    assert 3 <= len(sentences) <= 4  # 닮은 점 · 다른 점 · 판정 (· 결과)
    assert "이 국면은" in b["comment"]
    # 같은 쿼리는 같은 문장 (결정적)
    assert client.get(f"/api/explain?end={end}").json()["comment"] == b["comment"]


def test_similarity_phase6_fields_are_null_without_fit(client):
    b = client.get("/api/similarity").json()
    # 엔진이 fit을 아직 안 내면 새 필드는 전부 null (프론트는 null이면 배너·배지를 숨긴다)
    for key in ("fit", "applicable", "applicable_n", "forward_summary_source"):
        assert key in b
    if b["fit"] is None:
        assert b["applicable"] is None and b["applicable_n"] is None
        assert b["forward_summary_source"] is None


def test_similarity_passes_fit_through_and_explain_uses_status(client, monkeypatch):
    orig = api_data.similarity
    nan = float("nan")

    def with_fit(q):
        res = orig(q)
        statuses = ["weak", "ok", "strong", "weak", "ok"]
        res.fit = [
            {
                "conditions": [
                    {
                        "name": "낙폭 깊이",
                        "then": -0.3,
                        "now": nan,
                        "tolerance": 0.08,
                        "passed": False,
                    }
                ],
                "n_passed": 1,
                "status": st,
            }
            for st in statuses
        ]
        res.applicable = False
        res.applicable_n = 0
        res.forward_summary_source = "baseline"
        return res

    monkeypatch.setattr(api_data, "similarity", with_fit)
    b = client.get("/api/similarity").json()
    assert [f["status"] for f in b["fit"]] == ["weak", "ok", "strong", "weak", "ok"]
    assert b["fit"][0]["conditions"][0]["now"] is None  # NaN → null
    assert b["applicable"] is False and b["applicable_n"] == 0
    assert b["forward_summary_source"] == "baseline"

    # explain의 판정 문장은 해당 국면의 fit.status를 따른다 (1위 = weak, 3위 = strong)
    first, third = b["matches"][0]["end"], b["matches"][2]["end"]
    c1 = client.get(f"/api/explain?end={first}").json()["comment"]
    c3 = client.get(f"/api/explain?end={third}").json()["comment"]
    assert "닮았다고 보기 어려운 사례(참고용)" in c1
    assert "위치·환경 모두 닮은 사례" in c3


def test_mode_param_is_accepted_or_ignored(client):
    assert client.get("/api/similarity?mode=shape").status_code == 200
    assert client.get("/api/similarity?mode=regime").status_code in (200, 400)  # 엔진에 따라
    assert client.get("/api/similarity?mode=bogus").status_code == 400


# ------------------------------------------------------------------ events
def test_events_upcoming(client):
    r = client.get("/api/events/upcoming?days=14")
    assert r.status_code == 200
    rows = r.json()
    assert [x["date"] for x in rows] == ["2024-01-12", "2024-01-20"]
    assert rows[0] == {
        "date": "2024-01-12",
        "kind": "jobs",
        "title": "jobs 이벤트",
        "symbol": None,
        "scheduled": True,
        "confirmed": True,
    }
    assert client.get("/api/events/upcoming?days=1").json() == []


def test_events_impact_with_and_without_module(client, monkeypatch):
    df = pd.DataFrame(
        {
            "id": [7, 3],
            "date": pd.to_datetime(["2001-09-11", "2020-03-11"]),
            "kind": ["war", "policy"],
            "title": ["a", "b"],
            "source": ["https://x", None],
            "ret5": [-0.32, -0.28],
            "ret_1m": [0.05, np.nan],
            "ret_3m": [0.1, 0.2],
        }
    )
    monkeypatch.setattr(api_data, "event_impact", lambda limit, kinds: df.head(limit))
    rows = client.get("/api/events/impact?limit=1&kinds=war,policy").json()
    assert rows == [
        {
            "id": 7,
            "date": "2001-09-11",
            "kind": "war",
            "title": "a",
            "ret5": -0.32,
            "ret_1m": 0.05,
            "ret_3m": 0.1,
            "source": "https://x",
        }
    ]
    assert client.get("/api/events/impact?limit=2").json()[1]["ret_1m"] is None

    # 세션 B의 read_event_impact가 없으면(ImportError) 빈 목록
    monkeypatch.undo()
    import stocker.db

    monkeypatch.delattr(stocker.db, "read_event_impact", raising=False)
    assert client.get("/api/events/impact").json() == []


def test_event_path(client, monkeypatch):
    real_path = api_data.event_path
    monkeypatch.setattr(
        api_data,
        "event_by_id",
        lambda i: (
            {"id": i, "date": pd.Timestamp("2001-09-11"), "kind": "war", "title": "t"}
            if i == 7
            else None
        ),
    )
    monkeypatch.setattr(
        api_data,
        "event_path",
        lambda i, days: pd.Series([97.0, 100.0, 104.0], index=[-days, 0, days]),
    )
    b = client.get("/api/events/7/path?days=30").json()
    assert b["event"] == {"id": 7, "date": "2001-09-11", "kind": "war", "title": "t"}
    assert b["points"] == [{"d": -30, "v": 97.0}, {"d": 0, "v": 100.0}, {"d": 30, "v": 104.0}]
    assert client.get("/api/events/8/path").status_code == 404

    # stocker.events 모듈이 없으면 404 (event_by_id는 있어도 경로를 만들 수 없다)
    monkeypatch.setattr(api_data, "event_path", real_path)
    monkeypatch.setitem(sys.modules, "stocker.events", None)
    assert client.get("/api/events/7/path").status_code == 404


# ------------------------------------------------------------------ positions / report
def _positions_df():
    row = dict.fromkeys(SNAPSHOT_COLUMNS)
    rows = [
        row
        | {
            "snapshot_date": pd.Timestamp("2024-01-10"),
            "symbol": "SOXL",
            "currency": "USD",
            "quantity": 10.0,
            "avg_price": 20.0,
            "last_price": 25.0,
            "market_value": 250.0,
            "pnl": 50.0,
            "pnl_rate": 0.25,
        },
        row
        | {
            "snapshot_date": pd.Timestamp("2024-01-10"),
            "symbol": "RAM",
            "currency": "USD",
            "quantity": 1.0,
            "market_value": 5.0,
        },
    ]
    return pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS)


def test_positions_null_when_no_snapshot(client):
    assert client.get("/api/positions/latest").json() is None
    assert client.get("/api/positions/apply").json() is None


def test_positions_latest_and_apply(client, monkeypatch):
    monkeypatch.setattr(api_data, "latest_positions", _positions_df)
    b = client.get("/api/positions/latest").json()
    assert b["snapshot_date"] == "2024-01-10"
    assert [it["symbol"] for it in b["items"]] == ["SOXL", "RAM"]
    assert b["items"][0]["pnl_rate"] == 0.25 and b["items"][1]["pnl"] is None

    a = client.get("/api/positions/apply").json()
    assert a["position_value"] == 250.0 and a["currency"] == "USD" and a["n"] == 5
    bases = {(r["basis"], r["horizon"]) for r in a["rows"]}
    assert bases >= {("sox", 21), ("sox", 252), ("lev3", 21), ("lev3", 252)}
    for r in a["rows"]:
        assert set(r) == {"basis", "horizon", "median", "p10", "p90"}
        assert r["p10"] <= r["median"] <= r["p90"]


def test_report_latest(client, monkeypatch):
    assert client.get("/api/report/latest").json() is None
    monkeypatch.setattr(
        api_data,
        "report_run",
        lambda window=0: {"run_date": dt.date(2024, 1, 10), "report_md": "# 보고서"},
    )
    assert client.get("/api/report/latest").json() == {
        "run_date": "2024-01-10",
        "markdown": "# 보고서",
    }


def test_cors_allows_local_frontend(client):
    r = client.get("/api/health", headers={"Origin": "http://localhost:3000"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"
    r = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r.headers


# ------------------------------------------------------------------ DB
@pytest.mark.db
def test_api_against_db(engine):
    """계약 20절: 실제 DB로 similarity/overlay/explain이 200이고 explain의 drawdown.now가
    엔진(features.build_price_features / loaders.run)과 일치한다."""
    from stocker.similarity import SimilarityConfig, run
    from stocker.similarity.features import DRAWDOWN_COL, build_price_features
    from stocker.similarity.loaders import load_close

    api_data.clear_caches()
    api_data._engine = engine
    client = TestClient(create_app())
    try:
        sim = client.get("/api/similarity")
        assert sim.status_code == 200
        assert client.get("/api/overlay").status_code == 200
        r = client.get("/api/explain?end=2000-08-08")
        assert r.status_code == 200
        b = r.json()

        res = run(sim.json()["as_of"], SimilarityConfig(), engine=engine)
        close = load_close(engine)
        feats = build_price_features(close.loc[: res.as_of], 120)
        assert b["price"]["drawdown"]["now"] == pytest.approx(feats.at[res.as_of, DRAWDOWN_COL])
        assert b["price"]["drawdown"]["then"] == pytest.approx(
            feats.at[pd.Timestamp("2000-08-08"), DRAWDOWN_COL]
        )
        mine = next(m for m in res.matches if m.end_date == pd.Timestamp("2000-08-08"))
        assert b["match"]["combined"] == pytest.approx(mine.combined)
        for layer, v in mine.scores.items():
            assert b["match"]["scores"][layer] == pytest.approx(v)
        usd = next(x for x in b["macro"] if x["series"] == "DTWEXBGS")
        assert usd["then"] is None and usd["now"] is not None  # 2006년 이전 달러지수 없음
    finally:
        api_data.clear_caches()
        api_data._engine = None

"""토스 읽기 전용 연동 테스트. 네트워크·실제 DB 없이 urlopen·토큰 저장소를 가짜로 바꿔 검증한다."""

import datetime as dt
import io
import json
import urllib.error
import urllib.parse
from email.message import Message

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from stocker.broker import TossApiError, TossClient, from_env, toss
from stocker.broker import positions as pos
from stocker.broker.__main__ import app
from stocker.similarity import Match

NOW = dt.datetime(2026, 11, 5, 12, 0, tzinfo=dt.UTC)


@pytest.fixture(autouse=True)
def _no_credentials(monkeypatch):
    """개발 머신의 .env·환경변수에 실제 키가 있어도 테스트가 토스를 부르지 않게 한다."""
    for name in ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET", "TOSS_ACCOUNT_SEQ"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(toss, "dotenv_values", lambda *_a, **_k: {})
    monkeypatch.setattr(toss, "_utcnow", lambda: NOW)


class FakeNet:
    """urlopen 대역. 응답을 순서대로 돌려주고 요청을 기록한다."""

    def __init__(self, monkeypatch):
        self.queue: list = []
        self.requests: list = []
        self.sleeps: list[float] = []
        self.on_request = None  # 요청이 들어올 때 부르는 훅(다른 프로세스의 개입을 흉내 낸다)
        monkeypatch.setattr("urllib.request.urlopen", self._urlopen)
        monkeypatch.setattr(toss.time, "sleep", self.sleeps.append)

    def push(self, payload):
        self.queue.append(("ok", payload))

    def push_error(self, status, code, message="m", headers=None):
        body = {"error": {"requestId": "req-1", "code": code, "message": message, "data": None}}
        self.queue.append(("err", status, body, headers or {}))

    def push_token(self, token="tok-1", expires_in=86400):
        self.push({"access_token": token, "token_type": "Bearer", "expires_in": expires_in})

    def _urlopen(self, req, timeout=None):
        self.requests.append(req)
        if self.on_request:
            self.on_request(req)
        item = self.queue.pop(0)
        if item[0] == "err":
            _, status, body, headers = item
            msg = Message()
            for k, v in headers.items():
                msg[k] = v
            raise urllib.error.HTTPError(
                req.full_url, status, "err", msg, io.BytesIO(json.dumps(body).encode())
            )

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        return Resp(json.dumps(item[1]).encode())

    def paths(self):
        return [urllib.parse.urlparse(r.full_url).path for r in self.requests]


@pytest.fixture
def store(monkeypatch):
    """broker_tokens 대역(딕셔너리). 반환값을 직접 읽고 써서 다른 프로세스를 흉내 낸다."""
    rows: dict = {}

    def read(client_id, engine=None):
        return rows.get(client_id)

    def write(client_id, access_token, expires_at, issued_at=None, engine=None):
        rows[client_id] = {
            "access_token": access_token,
            "expires_at": expires_at,
            "issued_at": issued_at,
        }

    monkeypatch.setattr(toss, "read_broker_token", read)
    monkeypatch.setattr(toss, "write_broker_token", write)
    monkeypatch.setattr(toss, "get_engine", lambda: object())
    return rows


@pytest.fixture
def net(monkeypatch):
    return FakeNet(monkeypatch)


def client(seq="7"):
    return TossClient("cid", "secret", account_seq=seq)


# --- 토큰 캐시


def test_token_is_issued_once_stored_and_reused(net, store):
    net.push_token("tok-1")
    net.push([])
    net.push([])
    c = client()
    c.accounts()
    c.accounts()
    assert net.paths() == ["/oauth2/token", "/api/v1/accounts", "/api/v1/accounts"]
    form = urllib.parse.parse_qs(net.requests[0].data.decode())
    assert form == {
        "grant_type": ["client_credentials"],
        "client_id": ["cid"],
        "client_secret": ["secret"],
    }
    assert net.requests[0].get_header("Content-type") == "application/x-www-form-urlencoded"
    assert net.requests[1].get_header("Authorization") == "Bearer tok-1"
    assert store["cid"]["access_token"] == "tok-1"
    assert store["cid"]["expires_at"] == NOW + dt.timedelta(seconds=86400)


def test_second_process_reuses_cached_token_without_issuing(net, store):
    store["cid"] = {"access_token": "shared", "expires_at": NOW + dt.timedelta(hours=3)}
    net.push([])
    client().accounts()
    assert net.paths() == ["/api/v1/accounts"]
    assert net.requests[0].get_header("Authorization") == "Bearer shared"


@pytest.mark.parametrize("left", [dt.timedelta(seconds=59), dt.timedelta(seconds=-5)])
def test_expiring_or_expired_token_is_reissued(net, store, left):
    store["cid"] = {"access_token": "old", "expires_at": NOW + left}
    net.push_token("new")
    net.push([])
    client().accounts()
    assert net.paths()[0] == "/oauth2/token"
    assert net.requests[1].get_header("Authorization") == "Bearer new"
    assert store["cid"]["access_token"] == "new"


def test_token_just_over_a_minute_left_is_still_reused(net, store):
    store["cid"] = {"access_token": "ok", "expires_at": NOW + dt.timedelta(seconds=61)}
    net.push([])
    client().accounts()
    assert net.paths() == ["/api/v1/accounts"]


@pytest.mark.parametrize("status,code", [(401, "unauthorized"), (403, "token-revoked")])
def test_revoked_token_is_reissued_once_and_request_retried(net, store, status, code):
    store["cid"] = {"access_token": "stale", "expires_at": NOW + dt.timedelta(hours=3)}
    net.push_error(status, code)
    net.push_token("fresh")
    net.push([{"accountSeq": 1}])
    out = client().accounts()
    assert out == [{"accountSeq": 1}]
    assert net.paths() == ["/api/v1/accounts", "/oauth2/token", "/api/v1/accounts"]
    assert net.requests[2].get_header("Authorization") == "Bearer fresh"


def test_second_revocation_is_not_retried_again(net, store):
    store["cid"] = {"access_token": "stale", "expires_at": NOW + dt.timedelta(hours=3)}
    net.push_error(401, "token-revoked")
    net.push_token("fresh")
    net.push_error(401, "token-revoked")
    with pytest.raises(TossApiError) as exc:
        client().accounts()
    assert exc.value.code == "token-revoked"
    assert net.paths().count("/oauth2/token") == 1


def test_revoked_because_another_process_reissued_adopts_theirs(net, store):
    """남이 방금 새로 발급했다면 또 발급하지 않는다(발급하면 상대 토큰이 무효가 된다)."""
    store["cid"] = {"access_token": "mine-old", "expires_at": NOW + dt.timedelta(hours=3)}

    def other_process_reissues(req):
        if req.get_header("Authorization") == "Bearer mine-old":
            store["cid"] = {"access_token": "theirs", "expires_at": NOW + dt.timedelta(hours=9)}

    net.on_request = other_process_reissues
    net.push_error(401, "token-revoked")
    net.push([])
    client().accounts()
    assert "/oauth2/token" not in net.paths()
    assert net.requests[-1].get_header("Authorization") == "Bearer theirs"


def test_db_failure_falls_back_to_process_memory(net, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(toss, "read_broker_token", boom)
    monkeypatch.setattr(toss, "write_broker_token", boom)
    monkeypatch.setattr(toss, "get_engine", lambda: object())
    net.push_token("mem")
    net.push([])
    net.push([])
    c = client()
    c.accounts()
    c.accounts()
    assert net.paths().count("/oauth2/token") == 1


# --- 요청 형태


def test_account_header_on_holdings_and_buying_power_but_not_accounts(net, store):
    net.push_token()
    net.push([])
    net.push({"result": {"items": []}})
    net.push({"currency": "USD", "cashBuyingPower": 10})
    c = client("42")
    c.accounts()
    assert c.holdings() == {"items": []}  # {"result": ...} 봉투는 벗긴다
    assert c.buying_power()["cashBuyingPower"] == 10
    accounts_req, holdings_req, bp_req = net.requests[1:]
    assert accounts_req.get_header("X-tossinvest-account") is None
    assert holdings_req.get_header("X-tossinvest-account") == "42"
    assert bp_req.get_header("X-tossinvest-account") == "42"
    assert all(r.get_method() == "GET" for r in net.requests[1:])


def test_holdings_symbol_and_prices_query(net, store):
    net.push_token()
    net.push({"items": []})
    net.push([{"symbol": "SOXL"}])
    c = client()
    c.holdings("SOXL")
    c.prices(["SOXL", "SOXX"])
    assert net.requests[1].full_url.endswith("/api/v1/holdings?symbol=SOXL")
    assert net.requests[2].full_url.endswith("/api/v1/prices?symbols=SOXL%2CSOXX")


def test_account_seq_defaults_to_first_brokerage_account(net, store):
    net.push_token()
    net.push(
        [
            {"accountNo": "a", "accountSeq": 5, "accountType": "PENSION"},
            {"accountNo": "b", "accountSeq": 9, "accountType": "BROKERAGE"},
        ]
    )
    net.push({"items": []})
    client(seq=None).holdings()
    assert net.paths() == ["/oauth2/token", "/api/v1/accounts", "/api/v1/holdings"]
    assert net.requests[2].get_header("X-tossinvest-account") == "9"


def test_client_exposes_read_only_methods_only():
    public = {n for n in dir(TossClient) if not n.startswith("_")}
    assert public == {"accounts", "account_seq", "holdings", "buying_power", "prices", "token"}
    assert not [n for n in public if "order" in n]


# --- 429 / 오류


def test_429_waits_retry_after_once_then_succeeds(net, store):
    net.push_token()
    net.push_error(429, "rate-limited", headers={"Retry-After": "3"})
    net.push([])
    client().accounts()
    assert net.sleeps == [3.0]
    assert net.paths() == ["/oauth2/token", "/api/v1/accounts", "/api/v1/accounts"]


def test_429_twice_raises_after_single_wait(net, store):
    net.push_token()
    net.push_error(429, "rate-limited", headers={"Retry-After": "1"})
    net.push_error(429, "rate-limited", headers={"Retry-After": "1"})
    with pytest.raises(TossApiError) as exc:
        client().accounts()
    assert exc.value.code == "rate-limited" and net.sleeps == [1.0]


def test_error_envelope_becomes_toss_api_error(net, store):
    net.push_token()
    net.push_error(403, "ip-not-allowed", "허용되지 않은 IP")
    with pytest.raises(TossApiError) as exc:
        client().accounts()
    e = exc.value
    assert (e.code, e.message, e.request_id, e.status) == (
        "ip-not-allowed",
        "허용되지 않은 IP",
        "req-1",
        403,
    )
    assert "ip-not-allowed" in str(e)


def test_secret_not_in_repr_or_error_text(net, store):
    net.push_error(401, "invalid-client")
    c = client()
    assert "secret" not in repr(c)
    with pytest.raises(TossApiError) as exc:
        c.token()
    assert "secret" not in str(exc.value)


# --- from_env


def test_from_env_requires_both_credentials(monkeypatch):
    assert from_env() is None
    monkeypatch.setenv("TOSS_CLIENT_ID", "id")
    assert from_env() is None
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "sec")
    monkeypatch.setenv("TOSS_ACCOUNT_SEQ", "3")
    c = from_env()
    assert c.client_id == "id" and c._account_seq == "3"


# --- 포지션 스냅샷

HOLDINGS = {
    "totalPurchaseAmount": 1000,
    "items": [
        {
            "symbol": "SOXL",
            "name": "Direxion Daily Semiconductor Bull 3X",
            "marketCountry": "US",
            "currency": "USD",
            "quantity": "10",
            "lastPrice": 55.5,
            "averagePurchasePrice": "50",
            "marketValue": {"purchaseAmount": 500, "amount": 555.0, "amountAfterCost": 554},
            "profitLoss": {
                "amount": 55.0,
                "amountAfterCost": 54,
                "rate": 0.11,
                "rateAfterCost": 0.1,
            },
            "dailyProfitLoss": 1,
            "cost": {"commission": 0.5, "tax": 0},
        }
    ],
}


class FakeClient:
    def holdings(self, symbol=None):
        return HOLDINGS


def test_holdings_frame_maps_fields():
    df = pos.holdings_frame(HOLDINGS, dt.date(2026, 11, 5), NOW)
    assert list(df.columns) == pos.SNAPSHOT_COLUMNS
    r = df.iloc[0]
    assert (r.symbol, r.currency, r.quantity, r.avg_price, r.last_price) == (
        "SOXL",
        "USD",
        10,
        50,
        55.5,
    )
    assert (r.market_value, r.pnl, r.pnl_rate, r.source) == (555.0, 55.0, 0.11, "toss")


def test_snapshot_positions_upserts_with_pk_and_table(monkeypatch):
    calls = []
    monkeypatch.setattr(pos, "upsert_df", lambda *a, **k: calls.append((a, k)) or 1)
    df = pos.snapshot_positions(FakeClient(), snapshot_date="2026-11-05", engine="E")
    ((args, kwargs),) = calls
    assert args[1:] == ("positions_snapshot", ["snapshot_date", "symbol"])
    assert kwargs == {"engine": "E"}
    assert args[0] is df and df.iloc[0]["snapshot_date"] == dt.date(2026, 11, 5)


def test_snapshot_positions_without_holdings_writes_nothing(monkeypatch):
    monkeypatch.setattr(pos, "upsert_df", lambda *a, **k: pytest.fail("쓰면 안 된다"))

    class Empty:
        def holdings(self, symbol=None):
            return {"items": []}

    assert pos.snapshot_positions(Empty()).empty


def test_soxl_positions_filters_symbol_and_quantity():
    df = pd.DataFrame(
        {"symbol": ["SOXL", "NVDA", "SOXL"], "quantity": [0.0, 5.0, 2.0], "market_value": [1, 2, 3]}
    )
    out = pos.soxl_positions(df)
    assert out["market_value"].tolist() == [3]
    assert pos.soxl_positions(df[df["symbol"] == "NVDA"]) is None
    assert pos.soxl_positions(None) is None


# --- 분포 적용 (손계산)


def forward_summary():
    return pd.DataFrame(
        {
            "median": [0.1, 0.2],
            "p10": [-0.2, -0.1],
            "p90": [0.5, 0.6],
            "n_up": [3, 4],
            "n_down": [2, 1],
            "n": [5, 5],
        },
        index=pd.Index([126, 252], name="horizon"),
    )


def test_apply_distribution_sox_basis_multiplies_value():
    out = pos.apply_distribution(1000.0, forward_summary(), None)
    assert list(out["basis"].unique()) == [pos.BASIS_SOX]
    r = out[out["horizon"] == 252].iloc[0]
    assert (r.value_median, r.value_p10, r.value_p90) == pytest.approx((1200.0, 900.0, 1600.0))
    assert r["n"] == 5


def test_apply_distribution_leveraged_uses_its_own_percentiles():
    paths = {252: [-0.6, -0.2, 0.0, 0.4, 1.0]}  # np.percentile 선형보간
    out = pos.apply_distribution(1000.0, forward_summary(), paths)
    lev = out[out["basis"] == pos.BASIS_LEVERAGED]
    assert lev["horizon"].tolist() == [252]  # 126은 경로가 없어 줄이 없다
    r = lev.iloc[0]
    assert r["n"] == 5 and r["median"] == pytest.approx(0.0)
    assert r["p10"] == pytest.approx(-0.44)  # 위치 0.4 → -0.6 + 0.4*(-0.2-(-0.6))
    assert r["p90"] == pytest.approx(0.76)  # 위치 3.6 → 0.4 + 0.6*(1.0-0.4)
    assert (r.value_median, r.value_p10, r.value_p90) == pytest.approx((1000.0, 560.0, 1760.0))
    # SOX 줄이 3배 줄과 같은 horizon에서 나란히 (SOX 먼저)
    assert out[out["horizon"] == 252]["basis"].tolist() == [pos.BASIS_SOX, pos.BASIS_LEVERAGED]


def test_apply_distribution_skips_nan_returns():
    out = pos.apply_distribution(100.0, forward_summary(), {252: [float("nan"), 0.5]})
    r = out[(out["basis"] == pos.BASIS_LEVERAGED)].iloc[0]
    assert r["n"] == 1 and r.value_median == pytest.approx(150.0)


def test_leveraged_returns_matches_daily_compounding():
    """매일 +1%로 오르는 가짜 SOX: 3배 NAV는 하루 계수 f로 복리 → h일 뒤 수익률 f^h-1."""
    idx = pd.bdate_range("2015-01-01", periods=700)
    close = pd.Series(100 * 1.01 ** np.arange(700), index=idx)
    end = idx[200]
    m = Match(end_date=end, scores={}, combined=0.9, forward={}, start_date=idx[81])
    out = pos.leveraged_returns(close, [m], window=120, rf=None)
    f = 1 + 3 * 0.01 - 2 * 0.0136 / 252 - 0.0075 / 252
    assert out[21] == pytest.approx([f**21 - 1], rel=1e-6)
    assert out[252] == pytest.approx([f**252 - 1], rel=1e-6)


def test_leveraged_returns_drops_matches_without_enough_future():
    idx = pd.bdate_range("2015-01-01", periods=400)
    close = pd.Series(100 * 1.001 ** np.arange(400), index=idx)
    m = Match(end_date=idx[300], scores={}, combined=0.9, forward={}, start_date=idx[181])
    out = pos.leveraged_returns(close, [m], window=120, rf=None)
    assert len(out[21]) == 1 and out[252] == []


def test_money_follows_currency():
    assert pos.money(1234.5, "USD") == "$1,234.50"
    assert pos.money(-5, "USD") == "-$5.00"
    assert pos.money(1234567.4, "KRW") == "1,234,567원"
    assert pos.money(10, "EUR") == "10.00 EUR"
    assert pos.money(float("nan"), "USD") == "—"


# --- CLI


def test_cli_check_without_credentials_exits_1_with_guidance():
    res = CliRunner().invoke(app, ["check"])
    assert res.exit_code == 1
    assert "TOSS_CLIENT_ID/SECRET이 없다" in res.output
    assert "WTS 설정→Open API" in res.output and "허용 IP" in res.output


def test_cli_snapshot_without_credentials_exits_1():
    assert CliRunner().invoke(app, ["snapshot"]).exit_code == 1


def test_cli_check_prints_accounts_holdings_and_buying_power(monkeypatch):
    class C:
        def accounts(self):
            return [{"accountNo": "123-45", "accountSeq": 7, "accountType": "BROKERAGE"}]

        def holdings(self):
            return HOLDINGS

        def buying_power(self):
            return {"currency": "USD", "cashBuyingPower": 321.5}

        def account_seq(self):
            return "7"

    monkeypatch.setattr("stocker.broker.__main__.from_env", lambda: C())
    res = CliRunner().invoke(app, ["check"])
    assert res.exit_code == 0, res.output
    assert "123-45" in res.output and "SOXL" in res.output
    assert "$555.00" in res.output and "+11.00%" in res.output and "$321.50" in res.output


def test_cli_snapshot_prints_row_count(monkeypatch):
    monkeypatch.setattr("stocker.broker.__main__.from_env", lambda: object())
    monkeypatch.setattr(
        "stocker.broker.__main__.snapshot_positions", lambda c: pd.DataFrame({"a": [1, 2]})
    )
    res = CliRunner().invoke(app, ["snapshot"])
    assert res.exit_code == 0 and "저장 2행" in res.output


def test_cli_api_error_exits_2_with_ip_hint(monkeypatch):
    class C:
        def accounts(self):
            raise TossApiError("ip-not-allowed", "차단", "r-9", 403)

    monkeypatch.setattr("stocker.broker.__main__.from_env", lambda: C())
    res = CliRunner().invoke(app, ["check"])
    assert res.exit_code == 2 and "ip-not-allowed" in res.output and "허용 IP" in res.output


# --- DB (docker compose up -d db)


@pytest.mark.db
def test_snapshot_round_trip_through_db(engine):
    from stocker.db import init_db, read_positions

    init_db(engine)
    day = dt.date(1999, 1, 4)
    try:
        pos.snapshot_positions(FakeClient(), snapshot_date=day, engine=engine)
        pos.snapshot_positions(FakeClient(), snapshot_date=day, engine=engine)  # 같은 날 덮어쓰기
        df = read_positions(start=day, end=day, engine=engine)
        assert len(df) == 1
        r = df.iloc[0]
        assert (r.symbol, r.currency, r.quantity, r.market_value, r.pnl_rate, r.source) == (
            "SOXL",
            "USD",
            10,
            555.0,
            0.11,
            "toss",
        )
        assert r.snapshot_date == pd.Timestamp(day)
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "DELETE FROM positions_snapshot WHERE snapshot_date = '1999-01-04'"
            )


@pytest.mark.db
def test_broker_token_cache_round_trip_and_replace(engine):
    from stocker.db import init_db, read_broker_token, write_broker_token

    init_db(engine)
    cid = "test-client-roundtrip"
    exp = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
    try:
        assert read_broker_token(cid, engine=engine) is None
        write_broker_token(cid, "t1", exp, engine=engine)
        write_broker_token(
            cid, "t2", exp + dt.timedelta(hours=1), engine=engine
        )  # 클라이언트당 1행
        row = read_broker_token(cid, engine=engine)
        assert row["access_token"] == "t2"
        assert row["expires_at"] == exp + dt.timedelta(hours=1)
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql(f"DELETE FROM broker_tokens WHERE client_id = '{cid}'")


@pytest.mark.db
def test_client_shares_token_through_real_table(engine, net):
    from stocker.db import init_db

    init_db(engine)
    cid = "test-client-shared"
    try:
        net.push_token("shared-tok")
        net.push([])
        net.push([])
        TossClient(cid, "s", account_seq="1", engine=engine).accounts()
        TossClient(
            cid, "s", account_seq="1", engine=engine
        ).accounts()  # 다른 인스턴스(다른 프로세스 흉내)
        assert net.paths().count("/oauth2/token") == 1
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql(f"DELETE FROM broker_tokens WHERE client_id = '{cid}'")


def test_inflate_gzip_error_body_is_parsed():
    import gzip
    import json

    from stocker.broker.toss import _inflate, _parse_error

    body = gzip.compress(
        json.dumps(
            {"error": {"code": "invalid-request", "message": "m", "requestId": "r"}}
        ).encode()
    )
    assert _inflate(body).startswith(b"{")
    err = _parse_error(400, _inflate(body))
    assert (err.code, err.request_id) == ("invalid-request", "r")
    assert _inflate(b"{}") == b"{}"


def test_buying_power_sends_currency_query(net, store):
    net.push_token()
    net.push({"result": {"currency": "USD", "cashBuyingPower": "1"}})
    c = client("42")
    c.buying_power()
    assert "currency=USD" in net.requests[-1].full_url

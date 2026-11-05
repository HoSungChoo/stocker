"""토스증권 오픈 API 클라이언트 (stdlib urllib만, 읽기 전용). 계약: docs/DATA_CONTRACT.md 14~15절.

토큰은 클라이언트당 1개만 유효하다(재발급하면 이전 토큰이 무효). 그래서 발급한 토큰은 DB
`broker_tokens`에 두고 scheduler·대시보드·CLI가 함께 쓴다. 각자 발급하면 서로를 끊는다.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from dotenv import dotenv_values

from stocker.db import get_engine, read_broker_token, write_broker_token

logger = logging.getLogger(__name__)

BASE_URL = "https://openapi.tossinvest.com"
TOKEN_PATH = "/oauth2/token"
ACCOUNT_HEADER = "X-Tossinvest-Account"
TOKEN_MARGIN = dt.timedelta(seconds=60)  # 만료 60초 전부터는 새로 받는다
DEFAULT_TTL_SECONDS = 86400  # 응답에 expires_in이 없을 때
MAX_RETRY_AFTER = 30.0  # Retry-After가 이보다 길면 그만큼만 기다린다
TIMEOUT = 15


class TossApiError(Exception):
    """토스 오류 봉투 `{"error": {"requestId", "code", "message"}}` 를 옮긴 예외."""

    def __init__(
        self, code: str, message: str, request_id: str | None = None, status: int | None = None
    ):
        super().__init__(
            f"{code}: {message}" + (f" (requestId={request_id})" if request_id else "")
        )
        self.code = code
        self.message = message
        self.request_id = request_id
        self.status = status


def _env(name: str) -> str | None:
    """프로세스 환경변수 → `.env` 순. 빈 문자열은 없음."""
    return os.environ.get(name) or dotenv_values(".env").get(name) or None


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _parse_error(status: int, body: bytes) -> TossApiError:
    try:
        err = json.loads(body.decode("utf-8")).get("error") or {}
    except (ValueError, AttributeError):
        err = {}
    return TossApiError(
        str(err.get("code") or f"http-{status}"),
        str(err.get("message") or "응답을 해석할 수 없다"),
        err.get("requestId"),
        status,
    )


def _unwrap(payload):
    """성공 응답이 `{"result": ...}` 로 감싸져 있으면 벗긴다 (감싸져 있지 않아도 그대로 받는다)."""
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


class TossClient:
    def __init__(
        self, client_id: str, client_secret: str, account_seq: str | None = None, engine=None
    ):
        self.client_id = client_id
        self._secret = client_secret
        self._account_seq = account_seq or None
        self._engine = engine
        self._memo: tuple[str, dt.datetime] | None = None  # DB를 못 쓸 때의 프로세스 내 캐시

    def __repr__(self) -> str:  # 비밀값이 로그에 남지 않게
        return f"TossClient(client_id={self.client_id!r})"

    # ------------------------------------------------------------------ 토큰

    def _db(self):
        if self._engine is None:
            self._engine = get_engine()
        return self._engine

    def _cached(self) -> tuple[str, dt.datetime] | None:
        """DB(없으면 프로세스 메모)의 토큰. 만료 여유(60초)가 남은 것만 돌려준다."""
        found = None
        try:
            row = read_broker_token(self.client_id, engine=self._db())
            if row:
                found = (row["access_token"], row["expires_at"])
        except Exception as exc:  # noqa: BLE001  (DB가 꺼져 있어도 조회 자체는 되게)
            logger.warning(
                "토큰 캐시를 읽지 못했다(%s). 이 프로세스 안에서만 재사용한다.", type(exc).__name__
            )
            found = self._memo
        if found and found[1] - TOKEN_MARGIN > _utcnow():
            return found
        return None

    def _issue(self) -> str:
        body = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self._secret,
            }
        ).encode()
        req = urllib.request.Request(
            BASE_URL + TOKEN_PATH,
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        data = self._send(req, retry_429=True)
        token = data["access_token"]
        issued = _utcnow()
        expires = issued + dt.timedelta(seconds=int(data.get("expires_in") or DEFAULT_TTL_SECONDS))
        self._memo = (token, expires)
        try:
            write_broker_token(self.client_id, token, expires, issued, engine=self._db())
        except Exception as exc:  # noqa: BLE001
            logger.warning("토큰 캐시를 저장하지 못했다(%s).", type(exc).__name__)
        return token

    def token(self) -> str:
        """DB 캐시 → 없거나 만료 임박이면 발급 → 저장."""
        cached = self._cached()
        return cached[0] if cached else self._issue()

    def _renew(self, rejected: str) -> str:
        """서버가 거절한 토큰을 바꾼다. 다른 프로세스가 이미 새로 발급했으면 그것을 쓴다
        (또 발급하면 방금 받은 상대의 토큰이 무효가 되어 서로 끊는 싸움이 된다)."""
        cached = self._cached()
        if cached and cached[0] != rejected:
            return cached[0]
        return self._issue()

    # ------------------------------------------------------------------ HTTP

    def _send(self, req: urllib.request.Request, retry_429: bool = True):
        """요청 한 번(429면 Retry-After만큼 기다린 뒤 1회 재시도). 오류는 TossApiError."""
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            if exc.code == 429 and retry_429:
                try:
                    wait = float(exc.headers.get("Retry-After", "1"))
                except (TypeError, ValueError):
                    wait = 1.0
                time.sleep(min(max(wait, 0.0), MAX_RETRY_AFTER))
                return self._send(req, retry_429=False)
            raise _parse_error(exc.code, body) from None
        except urllib.error.URLError as exc:
            raise TossApiError("network-error", str(exc.reason)) from None
        return json.loads(raw.decode("utf-8")) if raw else None

    def _get(self, path: str, params: dict | None = None, account: bool = False):
        url = BASE_URL + path + (("?" + urllib.parse.urlencode(params)) if params else "")
        token = self.token()
        for attempt in (0, 1):
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            if account:
                headers[ACCOUNT_HEADER] = self.account_seq()
            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                return _unwrap(self._send(req))
            except TossApiError as exc:
                revoked = exc.status == 401 or exc.code == "token-revoked"
                if not revoked or attempt:
                    raise
                token = self._renew(token)

    # ------------------------------------------------------------------ 엔드포인트 (조회만)

    def accounts(self) -> list[dict]:
        return self._get("/api/v1/accounts") or []

    def account_seq(self) -> str:
        """설정값 → 없으면 /accounts의 첫 BROKERAGE 계좌."""
        if self._account_seq is None:
            accounts = self.accounts()
            brokerage = [a for a in accounts if a.get("accountType", "BROKERAGE") == "BROKERAGE"]
            if not brokerage:
                raise TossApiError("no-account", "조회할 위탁(BROKERAGE) 계좌가 없다")
            self._account_seq = str(brokerage[0]["accountSeq"])
        return self._account_seq

    def holdings(self, symbol: str | None = None) -> dict:
        params = {"symbol": symbol} if symbol else None
        return self._get("/api/v1/holdings", params, account=True) or {}

    def buying_power(self) -> dict:
        return self._get("/api/v1/buying-power", account=True) or {}

    def prices(self, symbols: list[str]) -> list[dict]:
        if not symbols:
            return []
        return self._get("/api/v1/prices", {"symbols": ",".join(symbols)}) or []


def from_env(engine=None) -> TossClient | None:
    """TOSS_CLIENT_ID/SECRET이 둘 다 있을 때만 클라이언트를 만든다. 없으면 None."""
    client_id, secret = _env("TOSS_CLIENT_ID"), _env("TOSS_CLIENT_SECRET")
    if not (client_id and secret):
        return None
    return TossClient(client_id, secret, _env("TOSS_ACCOUNT_SEQ"), engine=engine)

# 데이터·모듈 계약 (Phase 0)

구현 에이전트는 이 문서대로 만든다. 바꾸고 싶은 점은 보고서에 제안만 한다.

## 1. 저장소 레이아웃

```
stocker/
  __init__.py
  config.py            # 환경변수 로딩 (pydantic-settings)
  db/
    __init__.py        # get_engine(), init_db(), upsert_df()
    schema.sql
    __main__.py        # python -m stocker.db init
  ingest/
    __init__.py
    prices.py          # fetch_prices(symbols, start, end) -> DataFrame
    fred.py            # fetch_fred(series_ids, start, end) -> DataFrame
    __main__.py        # python -m stocker.ingest prices|fred
  sim/
    __init__.py
    leveraged.py       # simulate_leveraged(...), calibrate(...)
    __main__.py        # python -m stocker.sim
tests/
  conftest.py
  fixtures/            # 소형 CSV 픽스처 (네트워크 없는 테스트용)
  test_db.py           # @pytest.mark.db
  test_ingest.py
  test_sim.py
docker-compose.yml
Dockerfile
pyproject.toml
.env.example
Makefile
```

## 2. 환경변수 (`.env`)

| 이름 | 기본값 | 설명 |
|---|---|---|
| `STOCKER_DB_URL` | `postgresql+psycopg://stocker:stocker@localhost:5432/stocker` | SQLAlchemy URL |
| `FRED_API_KEY` | 없음 | FRED 키. 없으면 fred 수집은 명확한 에러로 종료 |
| `STOCKER_DATA_DIR` | `./data` | 캐시·수작업 CSV 위치 |

`stocker/config.py`는 `Settings` (pydantic-settings) 하나를 노출한다: `settings.db_url`, `settings.fred_api_key`, `settings.data_dir`.

## 3. 테이블

### `prices_daily`
| 컬럼 | 타입 | 비고 |
|---|---|---|
| symbol | text | PK 일부. yfinance 표기 (`^SOX`, `SOXX`, `SOXL`, `SMH`) |
| date | date | PK 일부 |
| open, high, low, close, adj_close | double precision | |
| volume | bigint | |
| source | text | `yfinance` |
| ingested_at | timestamptz | default now() |

TimescaleDB hypertable on `date`. PK `(symbol, date)`.

### `macro_series`
| 컬럼 | 타입 | 비고 |
|---|---|---|
| series_id | text | PK 일부. FRED id (`DFF`, `DGS2`, `DGS10`, `T10Y2Y`, `DCOILWTICO`, `DTWEXBGS`, `VIXCLS`, `CPIAUCSL`) |
| date | date | PK 일부. 원본 빈도 그대로 (일/월) |
| value | double precision | 결측은 행 생략 |
| source | text | `fred` |
| ingested_at | timestamptz | |

### `events` (Phase 1에서 채움, 스키마만 지금 생성)
| 컬럼 | 타입 | 비고 |
|---|---|---|
| id | bigserial | PK |
| date | date | |
| kind | text | `fomc`, `cpi`, `jobs`, `earnings`, `policy`, `war`, `election` |
| title | text | |
| symbol | text null | earnings일 때 종목 |
| scheduled | boolean | 사전 공개된 일정이면 true |
| source | text | |
| meta | jsonb | |

## 4. 모듈 인터페이스

### `stocker.db`
```python
def get_engine() -> sqlalchemy.Engine            # settings.db_url
def init_db(engine=None) -> None                 # schema.sql 실행 (idempotent)
def upsert_df(df: pd.DataFrame, table: str, pk: list[str], engine=None) -> int
    # INSERT ... ON CONFLICT (pk) DO UPDATE. 반환: 영향 행 수
def read_prices(symbol: str, start=None, end=None, engine=None) -> pd.DataFrame
    # index=date(DatetimeIndex, tz-naive), columns=open,high,low,close,adj_close,volume
def read_macro(series_id: str, start=None, end=None, engine=None) -> pd.Series
```

### `stocker.ingest.prices`
```python
DEFAULT_SYMBOLS = ["^SOX", "SOXX", "SOXL", "SMH"]
def fetch_prices(symbols: list[str], start="1990-01-01", end=None) -> pd.DataFrame
    # long format: columns = symbol,date,open,high,low,close,adj_close,volume,source
    # yfinance auto_adjust=False. 결측 행 제거. date는 tz-naive.
def ingest_prices(symbols=DEFAULT_SYMBOLS, start="1990-01-01", engine=None) -> int
```

### `stocker.ingest.fred`
```python
DEFAULT_SERIES = ["DFF","DGS2","DGS10","T10Y2Y","DCOILWTICO","DTWEXBGS","VIXCLS","CPIAUCSL"]
def fetch_fred(series_ids: list[str], start="1990-01-01", end=None) -> pd.DataFrame
    # long format: series_id,date,value,source
def ingest_fred(series_ids=DEFAULT_SERIES, start="1990-01-01", engine=None) -> int
```

### `stocker.sim.leveraged`
```python
def simulate_leveraged(
    index_returns: pd.Series,        # 일일 단순수익률, DatetimeIndex
    leverage: float = 3.0,
    expense_ratio: float = 0.0075,   # 연율, SOXL 총보수 근사
    financing_rate: pd.Series | float = 0.0,  # 연율. Series면 날짜 정렬 후 ffill
    financing_spread: float = 0.0,   # 연율. 금융비용에 더하는 스프레드
    start_value: float = 100.0,
    rate_scale: Literal["auto","percent","decimal"] = "auto",
    # FRED DFF는 percent 단위(5.33 = 5.33%). CLI는 항상 "percent"를 넘긴다.
    # "auto"는 1.0 초과 값을 percent로 간주하므로 저금리 구간(2021)에서 오판할 수 있다.
) -> pd.Series                       # NAV 경로
    # 일일: nav_t = nav_{t-1} * (1 + L*r_t - (L-1)*(rf_t+spread)/252 - ER/252)
    # 거래일 수 252 고정. nav가 0 이하로 가면 0에서 멈춘다.

def calibrate(
    index_returns: pd.Series,
    actual_nav: pd.Series,           # 실제 SOXL 조정종가
    leverage: float = 3.0,
    expense_ratio: float = 0.0075,
    financing_rate: pd.Series | float = 0.0,
    rate_scale: Literal["auto","percent","decimal"] = "auto",
) -> CalibrationResult
    # dataclass: financing_spread, annual_tracking_error, daily_corr, n_days,
    #            mean_annual_drift (연율화된 로그수익률 차이 평균, sim - actual)
    # financing_spread를 1차원 최적화해 로그 NAV 오차 최소화

def forward_returns(nav: pd.Series, horizons=(21, 63, 126, 252)) -> pd.DataFrame
    # 각 날짜 기준 h일 후 수익률. 컬럼명 `fwd_{h}`. Phase 1에서 재사용.
```

### CLI
```
python -m stocker.db init
python -m stocker.ingest prices [--symbols ^SOX,SOXX] [--start 1990-01-01]
python -m stocker.ingest fred [--series DFF,DGS10] [--start 1990-01-01]
python -m stocker.sim --index SOXX --leverage 3 --start 2022-01-01 [--end ...] [--compare SOXL] [--rf DFF]
    # DB에서 읽어 시뮬. --compare가 있으면 calibrate 결과와 추적오차를 표로 출력.
    # --rf DFF 권장. rf=0이면 상수 스프레드가 0~5% 금리 변동을 못 따라가 연 +1.2% 드리프트가 남는다.
    # 검증값(SOXX→SOXL 2021–2025, DFF 사용): 스프레드 +1.36%, 연 추적오차 4.75%, 일일 상관 0.999, 드리프트 ≈ 0.
    # SOXX 조정종가를 쓰면 스프레드에 SOXX 배당·보수(0.35%)가 섞인다. 허용 범위.
    # DB가 없으면 --source yfinance 로 직접 받아 실행 가능해야 한다.
```

## 5. 의존성 (pyproject)

runtime: pandas, numpy, yfinance, fredapi, sqlalchemy>=2, psycopg[binary], pydantic-settings, typer, python-dotenv, scipy
dev: pytest, ruff

## 6. docker compose

- `db`: `timescale/timescaledb:latest-pg16`, port 5432, user/pass/db = stocker/stocker/stocker, named volume.
- `app`: 저장소 `Dockerfile` (python:3.12-slim + uv). 기본 명령은 없음. `docker compose run --rm app python -m stocker.ingest prices` 형태로 사용.
- `app`은 `.env`를 읽고 `STOCKER_DB_URL` 호스트를 `db`로 덮어쓴다.

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

`stocker/config.py`는 `Settings` (pydantic-settings) 하나를 노출한다: `settings.db_url`, `settings.fred_api_key`, `settings.data_dir`. 코드에서는 `settings.fred_key`(빈 문자열을 `None`으로 바꿔 주는 프로퍼티)를 쓴다.

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
    # yfinance auto_adjust=False. 결측 행 제거. date는 tz-naive. end는 그날 포함.
    # 데이터를 못 받은 심볼은 경고 후 건너뛴다(yfinance는 실패 시 빈 결과를 줄 수 있다).
def store_prices(df: pd.DataFrame, engine=None) -> int     # prices_daily upsert
def ingest_prices(symbols=DEFAULT_SYMBOLS, start="1990-01-01", engine=None) -> int
```

### `stocker.ingest.fred`
```python
DEFAULT_SERIES = ["DFF","DGS2","DGS10","T10Y2Y","DCOILWTICO","DTWEXBGS","VIXCLS","CPIAUCSL"]
def fetch_fred(series_ids: list[str], start="1990-01-01", end=None) -> pd.DataFrame
    # long format: series_id,date,value,source. settings.fred_key가 None이면 RuntimeError.
def store_fred(df: pd.DataFrame, engine=None) -> int       # macro_series upsert
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

---

# Phase 1 계약: 산업 사이클 데이터 + 유사도 엔진

## 7. 산업 사이클 데이터 (`stocker.ingest.semi`) — Sonnet 세션

### 7.1 테이블 `semi_cycle`
| 컬럼 | 타입 | 비고 |
|---|---|---|
| metric | text | PK 일부. 아래 목록 |
| date | date | PK 일부. 월말(월간) 또는 분기말(분기) |
| value | double precision | |
| source | text | `wsts`, `yfinance` |
| ingested_at | timestamptz | |

metric 목록 (원시값만 저장, YoY 등 파생값은 엔진이 계산):
- `wsts_sales_world_usd_m` — WSTS 월간 전세계 반도체 매출(백만 달러, 3개월 이동평균 기준 공식 발표값). 1986년부터.
- `mu_revenue_usd_m` — 마이크론 분기 매출(백만 달러). yfinance `MU` quarterly financials. 가능한 범위만.
- `mu_gross_margin` — 마이크론 분기 매출총이익률(0~1).

### 7.2 시드 파일
- `seed/wsts_sales_world.csv` — 컬럼 `date,value`. WSTS "Historical Billings Report"(공개 Excel)를 1회 파싱해 커밋한다. 파싱 스크립트는 `scripts/parse_wsts.py`로 남긴다(런타임 의존성 추가 금지, 1회용은 `uv run --with openpyxl`).
- `seed/*.csv`는 **git에 올리지 않는다**(WSTS 자료 재배포 제한 가능성). `make seed`(= `uv run --with openpyxl python scripts/parse_wsts.py`)로 로컬에서 생성한다. 스크립트만 추적한다.
- `mu_*` 두 metric은 yfinance가 최근 5분기만 주므로 과거 국면 비교에는 쓰이지 않는다(참고용). 값의 신뢰성도 미확인.

### 7.3 인터페이스
```python
# stocker/ingest/semi.py
def load_wsts_seed() -> pd.DataFrame            # seed CSV → long format (metric,date,value,source)
def fetch_mu_financials() -> pd.DataFrame        # yfinance MU → long format, 두 metric
def fetch_semi() -> pd.DataFrame                 # 위 둘을 합친 long format
def store_semi(df, engine=None) -> int           # semi_cycle upsert
def ingest_semi(engine=None) -> int              # fetch_semi → store_semi
# stocker/db  (이 세션이 추가)
def read_cycle(metric: str, start=None, end=None, engine=None) -> pd.Series
```
CLI: `python -m stocker.ingest semi`.

## 8. 유사도 엔진 (`stocker.similarity`) — Opus 세션

### 8.1 원칙
- **룩어헤드 금지**: `as_of` 날짜의 특징은 그 날짜 이하의 데이터만으로 계산한다. z-score의 평균·표준편차도 `as_of`까지의 expanding 통계를 쓴다(전체 기간 통계 금지).
- 모든 계산 함수는 DataFrame/Series를 받는 순수 함수. DB 접근은 `loaders.py` 하나에만.
- 점수는 모두 **0~1, 1이 가장 닮음**. 레이어마다 거리 → 후보 전체에서의 백분위 순위로 바꿔 비교 가능하게 한다.

### 8.2 설정
```python
@dataclass
class SimilarityConfig:
    window: int = 120  # 거래일. 60/120/250 지원
    top_k: int = 5
    min_gap_days: int = 250  # 선택된 국면끼리, 그리고 as_of와의 최소 간격
    weights: dict = {"price_shape": 0.5, "macro": 0.3, "semi_cycle": 0.2}
    horizons: tuple = (21, 63, 126, 252)
    macro_series: tuple = ("DFF", "DGS10", "T10Y2Y", "DCOILWTICO", "DTWEXBGS", "VIXCLS")
    change_days: int = 126  # 매크로 "방향" 특징의 기간
    cycle_lag_days: int = (
        45  # 산업 사이클 월간 값의 발표 지연. WSTS 월말값은 다음 달 중순 공개되므로
    )
    # 날짜를 45일 뒤로 밀어 룩어헤드를 막는다
```

### 8.3 레이어
**price_shape** — 입력: SOX 종가(`^SOX`, `close`).
- 창 벡터: 창 시작 대비 누적 로그수익률(길이 `window`), 창 내부 z-score.
- 보조 특징: 창 끝 시점의 전고점 대비 낙폭(as_of까지의 최고가 기준), 낙폭 지속 거래일.
- 거리: 유클리드로 후보 상위 300개를 먼저 추린 뒤 DTW(Sakoe-Chiba 밴드 10%)로 재정렬. 낙폭 특징은 절대차로 가중 합산(가중치 0.2).
- 후보 끝 날짜 t는 `t <= as_of - min_gap_days`.

**macro** — 입력: FRED 시리즈 dict.
- 특징(시리즈별 2개): 수준의 expanding z-score, `change_days` 변화량의 expanding z-score.
- 거리: 코사인. 결측 시리즈는 제외하고 남은 차원으로 계산.
- macro 데이터가 전혀 없으면(FRED 키 없음) 레이어 점수 NaN, 가중치는 남은 레이어로 재정규화하고 결과에 `layers_missing`로 표시.

**semi_cycle** — 입력: `read_cycle` 시리즈들.
- 특징: `wsts_sales_world_usd_m`의 YoY(%), YoY의 6개월 변화, `mu_gross_margin`(있으면). 월간 값을 거래일로 ffill.
- 거리: 표준화 유클리드. 없으면 macro와 같은 방식으로 NaN 처리.

### 8.4 결과
```python
@dataclass
class Match:
    end_date: pd.Timestamp
    scores: dict[str, float]  # 레이어별 0~1
    combined: float
    forward: dict[int, float]  # horizon → 이후 수익률(SOX). 미래 데이터가 부족하면 NaN


@dataclass
class SimilarityResult:
    as_of: pd.Timestamp
    config: SimilarityConfig
    matches: list[Match]  # top_k, combined 내림차순, min_gap 적용
    n_candidates: int
    layers_missing: list[str]
    forward_summary: pd.DataFrame  # index=horizon, columns=median,p10,p90,n_up,n_down,n
```
```python
def build_price_features(close: pd.Series, window: int) -> pd.DataFrame
def build_macro_features(series: dict[str, pd.Series], change_days: int) -> pd.DataFrame
def build_cycle_features(series: dict[str, pd.Series]) -> pd.DataFrame
def score(as_of, close, macro, cycle, config) -> SimilarityResult         # 순수
def run(as_of, config=SimilarityConfig(), engine=None) -> SimilarityResult # loaders 사용
def walk_forward(as_of_dates: list, config, engine=None) -> pd.DataFrame   # 각 as_of의 forward_summary 모음
```
CLI: `python -m stocker.similarity --as-of 2026-11-04 [--window 120] [--top-k 5] [--weights 0.5,0.3,0.2]`
표 출력: 상위 K 국면(끝 날짜, 레이어 점수, 종합, 이후 1/3/6/12개월 수익률) + forward_summary + `n_candidates`, `layers_missing`.

### 8.5 필수 테스트
- 룩어헤드 테스트: `as_of=2018-12-20`에 대해 DB 전체로 돌린 결과와 `as_of` 이후 데이터를 잘라낸 입력으로 돌린 결과가 동일해야 한다.
- min_gap 테스트: 선택된 끝 날짜끼리 간격이 모두 `min_gap_days` 이상.
- 합성 데이터 테스트: 알려진 패턴을 심어 두면 price_shape가 그 날짜를 1위로 찾는다.
- 레이어 결측 테스트: macro 없이도 실행되고 `layers_missing=["macro"]`.

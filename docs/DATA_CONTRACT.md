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

TimescaleDB hypertable on `date`, **`chunk_time_interval = 5 years`**(모든 hypertable 공통. 기본값 7일을 쓰면 일별 32년 = 수천 조각이 되어 읽기마다 락 한도를 넘긴다). PK `(symbol, date)`.

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

---

# Phase 2 계약: 이벤트 타임라인 + 대시보드

## 9. 이벤트 데이터 (`stocker.ingest.events`) — Sonnet 세션

### 9.1 원칙
- 이벤트는 **맥락 표시용**이다. 유사도 점수에 넣지 않는다.
- `scheduled=true`는 사전에 공개된 일정(FOMC, CPI, 실적일). `false`는 사후 기록(정책·전쟁·선거).
- 테이블은 Phase 0의 `events`(계약 3절) 그대로. `kind` 값: `fomc`, `cpi`, `jobs`, `earnings`, `policy`, `war`, `election`.
- 중복 방지: `(date, kind, coalesce(symbol,''), title)` 유니크 인덱스 `events_uniq`. 적재는 `upsert_df`(PK 기준)가 아니라 `events.py`에서 이 인덱스 기준 `ON CONFLICT DO UPDATE`로 직접 한다(실적 `meta`가 발표 후 갱신되도록).

### 9.2 소스
| kind | 소스 | 범위 | 방법 |
|---|---|---|---|
| fomc | 연준 FOMC 일정 페이지(현재·과거 연도) | 1994~ | stdlib `urllib` + `html.parser`/정규식. 외부 파싱 라이브러리 추가 금지. 막히면 `seed/events/fomc.json`에 수작업 목록을 만들고 출처 URL을 적는다 |
| cpi, jobs | FRED 릴리스 일정 API(발표 주체는 BLS. BLS 사이트는 자동 접속을 403으로 막아 우회하지 않음) | 1994~ 올해·내년 예정 | `FRED_API_KEY` 필요. 없으면 경고 후 건너뜀. 근사 날짜 생성은 **하지 않는다** |
| earnings | yfinance `Ticker(sym).earnings_dates` | 최근 ~2년 + 예정 | 심볼: NVDA, TSM, MU, AMD, ASML, AVGO, INTC. `symbol` 컬럼에 저장 |
| policy, war, election | `seed/events/policy.json` 수작업. 시장 충격(닷컴 붕괴, 리먼, 코로나 등)은 `policy`에 두고 `meta.category="shock"`로 구분 | 1994~ | 최소 항목: 2000 닷컴 붕괴 시작, 2001 9·11, 2008 리먼, 2011 동일본 지진(반도체 공급), 2018 미중 관세 1차, 2019 일본 수출규제, 2020 코로나, 2022 CHIPS법·10월 대중 수출규제, 2023 10월 수출규제 강화, 2024·2020·2016·2012·2008 미 대선, 2022 우크라이나 전쟁, 2025 관세. 각 항목에 `title`, `kind`, `date`, `meta.source`(URL) |

### 9.3 인터페이스
```python
# stocker/ingest/events.py
def fetch_fomc() -> pd.DataFrame            # long: date,kind,title,symbol,scheduled,source,meta
def fetch_bls() -> pd.DataFrame
def fetch_earnings(symbols=EARNINGS_SYMBOLS) -> pd.DataFrame
def load_policy_seed() -> pd.DataFrame
def ingest_events(engine=None) -> int       # 넷을 합쳐 upsert
# stocker/db (이 세션이 추가)
def read_events(start=None, end=None, kinds: list[str] | None = None, engine=None) -> pd.DataFrame
    # columns: date,kind,title,symbol,scheduled,source,meta. date 오름차순
```
CLI: `python -m stocker.ingest events [--only fomc,policy]`. kind별 행 수·범위 출력.

## 10. 대시보드 (`stocker.web`) — Sonnet 세션

### 10.1 스택
- Streamlit + Plotly. 의존성 `streamlit`, `plotly` 를 pyproject runtime에 추가(이 세션만 pyproject·uv.lock 수정).
- docker compose에 `web` 서비스 추가: `app` 이미지 재사용, 명령 `streamlit run stocker/web/app.py --server.port 8501 --server.address 0.0.0.0`, 포트 8501, `depends_on: db`, env `STOCKER_DB_URL` 호스트 `db`.
- 로컬 실행: `uv run streamlit run stocker/web/app.py`. Makefile `web` 타깃 추가.

### 10.2 화면 구성 (위→아래, 한 페이지)
1. **헤더**: 기준일(as_of), `n_candidates`, `layers_missing`(있으면 경고 배너: "매크로 레이어 꺼짐 — FRED 키 필요"), 표본 수 `n`.
2. **사이드바 컨트롤**: as_of(날짜, 기본 DB 마지막 날), window(60/120/250), top_k(3~10), 가중치 슬라이더 3개(합 1로 정규화), cycle_lag_days, "SOXL 시뮬 보기" 토글, 이벤트 종류 다중 선택, "DB 다시 읽기"(캐시 비움) 버튼.
3. **종합 결과 카드**: 상위 K 표(구간, 레이어별 점수, 종합, fwd 1/3/6/12m). 레이어 점수는 항상 분리 표시.
4. **겹친 차트**: 현재 창(굵은 선)과 상위 K 과거 창(얇은 선)을 창 시작=100으로 정규화해 겹친다. x축은 "창 시작 후 거래일"(0~window), 과거 창은 window 이후 252일까지 연장(점선)해 "그 뒤 어떻게 됐나"를 같이 보여준다. 현재 창은 window까지만.
5. **이벤트 마커**: 위 차트에 `read_events`로 가져온 이벤트를 kind별 아이콘/색으로 찍는다. 현재 창과 각 과거 창 모두. 호버에 title. 이벤트가 없거나 `read_events`가 없으면(ImportError) 마커 없이 그린다.
6. **이후 수익 분포**: `forward_summary` 표 + horizon별 박스/점 차트(n이 작으므로 개별 점을 모두 찍는다).
7. **SOXL 시뮬** (토글 시): 각 과거 창의 SOX 경로(window + 252일)를 `stocker.sim.simulate_leveraged`(L=3, ER 0.0075, rf는 DB `DFF` 있으면 사용, 없으면 0 + 경고, spread 0.0136)에 통과시켜 NAV 경로와 최종 수익률을 표시. 실제 SOXL 데이터가 있는 구간(2010~)은 실제 NAV도 점선으로 겹친다.
8. **정직성 푸터**: 고정 문구. "표본 n개. 유사 국면의 결과는 갈릴 수 있으며, 이 도구는 예측하지 않는다." + 상승/하락 개수.

### 10.3 구조
```
stocker/web/
  app.py            # Streamlit 엔트리. 레이아웃만
  data.py           # run(), read_prices, read_events, sim 호출을 모아 캐시(@st.cache_data, ttl 1h)
  charts.py         # 순수 함수. DataFrame 변환(build_overlay, attach_events, build_sim 등)과 Plotly Figure 생성. streamlit 미import
tests/test_web.py   # charts.py 함수가 합성 데이터로 Figure를 만드는지(렌더링 없이). streamlit 미import
```
- `data.py`의 DB 접근은 `stocker.db`·`stocker.similarity.loaders`만 통해서. 엔진 내부를 재구현하지 않는다.
- 한국어 UI. 용어는 첫 등장에 짧은 툴팁(`help=`)으로 풀이: 레버리지 감가, 추적오차, DTW, z-score.

---

# Phase 3 계약: 아침 보고서 + 스케줄러

## 11. 범위 결정
- **과금 없는 구성이 기본.** 보고서는 템플릿(Jinja2)으로 만든다. LLM 요약은 `ANTHROPIC_API_KEY`가 설정된 경우에만 켜지는 선택 기능이며, 기본은 꺼짐. 키 설정은 사용자 결정(유료).
- **토스증권 계좌 연동은 보류.** 사용자가 토스 WTS에서 API 키를 발급한 뒤 별도 Phase로 진행한다.
- **전송 채널**: 파일(`data/reports/`)이 기본. 이메일(SMTP)·텔레그램은 환경변수가 모두 있을 때만 활성.

## 12. 보고서 (`stocker.report`) — Sonnet 세션

### 12.1 환경변수 (`.env.example`에 추가, 모두 선택)
| 이름 | 설명 |
|---|---|
| `REPORT_TIME` | 매일 실행 시각, KST `HH:MM`. 기본 `07:30` |
| `REPORT_WINDOWS` | 쉼표 구분 창 길이. 기본 `120,250` |
| `REPORT_LLM` | `on`일 때만 LLM 요약 활성. 기본 `off`. 키만으로는 켜지지 않는다(우발 호출 방지) |
| `OPENAI_API_KEY` | `REPORT_LLM=on`과 함께 있으면 LLM 요약 1단락 추가 (유료, 사용자 결정 2026-11-05) |
| `OPENAI_MODEL` | 기본 `gpt-6.1-sol` (고정). 공식 ID. `gpt-6-sol`은 구모델이므로 쓰지 않는다 |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `REPORT_EMAIL_TO` | 모두 있으면 이메일 전송 |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | 둘 다 있으면 텔레그램 전송(stdlib urllib). **형식**: (1) `sendMessage`로 짧은 요약(≤3,500자, 표 없음: 제목, 창별 상위 3 국면과 12개월 후 수익, 중앙값·상승/하락 개수, 이번 주 이벤트, LLM 요약, 대시보드 링크; parse_mode 없이 평문) (2) `sendDocument`로 전체 `.md` 파일 첨부. 텔레그램은 4,096자 제한이 있고 Markdown 표를 렌더링하지 못한다 |

### 12.2 테이블 `report_runs`
| 컬럼 | 타입 | 비고 |
|---|---|---|
| run_date | date | PK. 보고서 기준일(데이터 마지막 거래일) |
| created_at | timestamptz | |
| window | int | PK 일부 |
| result | jsonb | `SimilarityResult`를 직렬화(matches, forward_summary, layers_missing, weights_used) |
| report_md | text | 렌더된 Markdown(window별 섹션 포함 전체는 window=0 행에) |

### 12.3 인터페이스
```python
# stocker/report/pipeline.py
def refresh_data(engine=None) -> dict[str, int]      # prices(최근 30일만), fred(최근 60일), semi, events 순서로 수집. 실패한 단계는 건너뛰고 기록
def build_report(as_of=None, windows=(120, 250), engine=None) -> Report
    # Report: as_of, sections(window별 SimilarityResult + 전일 대비 변화), upcoming_events(7일), data_status, llm_summary(None 가능)
def compare_with_previous(result, previous_result) -> dict   # 상위 K 구성 변화(새로 진입/이탈), 종합 점수 변화, forward 중앙값 변화
def render_markdown(report) -> str                          # templates/report.md.j2
def render_html(report) -> str                              # templates/report.html.j2 (이메일용, 인라인 CSS)
def deliver(report, md, html) -> list[str]                  # 활성 채널 목록 반환. 파일은 항상: data/reports/YYYY-MM-DD.md/.html
def run_daily(engine=None) -> Report                         # refresh → build → render → store(report_runs) → deliver
# stocker/report/llm.py
def summarize(report) -> str | None
    # REPORT_LLM=on 이고 OPENAI_API_KEY가 있을 때만. openai SDK(Responses API), model=OPENAI_MODEL(기본 gpt-6.1-sol),
    # max_output_tokens 600. 프롬프트: 사실만, 투자 권고 금지, 한국어 4~6문장. 실패 시 None(보고서는 계속).
# stocker/report/scheduler.py
def main() -> None                                           # APScheduler BlockingScheduler, cron(화~토 REPORT_TIME, tz Asia/Seoul; 미국 장은 KST 다음날 새벽 마감), 시작 시 즉시 1회 실행 옵션 --run-now
```
CLI: `python -m stocker.report run [--as-of] [--no-deliver]`, `python -m stocker.report schedule [--run-now]`, `python -m stocker.report show [--date]`.

### 12.4 보고서 내용 (Markdown, 위→아래)
1. 제목: `SOX 국면 보고서 — YYYY-MM-DD`
2. 데이터 상태: 각 소스 마지막 날짜, 오늘 갱신 행 수, 꺼진 레이어
3. 창별 섹션(120, 250): 상위 K 표(구간, 레이어 점수, 종합, fwd 12m) + forward_summary + **전일 대비 변화**(새로 들어온/빠진 국면, 중앙값 변화)
4. 이번 주 예정 이벤트(7일, scheduled=true)
5. (선택) LLM 요약
6. 고정 푸터: 표본 n, 상승/하락 개수, "예측하지 않는다"
7. 대시보드 링크 `http://localhost:8501`

### 12.5 인프라
- 의존성: `jinja2`, `apscheduler>=3.10,<4`, `openai`(`anthropic`은 제거). 이 세션만 pyproject·uv.lock 수정.
- docker compose `scheduler` 서비스: `app` 이미지, 명령 `python -m stocker.report schedule`, `depends_on: db`, `restart: unless-stopped`, `TZ=Asia/Seoul`.
- Makefile: `report`(즉시 1회), `schedule`.
- 대시보드 `app.py` 상단에 "최신 보고서 보기" expander 추가(`report_runs` 최신 `report_md` 표시). 이 세션이 `stocker/web/app.py`를 이 목적에 한해 수정한다.

### 12.6 테스트
- 템플릿 렌더(합성 Report → Markdown에 필수 섹션 7개 존재), `compare_with_previous`(진입/이탈 계산), `deliver`가 채널 미설정 시 파일만 쓰는지, `summarize`가 키 없으면 None, 스케줄러 cron 표현이 `REPORT_TIME` 파싱대로인지. `@pytest.mark.db`: `run_daily(--no-deliver)` 후 `report_runs` 행 존재, 두 번 실행해도 PK 충돌 없이 갱신.

# Phase 6C 계약: 환경 지표 추가 · 체크리스트 패널 · 이벤트 마커 토글 · 환경 우선 기본값

호성님 결정(2026-11-06): 차트의 높낮이보다 전후 환경에 집중한다. 테스프레소 영상(1990년대 사례 비교)의 지표·체크리스트 방식을 체계화한다. "사례 직접 입력" 기능은 만들지 않는다(엔진이 고른 유사 국면만 쓴다). 유튜브 수집은 보류.
공통 규칙 `CLAUDE.md`, 기존 계약 `DATA_CONTRACT.md`·`PHASE5_CONTRACT.md`·`PHASE6_CONTRACT.md`.

## 28. 파일 경계 (세션 2개 동시)
| 세션 | 소유 파일 | 금지 |
|---|---|---|
| I 환경 지표 수집 (Sonnet) | `stocker/ingest/indicators.py`(신규), `stocker/ingest/fred.py`(DEFAULT_SERIES 추가만), `stocker/ingest/__main__.py`(`indicators` 커맨드 추가만), `scripts/parse_shiller.py`·`scripts/parse_ritter.py`(신규), `seed/indicators/*.csv`(git 미추적; `.gitignore`에 `seed/indicators/*.csv` 추가), `tests/test_indicators.py`, `Makefile`(`seed-indicators` 타깃 추가만) | `stocker/checklist/`, `stocker/api/`, `frontend/`, `stocker/similarity/` |
| C 체크리스트 + 마커 토글 (Sonnet) | `stocker/checklist/**`(신규), `stocker/api/**`(checklist 라우트·overlay events 필드), `frontend/**`, `tests/test_checklist.py`, `tests/test_api.py`(추가만) | `stocker/ingest/`, `stocker/similarity/`, `stocker/backtest/` |

환경 우선 기본값 전환(`stocker/similarity/config.py` `mode` 기본값 `regime`)과 regime 그룹에 새 지표를 넣는 일은 **6B 백테스트 세션 종료 후 상위 세션이** 한다. 두 세션 모두 `config.py`를 건드리지 않는다.

## 29. 환경 지표 (I)
모두 `macro_series` 테이블에 넣는다(series_id, date, value, source). 원 단위 그대로 저장(퍼센트는 퍼센트).

| series_id | 내용 | 출처 | 범위 | 비고 |
|---|---|---|---|---|
| `BAMLH0A0HYM2` | 하이일드 OAS(%p) | FRED | 1996-12~ 일간 | DEFAULT_SERIES에 추가 |
| `BAMLH0A3HYC` | CCC 이하 OAS(%p) | FRED | 1996-12~ 일간 | 〃 |
| `DGS3MO` | 3개월물(%) | FRED | 1981~ | 〃 (금리 방향 보조) |
| `CAPEX_IPS_GDP` | 정보처리장비·소프트웨어 민간고정투자 / 명목 GDP (비율) | FRED 두 시리즈의 비(분기). 정확한 ID는 세션이 FRED에서 확인해 보고에 적는다 | 1947~ 분기 | 분기말 날짜 |
| `SHILLER_CAPE` | 실러 CAPE | 실러 공개 엑셀(ie_data.xls) | 1881~ 월간 | 수동 다운로드 가능, 스크립트로 파싱. 출처·받은 날짜를 CSV 주석에 |
| `SHILLER_EP` | 후행 12개월 E/P (= E / P, 소수) | 〃 | 1871~ 월간 | 일드 스프레드 = SHILLER_EP − DGS10/100 |
| `IPO_FIRSTDAY` | 공모주 첫날 평균 수익률(%) | 제이 리터 교수 공개 데이터(월간 또는 연간) | 1980~ | 월간이 없으면 연간을 그 해 각 월말에 복제하고 `source`에 표시 |

인터페이스: `fetch_indicators() -> DataFrame`(long), `ingest_indicators(engine=None) -> int`, CLI `python -m stocker.ingest indicators`. 자동 다운로드가 막히면 수동 절차를 docstring에 적고 CSV는 반드시 만들어 둔다. 네트워크 실패는 시리즈 단위로 경고 후 건너뛴다.

## 30. 체크리스트 (C)
### 30.1 신호 규칙 (고정. `stocker/checklist/rules.py`)
각 신호는 `(id, group, label, rule, needs)`이고, 날짜 d에서 **d 이하의 데이터만으로** 켜짐/꺼짐/데이터 없음을 낸다.

| id | 그룹 | 규칙 |
|---|---|---|
| `bottom_10y_turn` | 바닥 | DGS10 ≤ (최근 60거래일 DGS10 최고) − 0.4 |
| `bottom_fed_cut` | 바닥 | 최근 30일 DFF 변화 ≤ −0.20%p |
| `bubble_inversion` | 버블 끝 | T10Y2Y < 0 |
| `bubble_yield_spread` | 버블 끝 | SHILLER_EP − DGS10/100 ≤ −0.027 |
| `bubble_ipo` | 버블 끝 | IPO_FIRSTDAY ≥ 50 |
| `heat_cape` | 과열 | SHILLER_CAPE ≥ 35 |
| `heat_sox_1y` | 과열 | SOX 252거래일 수익률 ≥ +100% |
| `crisis_hy` | 위기 | BAMLH0A0HYM2 ≥ 7.0 |
| `crisis_ccc` | 위기 | BAMLH0A3HYC ≥ 12.0 |

월간·분기 시리즈는 발표 지연을 고려해 날짜를 45일 뒤로 민다(WSTS와 같은 방식). 데이터가 없으면 `null`(화면 "데이터 없음").

### 30.2 인터페이스·API
```python
def evaluate(date, series: dict[str, pd.Series], close: pd.Series) -> list[Signal]   # Signal: id, group, label, on: bool|None, value, threshold
def evaluate_db(date, engine=None) -> list[Signal]
```
`GET /api/checklist?as_of=&window=&top_k=&mode=` → `{"now":[Signal...], "matches":[{"end":"2000-08-08","signals":[Signal...]}]}` (matches는 similarity 결과의 상위 K 창 끝 날짜 기준).

### 30.3 화면
- `/` 페이지 분포 아래, 평가표 위에 **체크리스트 패널**: 그룹 4개(바닥·버블 끝·과열·위기)를 열로, 신호를 행으로, "지금" 열과 상위 K 국면 열. 셀은 ● 켜짐 / ○ 꺼짐 / – 데이터 없음, 호버에 값과 임계값.
- 평가표 카드에 "그때 켜진 신호 / 지금 켜진 신호" 한 줄(배지).
- 고정 문구: "체크리스트는 점수에 쓰이지 않는다. 1990년대 사례에서 쓰인 신호를 규칙으로 옮긴 것이다."

## 31. 이벤트 마커 토글 (C)
- 겹친 차트 범례 옆에 종류별 버튼(FOMC · CPI·고용 · 실적 · 정책·전쟁·선거). 기본 전부 켜짐. 끄면 현재 창과 과거 창의 해당 마커가 모두 사라진다. 상태는 URL 쿼리(`markers=fomc,cpi`)에 반영해 새로고침해도 유지.
- 기존 `/events` 페이지의 종류 필터와 같은 kind 매핑을 쓴다.

## 32. 완료 기준
- `python -m stocker.ingest indicators` 후 `macro_series`에 7개 시리즈가 있고, 2000-03-10 기준 `bubble_inversion`·`bubble_yield_spread`·`bubble_ipo`가 모두 켜짐(영상의 주장 검증), 2026-11-04 기준 세 개 모두 꺼짐 또는 데이터 없음.
- `/` 페이지에 체크리스트 패널과 마커 토글이 보이고 동작한다.

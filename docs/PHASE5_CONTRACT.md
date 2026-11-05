# Phase 5 계약: React 페이지 (API + 설명 엔진 + 이벤트 영향)

목업: https://claude.ai/artifact/NtSpEFEzDRiJXx2xQAtiaR (다크모드, 페이지 3장: 국면 비교 / 이벤트 / 내 계좌). 개인 모드(로그인 없음).
공통 규칙은 `CLAUDE.md`, 기존 모듈 계약은 `DATA_CONTRACT.md`.

## 16. 파일 경계 (세션 3개 동시 진행)

| 세션 | 소유 파일 | 금지 |
|---|---|---|
| A 설명 엔진 + API (Opus) | `stocker/similarity/explain.py`, `stocker/api/**`, `tests/test_explain.py`, `tests/test_api.py`, `pyproject.toml`·`uv.lock`(fastapi, uvicorn, httpx 추가), `docker-compose.yml`(`api`, `ui` 서비스 추가; 기존 서비스 수정 금지) | `stocker/events/`, `frontend/`, `stocker/db/` |
| B 이벤트 영향 (Sonnet) | `stocker/events/**`(신규), `stocker/db/schema.sql`(`event_impact` 추가), `stocker/db/__init__.py`(`read_event_impact` 추가), `tests/test_event_impact.py` | `stocker/api/`, `frontend/`, `pyproject.toml` |
| C React 프론트 (Sonnet) | `frontend/**`(신규), `Dockerfile.web`, `Makefile`(`ui`, `ui-dev` 타깃), `README.md`(해당 줄), `.gitignore`(node_modules 등) | 파이썬 코드 전부, `docker-compose.yml` |

A는 B의 `read_event_impact`를 함수 안에서 lazy import하고, 없으면 빈 목록을 돌려준다. C는 17절 스키마의 예시 JSON을 `frontend/src/mocks/`에 두고 mock으로 먼저 개발한 뒤 실제 API로 전환한다.

## 17. API (`stocker/api`, FastAPI, 포트 8000, 프리픽스 `/api`)

- 계산을 복제하지 않는다: `stocker.similarity.loaders.run`, `explain`, `stocker.db.*`, `stocker.sim.simulate_leveraged`, `stocker.broker.positions.*`를 호출만 한다.
- 모든 날짜는 `YYYY-MM-DD` 문자열, 수익률은 **소수**(0.25 = +25%), 점수는 0~1. 결측은 `null`.
- 공통 쿼리: `as_of`(기본 DB 마지막 거래일), `window`(60|120|250, 기본 120), `top_k`(기본 5), `w_price,w_macro,w_cycle`(기본 0.5,0.3,0.2; 합으로 정규화), `cycle_lag`(기본 45).
- CORS: localhost 3000·5173 허용. 응답 캐시: 같은 쿼리 1시간(메모리).

```
GET /api/health → {"ok":true,"last_trading_day":"2026-11-04","layers_missing":[]}

GET /api/similarity → {
  "as_of":"2026-11-04","window":120,"top_k":5,"n_candidates":7813,
  "layers_missing":[],"weights_used":{"price_shape":0.5,"macro":0.3,"semi_cycle":0.2},
  "matches":[{"rank":1,"start":"2000-02-17","end":"2000-08-08",
              "scores":{"price_shape":1.0,"macro":0.86,"semi_cycle":0.96},"combined":0.95,
              "forward":{"21":0.151,"63":-0.216,"126":-0.331,"252":-0.385}}],
  "forward_summary":[{"horizon":21,"median":-0.048,"p10":-0.139,"p90":0.102,"n_up":2,"n_down":3,"n":5}]
}

GET /api/overlay → {
  "current":{"start":"2026-05-15","end":"2026-11-04",
             "points":[{"d":0,"v":100.0}],                  # d = 창 시작 후 거래일, v = 창 시작=100
             "events":[{"d":34,"date":"2026-07-01","kind":"fomc","title":"FOMC"}]},
  "matches":[{"rank":1,"start":"...","end":"...",
              "window_points":[{"d":0,"v":100.0}],
              "after_points":[{"d":120,"v":98.1}],         # 창 끝 이후 252거래일
              "events":[{"d":10,"date":"...","kind":"...","title":"..."}]}]
}

GET /api/explain?end=2000-08-08 (+ 공통 쿼리) → {
  "match":{"start":"2000-02-17","end":"2000-08-08","combined":0.95,"scores":{"price_shape":1.0,"macro":0.86,"semi_cycle":0.96}},
  "price":{"dtw_rank":1,"euclid_rank":3,
           "drawdown":{"then":-0.287,"now":-0.298},"drawdown_days":{"then":151,"now":135}},
  "macro":[{"series":"DFF","label":"기준금리","unit":"%","then":6.44,"now":3.88,"z_then":1.2,"z_now":0.4},
           {"series":"DGS10","label":"10년물","unit":"%"},
           {"series":"T10Y2Y","label":"장단기 스프레드","unit":"%p"},
           {"series":"DCOILWTICO","label":"WTI 유가","unit":"$"},
           {"series":"DTWEXBGS","label":"달러지수","unit":""},
           {"series":"VIXCLS","label":"VIX","unit":""}],
  "cycle":[{"metric":"wsts_sales_yoy","label":"반도체 매출 YoY","then":0.407,"now":1.443},
           {"metric":"wsts_yoy_chg6m","label":"YoY 6개월 변화","then":0.1,"now":0.3}],
  "forward":{"21":0.151,"63":-0.216,"126":-0.331,"252":-0.385}
}
  # z_then/z_now = 각 시점까지의 expanding z-score(룩어헤드 금지). 값이 없는 시리즈(2006년 이전 달러지수)는 then=null.

GET /api/events/upcoming?days=14 → [{"date":"2026-11-06","kind":"jobs","title":"고용보고서","symbol":null,"scheduled":true,"confirmed":true}]
GET /api/events/impact?limit=20&kinds=policy,war,election → [{"id":12,"date":"2001-09-11","kind":"war","title":"...","ret5":-0.323,"ret_1m":0.05,"ret_3m":0.1,"source":"https://..."}]   # |ret5| 내림차순
GET /api/events/{id}/path?days=30 → {"event":{"id":12,"date":"...","kind":"...","title":"..."},"points":[{"d":-30,"v":97.0},{"d":0,"v":100.0},{"d":30,"v":104.0}]}   # 이벤트 당일=100
GET /api/positions/latest → {"snapshot_date":"2026-11-05","items":[{"symbol":"SOXL","quantity":1,"avg_price":1,"last_price":1,"market_value":1,"pnl":1,"pnl_rate":0.1,"currency":"USD"}]} | null
GET /api/positions/apply (+ 공통 쿼리) → {"position_value":5182.32,"currency":"USD","n":5,
   "rows":[{"basis":"sox","horizon":252,"median":0.25,"p10":-0.247,"p90":0.525},{"basis":"lev3","horizon":252,"median":0.454,"p10":-0.778,"p90":1.229}]}
GET /api/report/latest → {"run_date":"2026-11-04","markdown":"..."}
```

### `stocker/similarity/explain.py` (A)
```python
@dataclass
class Explanation: ...  # 위 JSON과 같은 구조
def explain(as_of, match_end, close, macro, cycle, config) -> Explanation   # 순수. features.py의 같은 함수로 계산해 score()와 값이 일치해야 한다
def explain_db(as_of, match_end, config, engine=None) -> Explanation        # loaders 경유
```
필수 테스트: (1) `explain`의 drawdown·z-score가 `features.py` 결과와 동일, (2) 룩어헤드: as_of 이후 데이터를 잘라도 then/now 값 동일, (3) 달러지수 없는 2000년 구간 then=null.

## 18. 이벤트 영향 (`stocker/events`, B)

- 테이블 `event_impact(event_id bigint PK, ret5 double, ret_1m double, ret_3m double, computed_at timestamptz)`. `ret5` = 이벤트 직전 5거래일 종가 대비 직후 5거래일 종가. 이벤트일이 휴장이면 그 이전 마지막 거래일 기준. `ret_1m`/`ret_3m`은 이벤트일 종가 대비 21/63거래일 뒤.
- `compute_event_impact(kinds=("policy","war","election"), engine=None) -> int`(upsert), `event_path(event_id, days=30, engine=None) -> pd.Series`(당일=100, index=d).
- `stocker.db.read_event_impact(limit=20, kinds=None, engine=None) -> pd.DataFrame`(events 조인: id,date,kind,title,source,ret5,ret_1m,ret_3m; |ret5| 내림차순).
- CLI `python -m stocker.events impact`. `stocker/report/pipeline.py`는 건드리지 않는다(다음 단계에서 연결).

## 19. 프론트 (`frontend/`, C)

- Vite + React 18 + TypeScript + Tailwind + Recharts + TanStack Query + React Router. 다크모드 기본. 팔레트: 배경 `#0F1115`, 카드 `#171A21`, 테두리 `#262B35`, 본문 `#E8EAEE`, 보조 `#9AA3B2`, 현재선 `#5B8DEF`, 1~5위 `#F08A3E #3BBFAE #B07CD6 #E35D5D #9AA3B2`, 상승 `#4CC274`, 하락 `#F06A6A`. 글꼴 Inter + Noto Sans KR.
- 라우트 `/`(국면 비교), `/events`, `/my`. 목업 구성 요소를 그대로 구현:
  - `/`: 헤더 컨트롤(기준일, 창 60/120/250, top_k, 가중치 슬라이더 3개, 표본 n, 상승/하락, 레이어 상태 배너) → 겹친 차트(현재 굵은 선, 과거 창 실선+이후 점선, 범례 체크박스로 토글, 이벤트 마커 kind별 모양, 호버 툴팁) → 분포 4칸 → 평가표 카드(상위 3 펼침: 점수 막대 3개 + 근거 표 "그때/지금/읽는 법", 4~5위는 "근거 펼치기") → 정직성 푸터.
  - `/events`: 종류 필터 pill → 왼쪽 14일 일정(날짜·D+n·태그·제목·출처) → 오른쪽 영향 표(날짜·태그·사건·ret5·크기 막대, 행 클릭 선택) → 선택 이벤트 상세(전후 ±30일 경로 차트, "그때 국면 비교 열기" 링크 = `/?as_of=<date>`).
  - `/my`: 연결 상태 카드(권한·키 보관·마지막 조회·허용 IP·"지금 가져오기") → 포지션 표 → 분포 적용 표(SOX 기준/3배 레버리지 기준 × 창 2개) → 매매 복기(빈 상태 문구).
- "읽는 법" 열은 프론트에서 규칙으로 생성: 두 값의 상대 차이 10% 이내 "비슷", 그 이상이면 "그때가 높음/낮음", 부호가 다르면 "방향이 다름", then=null이면 "데이터 없음".
- API 베이스는 `VITE_API_BASE`(기본 `/api`). 로딩·오류·`layers_missing` 배너. 모바일 폭에서 표는 가로 스크롤, 컨트롤은 줄바꿈.
- `Dockerfile.web`: node 빌드 → nginx 정적 서빙, `/api` 경로를 `http://api:8000`으로 프록시. 포트 3000. compose `ui` 서비스 정의는 A가 추가한다(`build: {context: ., dockerfile: Dockerfile.web}`, `ports: 3000:3000`, `depends_on: api`).
- 테스트: Vitest + Testing Library. mock JSON으로 세 라우트가 렌더되고, 차트 선 개수·카드 개수·이벤트 행 수가 맞는지.
- Streamlit(`stocker/web`)은 유지. 완료 기준 충족 후 별도 결정으로 제거.

## 20. 완료 기준
- `docker compose up -d db api ui` 후 http://localhost:3000 세 라우트가 실제 데이터로 보인다.
- `/api/explain`의 then/now 값이 `python -m stocker.similarity` 및 `features.py`와 일치한다.
- 룩어헤드 테스트가 explain에도 통과한다.
- 모바일 폭(390px)에서 차트·표가 깨지지 않는다.

# Phase 6 계약: 적합 기준 · 평가표 코멘트 · 환경 우선 모드 · 백테스트

계획: `docs/PLAN_PHASE6.md`(승리 조건은 실험 전 고정). 공통 규칙 `CLAUDE.md`, 기존 계약 `DATA_CONTRACT.md`, `PHASE5_CONTRACT.md`.
호성님 지시(2026-11-05): "무조건 딱 맞는 결과를 찾으라는 말이 아니다. 현재 상황에 알맞은 과거 사례가 없으면 적용하지 않아도 된다. 평가표 하단에 왜 비슷한지 코멘트를 달아 달라."

## 21. 파일 경계 (세션 2개 동시)

| 세션 | 소유 파일 | 금지 |
|---|---|---|
| R 환경 우선 모드 + 적합 기준 + 백테스트 (Opus) | `stocker/similarity/regime.py`(신규), `stocker/similarity/fit.py`(신규, 적합 기준), `stocker/similarity/config.py`(`mode`, 적합 설정 추가), `stocker/similarity/engine.py`(적합 판정 결과를 `SimilarityResult`에 싣는 최소 변경), `stocker/similarity/loaders.py`(mode 분기), `stocker/similarity/__main__.py`(`--mode`), `stocker/backtest/**`(신규), `stocker/db/schema.sql`(`backtest_runs` 추가만), `stocker/db/__init__.py`(backtest read/write 추가만), `docs/BACKTEST_RESULTS.md`, `tests/test_regime.py`, `tests/test_fit.py`, `tests/test_backtest.py` | `stocker/similarity/explain.py`, `stocker/api/`, `frontend/` |
| K 평가표 코멘트 (Sonnet) | `stocker/similarity/explain.py`(`comment` 추가), `stocker/similarity/comment.py`(신규), `stocker/api/`(explain 응답에 `comment`, similarity 응답에 적합 필드 통과), `frontend/`(카드 하단 코멘트, "해당 없음" 상태, 모드 토글), `tests/test_comment.py`, `tests/test_api.py`(추가만) | `regime.py`, `fit.py`, `backtest/`, `config.py` |

K는 R의 `SimilarityResult.fit`(23절)을 **있으면 쓰고 없으면 None으로 처리**한다(동시 진행이므로).

## 22. FRED 시리즈 추가 (상위 세션이 직접)
`DEFAULT_SERIES`에 `M2SL`(월간 M2), `WALCL`(주간 연준 자산, 2002-12~) 추가. 실질금리 = `DFF` − `CPIAUCSL` YoY는 엔진에서 파생.

## 23. 적합 기준 ("알맞은 과거가 없으면 적용하지 않는다")

점수는 상대 점수라 1위는 항상 0.95 안팎이다. 그래서 **절대 기준**을 따로 둔다. 모든 조건은 원 단위(퍼센트포인트, 일수)로 적어 사용자가 읽을 수 있게 한다.

후보 국면마다 조건 5개를 검사한다 (`fit.py`, `FitCheck` dataclass: `conditions: list[{name, then, now, tolerance, passed}]`, `n_passed`, `status`):

| 조건 | 기준 |
|---|---|
| 낙폭 깊이 | 고점 대비 낙폭 차이 ≤ 8%p |
| 낙폭 기간 | 고점 이후 거래일 수 차이 ≤ 40% (긴 쪽 기준) |
| 금리 환경 | 기준금리 수준 차이 ≤ 2.0%p **그리고** 최근 6개월 금리 방향(인상/동결/인하) 같음 |
| 업황 방향 | WSTS 매출 YoY 부호 같음 |
| 변동성 | 60일 실현 변동성 차이 ≤ 40% (큰 쪽 기준) |

- `status`: `strong`(5개 충족), `ok`(3~4개), `weak`(2개 이하).
- `SimilarityResult`에 `fit: list[FitCheck]`(matches와 같은 순서), `applicable: bool`, `applicable_n: int` 추가. `applicable` = 상위 K 중 `ok` 이상이 **2개 이상**. `forward_summary`는 `ok` 이상인 국면만으로 계산하고 `n`은 그 수. `weak`만 있으면 `applicable=False`이고 `forward_summary`는 **기준선(전체 과거의 같은 horizon 분포)**으로 대체하되 `forward_summary_source="baseline"`로 표시한다.
- 화면·보고서·CLI는 `applicable=False`면 "현재와 충분히 닮은 과거 사례가 없다. 아래 분포는 전체 과거 기준이다"를 맨 위에 보여준다. 상위 K 카드는 그대로 보여주되 `weak` 배지를 단다.
- 백테스트에서도 같은 규칙을 쓴다(해당 없음 시점은 기준선 분포로 채점). 이렇게 해야 "적용하지 않는다"의 효과가 점수에 반영된다.
- 기준값(8%p, 40%, 2.0%p, 40%)은 이 문서에 고정한다. 백테스트 결과를 보고 바꾸지 않는다.

## 24. 환경 우선 모드 (`regime.py`)
`PLAN_PHASE6.md` 3절의 특징·가중치 그대로. `score_regime(as_of, close, macro, cycle, events, config) -> SimilarityResult`(기존 `score`와 같은 반환형). `config.mode = "shape" | "regime"`, CLI `--mode`, `loaders.run(..., mode=)`. 룩어헤드 금지(expanding 통계), 모양 점수는 기존 DTW 결과를 10%로.

## 25. 백테스트 (`stocker/backtest`)
- `python -m stocker.backtest run --modes baseline,shape,regime [--start 1999-01 --end 2025-10] [--horizon 252]`: 매월 말 거래일마다 각 모드 실행(적합 기준 포함), 결과 `backtest_runs(mode, as_of, horizon, median, p10, p90, n, applicable, realized)` upsert.
- `python -m stocker.backtest report`: `metrics.py`로 구간 점수(α=0.2: 폭 + (2/α)×구간 밖 거리), 적중률, 방향 일치를 전체·시대별(1999–2006, 2007–2012, 2013–2019, 2020–2025)로 집계해 `docs/BACKTEST_RESULTS.md`를 **생성**한다(표 + `PLAN_PHASE6.md` 1절 승리 조건의 기계적 판정 + "결론 없음" 가능).
- 성능: 320시점 × 3모드가 10분 이내. 입력(종가·매크로·사이클)은 한 번만 읽어 메모리에서 as_of로 자른다.
- 테스트: 구간 점수 손계산, 룩어헤드(임의 as_of에서 이후 데이터를 잘라도 결과 동일), 적합 기준 적용 시 baseline 대체 동작.

## 26. 평가표 코멘트 (`comment.py`, K)
- 입력: `Explanation`(그때/지금 값) + `FitCheck`(있으면). 출력: 한국어 2~4문장 `comment: str`. **규칙 기반, 결정적, 과금 없음.**
- 구조: (1) 닮은 점 한 문장 — 통과한 조건과 차이가 작은 지표를 원 값과 함께("낙폭이 -28.7% 대 -29.8%로 거의 같고, 둘 다 매출 급증기다"). (2) 다른 점 한 문장 — 차이가 큰 지표("다만 기준금리는 그때가 2.6%p 높았고 유가는 3배 차이라 돈의 환경은 다르다"). (3) 한 줄 판정 — `status`에 따라 "위치는 닮았지만 환경은 다른 사례" / "위치·환경 모두 닮은 사례" / "닮았다고 보기 어려운 사례(참고용)". (4) 결과 한 줄 — "이 국면 뒤 12개월 SOX는 -38.5%였다." 예측 문구 금지.
- 문구 임계값: 낙폭 차 ≤3%p "거의 같다", ≤8 "비슷하다", 그 외 "다르다"; 금리 차 ≤0.5%p "같은 수준", ≤2 "비슷", 그 외 "다르다"; 비율형(유가·YoY)은 큰 쪽/작은 쪽 ≥2배면 "N배 차이".
- API: `/api/explain` 응답에 `comment`, `/api/similarity` 응답에 `fit`, `applicable`, `applicable_n`, `forward_summary_source` 통과. 프론트: 카드 하단 코멘트 블록, `weak` 배지, 페이지 상단 "해당 없음" 배너, 헤더에 모드 토글(모양 우선/환경 우선; R의 `mode`가 없으면 토글 숨김).
- (선택, 기본 꺼짐) `REPORT_LLM=on`이면 규칙 코멘트를 OpenAI로 다듬은 한 문장을 추가. 기본은 규칙 코멘트만.

## 27. 완료 기준
- `python -m stocker.similarity --as-of 2026-11-04 --mode regime`가 적합 상태와 함께 결과를 낸다.
- `python -m stocker.backtest run && report`가 `docs/BACKTEST_RESULTS.md`를 만들고, 판정이 승리 조건대로 적혀 있다.
- React `/`에서 카드마다 코멘트가 보이고, 적합 국면이 없을 때 배너와 기준선 분포가 보인다.

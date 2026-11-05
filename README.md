# stocker

반도체 지수(SOX / SOXX)의 **현재 국면이 과거 어느 국면과 닮았는지**를 찾고,
그 국면 이후의 수익 분포와 레버리지 ETF(SOXL) 시뮬레이션 결과를 보여주는 로컬 대시보드.

예측기가 아닙니다. "닮은 과거 n개 중 결과가 어떻게 갈렸는가"를 숨기지 않고 보여주는 도구입니다.

- 기획/설계: [docs/PLAN.md](docs/PLAN.md)
- 데이터·모듈 계약: [docs/DATA_CONTRACT.md](docs/DATA_CONTRACT.md)
- 에이전트 작업 규칙: [CLAUDE.md](CLAUDE.md)

## 빠른 시작 (Phase 0)

```bash
cp .env.example .env          # FRED_API_KEY 입력
docker compose up -d db
uv sync
uv run python -m stocker.db init
uv run python -m stocker.ingest prices
uv run python -m stocker.ingest fred
uv run python -m stocker.sim --index SOXX --leverage 3 --start 2022-01-01 --compare SOXL
```

대시보드 (http://localhost:8501):

```bash
make web                     # 로컬: uv run streamlit run stocker/web/app.py
docker compose up web        # docker (db 자동 기동)
```

## DB 초기화가 필요한 경우

스키마의 hypertable 조각 간격이 바뀌었으면(2026-11-05 이전 볼륨) 볼륨을 지우고 다시 적재한다. 모든 데이터는 재다운로드 가능하다.

```bash
docker compose down -v && docker compose up -d db && uv run python -m stocker.db init
uv run python -m stocker.ingest prices && uv run python -m stocker.ingest fred && uv run python -m stocker.ingest semi && uv run python -m stocker.ingest events
```

## 상태

Phase 0 진행 중. 로드맵은 docs/PLAN.md 참고.

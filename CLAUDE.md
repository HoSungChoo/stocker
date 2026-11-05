# CLAUDE.md — stocker

이 저장소에서 작업하는 모든 에이전트가 따르는 규칙.

## 역할 분담

- **기획·설계·리뷰**: 상위 에이전트(Fable). `docs/PLAN.md`, `docs/DATA_CONTRACT.md`, 이 파일을 소유한다.
- **구현**: 하위 에이전트(Opus/Sonnet). 브리프에 명시된 파일·디렉터리만 수정한다.
  브리프 밖의 파일을 고쳐야 한다면 고치지 말고 보고서에 "필요한 변경"으로 적는다.
- 구현 에이전트는 `docs/DATA_CONTRACT.md`를 계약으로 취급한다. 계약을 바꾸고 싶으면 보고서에 제안만 한다.

## 기술 스택

- Python 3.12, 패키지 관리 `uv`, 패키지 이름 `stocker` (저장소 루트의 `stocker/`).
- 데이터: pandas, numpy. 시세 수집 yfinance. 매크로 FRED(`fredapi`).
- 저장: PostgreSQL 16 + TimescaleDB (docker compose `db` 서비스). SQLAlchemy 2 + psycopg 3.
- CLI: typer. 각 하위 패키지가 자기 `__main__.py`를 가진다 (`python -m stocker.ingest`, `python -m stocker.sim`, `python -m stocker.db`).
- 테스트: pytest. 네트워크·DB가 필요한 테스트는 마커 `network`, `db`로 분리하고 기본 실행에서 제외한다.
- 린트/포맷: ruff (`uv run ruff check .`, `uv run ruff format .`).

## 코드 규칙

- 모든 시계열 함수는 `pd.Series`/`pd.DataFrame`을 받고 돌려준다. DB 접근은 `stocker/db/`에만 둔다.
- 날짜 인덱스는 tz-naive `DatetimeIndex`, 거래일 기준. 컬럼명은 계약 문서의 소문자 snake_case 그대로.
- 심볼 표기는 yfinance 기준 (`^SOX`, `SOXX`, `SOXL`, `SMH`).
- 비밀값은 `.env`에서만 읽는다 (`stocker/config.py`). 코드·문서에 키를 쓰지 않는다.
- 한국어 주석/문서, 영어 식별자.

## 검증

- `uv run pytest` 가 네트워크 없이 통과해야 한다.
- `uv run pytest -m network` 는 인터넷이 있을 때만.
- `docker compose up -d db` 후 `uv run pytest -m db`.

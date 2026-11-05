.PHONY: db-up db-down init test lint fmt web report schedule

db-up:
	docker compose up -d --wait db

db-down:
	docker compose down

init:
	uv run python -m stocker.db init

web: ## Streamlit 대시보드 (http://localhost:8501)
	uv run streamlit run stocker/web/app.py

report: ## 아침 보고서 즉시 1회 (data/reports/에 저장)
	uv run python -m stocker.report run

schedule: ## 매일 아침 보고서 스케줄러 (화~토 REPORT_TIME KST)
	uv run python -m stocker.report schedule

test:
	uv run pytest

lint:
	uv run ruff check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

seed: ## WSTS 시드 CSV 생성 (git 미추적)
	uv run --with openpyxl python scripts/parse_wsts.py

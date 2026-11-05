.PHONY: db-up db-down init test lint fmt

db-up:
	docker compose up -d --wait db

db-down:
	docker compose down

init:
	uv run python -m stocker.db init

test:
	uv run pytest

lint:
	uv run ruff check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

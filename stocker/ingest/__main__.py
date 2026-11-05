"""CLI: python -m stocker.ingest prices|fred|semi|events"""

from __future__ import annotations

import pandas as pd
import typer

from stocker.ingest.fred import DEFAULT_SERIES
from stocker.ingest.prices import DEFAULT_SYMBOLS

app = typer.Typer(add_completion=False, no_args_is_help=True, help="데이터 수집")


@app.callback()
def _main() -> None:
    """데이터 수집."""


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _report(df: pd.DataFrame, key: str, rows: int) -> None:
    typer.echo(f"적재 {rows}행")
    for k, g in df.groupby(key, sort=False):
        typer.echo(f"  {k}: {len(g)}행, {g['date'].min():%Y-%m-%d} ~ {g['date'].max():%Y-%m-%d}")


@app.command()
def prices(
    symbols: str = typer.Option(",".join(DEFAULT_SYMBOLS), "--symbols", help="쉼표 구분 심볼"),
    start: str = typer.Option("1990-01-01", "--start", help="시작일 YYYY-MM-DD"),
) -> None:
    """yfinance 시세를 prices_daily에 적재."""
    from stocker.ingest.prices import fetch_prices, store_prices

    df = fetch_prices(_split(symbols), start=start)
    if df.empty:
        typer.echo("받은 데이터가 없다", err=True)
        raise typer.Exit(1)
    _report(df, "symbol", store_prices(df))


@app.command()
def fred(
    series: str = typer.Option(",".join(DEFAULT_SERIES), "--series", help="쉼표 구분 FRED id"),
    start: str = typer.Option("1990-01-01", "--start", help="시작일 YYYY-MM-DD"),
) -> None:
    """FRED 시계열을 macro_series에 적재."""
    from stocker.ingest.fred import fetch_fred, store_fred

    try:
        df = fetch_fred(_split(series), start=start)
    except RuntimeError as exc:
        typer.echo(f"오류: {exc}", err=True)
        raise typer.Exit(1) from exc
    if df.empty:
        typer.echo("받은 데이터가 없다", err=True)
        raise typer.Exit(1)
    _report(df, "series_id", store_fred(df))


@app.command()
def semi() -> None:
    """WSTS 시드 + 마이크론 분기 실적을 semi_cycle에 적재."""
    from stocker.ingest.semi import fetch_semi, store_semi

    df = fetch_semi()
    _report(df, "metric", store_semi(df))


@app.command()
def events(
    only: str = typer.Option("", "--only", help="쉼표 구분 kind만 수집 (예: fomc,policy)"),
) -> None:
    """FOMC·CPI·고용보고서·실적일·정책 시드를 events에 적재."""
    from stocker.ingest.events import VALID_KINDS, collect_events, store_events

    kinds = _split(only)
    unknown = [k for k in kinds if k not in VALID_KINDS]
    if unknown:
        typer.echo(f"오류: 알 수 없는 kind {unknown}. 허용: {', '.join(VALID_KINDS)}", err=True)
        raise typer.Exit(1)
    df = collect_events(kinds or None)
    if df.empty:
        typer.echo("받은 데이터가 없다", err=True)
        raise typer.Exit(1)
    _report(df, "kind", store_events(df))
    missing = [k for k in (kinds or VALID_KINDS) if k not in set(df["kind"])]
    if missing:
        typer.echo(f"경고: 데이터가 없는 kind: {', '.join(missing)}", err=True)


if __name__ == "__main__":
    app()

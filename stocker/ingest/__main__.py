"""CLI: python -m stocker.ingest prices|fred|semi"""

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


if __name__ == "__main__":
    app()

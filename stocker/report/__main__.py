"""CLI: python -m stocker.report run|schedule|show"""

from __future__ import annotations

import datetime as dt
import logging

import typer

app = typer.Typer(add_completion=False, no_args_is_help=True, help="아침 보고서")


@app.callback()
def _main() -> None:
    """아침 보고서."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@app.command()
def run(
    as_of: str = typer.Option("", "--as-of", help="기준일 YYYY-MM-DD (기본: 마지막 거래일)"),
    no_deliver: bool = typer.Option(False, "--no-deliver", help="파일·이메일·텔레그램 모두 생략"),
    no_refresh: bool = typer.Option(False, "--no-refresh", help="데이터 갱신(네트워크) 생략"),
) -> None:
    """갱신 → 계산 → 렌더 → report_runs 저장 → 전달을 1회 실행."""
    from stocker.config import settings
    from stocker.report.pipeline import run_daily

    report = run_daily(as_of=as_of or None, refresh=not no_refresh, send=not no_deliver)
    typer.echo(f"기준일 {report.as_of:%Y-%m-%d}, 창 {[s.window for s in report.sections]}")
    for src in report.data_status["sources"]:
        typer.echo(f"  {src['name']}: {src['refreshed']}")
    if no_deliver:
        typer.echo("전달 생략 (report_runs에만 저장)")
    else:
        typer.echo(f"전달 채널: {', '.join(report.channels)}")
        typer.echo(f"파일: {settings.data_dir}/reports/{report.as_of:%Y-%m-%d}.md")


@app.command()
def schedule(
    run_now: bool = typer.Option(False, "--run-now", help="시작 직후 1회 실행"),
) -> None:
    """월~금 REPORT_TIME(KST)에 매일 실행. 종료하려면 Ctrl+C."""
    from stocker.report.scheduler import main

    main(run_now=run_now)


@app.command()
def show(
    date: str = typer.Option("", "--date", help="보고서 기준일 YYYY-MM-DD (기본: 최신)"),
) -> None:
    """저장된 보고서(report_runs window=0)를 출력."""
    from stocker.db import read_report_run

    row = read_report_run(dt.date.fromisoformat(date) if date else None, 0)
    if row is None:
        typer.echo("저장된 보고서가 없다. `python -m stocker.report run` 먼저.", err=True)
        raise typer.Exit(1)
    typer.echo(row["report_md"])


if __name__ == "__main__":
    app()

"""CLI: python -m stocker.backtest run --modes baseline,shape,regime [--start 1999-01 --end 2025-10]
   [--horizons 21,63,126,252]
python -m stocker.backtest report [--horizon 252] [--out docs/BACKTEST_RESULTS.md]
"""

from __future__ import annotations

import time
from pathlib import Path

import typer

from stocker.backtest.harness import ALL_MODES, DEFAULT_END, DEFAULT_START
from stocker.backtest.report import DEFAULT_PATH
from stocker.similarity.config import SimilarityConfig

app = typer.Typer(
    add_completion=False, help="기준선·모양 우선·환경 우선 백테스트", no_args_is_help=True
)


@app.callback()
def _main() -> None:
    """기준선·모양 우선·환경 우선 백테스트."""


@app.command()
def run(
    modes: str = typer.Option(
        ",".join(ALL_MODES),
        help="쉼표 구분: baseline,shape,shape_env,regime (shape_env는 참고 모드)",
    ),
    start: str = typer.Option(DEFAULT_START, help="시작 월 YYYY-MM"),
    end: str = typer.Option(DEFAULT_END, help="끝 월 YYYY-MM (포함)"),
    horizons: str = typer.Option(
        "21,63,126,252", "--horizons", "--horizon", help="저장할 horizon들"
    ),
    window: int = typer.Option(120, help="창 길이(거래일)"),
    top_k: int = typer.Option(5, help="상위 K"),
) -> None:
    """매월 말 거래일마다 각 모드를 실행해 backtest_runs에 upsert한다."""
    from stocker.backtest.harness import run_backtest
    from stocker.db import get_engine, write_backtest_runs

    mode_list = [m.strip() for m in modes.split(",") if m.strip()]
    bad = [m for m in mode_list if m not in ALL_MODES]
    if bad:
        raise typer.BadParameter(f"알 수 없는 모드: {bad}")
    hz = tuple(int(h) for h in horizons.split(",") if h.strip())
    cfg = SimilarityConfig(window=window, top_k=top_k, horizons=hz)
    engine = get_engine()

    def progress(i: int, n: int, elapsed: float) -> None:
        if i == n or i % 24 == 0:
            typer.echo(f"  {i}/{n} 시점  {elapsed:.1f}s")

    t0 = time.perf_counter()
    df = run_backtest(mode_list, start, end, cfg, hz, engine, progress)
    n_written = write_backtest_runs(df, engine)
    elapsed = time.perf_counter() - t0
    n_dates = df["as_of"].nunique() if not df.empty else 0
    typer.echo(
        f"modes={mode_list} 시점 {n_dates}개 × horizon {len(hz)}개 → {len(df)}행 "
        f"(DB upsert {n_written})  elapsed={elapsed:.1f}s"
    )


@app.command()
def report(
    horizon: int = typer.Option(252, help="채점 horizon(거래일)"),
    out: Path = typer.Option(DEFAULT_PATH, help="출력 Markdown 경로"),
) -> None:
    """backtest_runs를 읽어 docs/BACKTEST_RESULTS.md를 생성한다."""
    from stocker.backtest.report import write_report
    from stocker.db import get_engine, read_backtest_runs

    runs = read_backtest_runs(engine=get_engine())
    if runs.empty:
        typer.echo("backtest_runs가 비어 있다. 먼저 `run`.", err=True)
        raise typer.Exit(code=1)
    path = write_report(runs, out, horizon)
    typer.echo(f"wrote {path} (horizon={horizon}, rows={len(runs)})")


if __name__ == "__main__":
    app()

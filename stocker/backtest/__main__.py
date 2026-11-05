"""CLI: python -m stocker.backtest run --modes baseline,shape,regime [--start 1999-01 --end 2025-10]
   [--horizons 21,63,126,252]
python -m stocker.backtest run --preset phase6b      (PLAN_PHASE6B 2절의 arm 9개)
python -m stocker.backtest report [--horizon 252] [--out docs/BACKTEST_RESULTS.md]
python -m stocker.backtest report --preset phase6b   → docs/BACKTEST_RESULTS_K.md
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import typer

from stocker.backtest.harness import (
    ALL_MODES,
    DEFAULT_END,
    DEFAULT_START,
    PRESET_COLUMNS,
)
from stocker.backtest.phase6b import DEFAULT_PATH_K
from stocker.backtest.presets import PRESETS, get_preset
from stocker.backtest.report import DEFAULT_PATH
from stocker.similarity.config import SimilarityConfig

app = typer.Typer(
    add_completion=False, help="기준선·모양 우선·환경 우선 백테스트", no_args_is_help=True
)

# preset 실행 결과의 사본(CSV). DB에도 저장하지만 read_backtest_runs가 n_eff 컬럼을 돌려주지
# 않아(stocker/db는 이 작업 범위 밖) report가 실질 표본 수를 여기서 보충한다.
RUNS_DIR = Path("data/backtest")


def _runs_csv(preset: str) -> Path:
    return RUNS_DIR / f"{preset}_runs.csv"


@app.callback()
def _main() -> None:
    """기준선·모양 우선·환경 우선 백테스트."""


def _progress(i: int, n: int, elapsed: float) -> None:
    if i == n or i % 24 == 0:
        typer.echo(f"  {i}/{n} 시점  {elapsed:.1f}s")


def _write_preset_runs(df: pd.DataFrame, engine) -> int:
    """backtest_runs에 arm 행 upsert (키 mode·as_of·horizon, mode=arm 이름) + 정보 컬럼."""
    from stocker.db import BACKTEST_COLUMNS, upsert_df

    cols = BACKTEST_COLUMNS + PRESET_COLUMNS + ["n_eff"]
    out = df[cols].copy()
    out["as_of"] = pd.to_datetime(out["as_of"])
    out["created_at"] = pd.Timestamp.now(tz="UTC")
    return upsert_df(out, "backtest_runs", ["mode", "as_of", "horizon"], engine=engine)


def _check_baseline_unchanged(df: pd.DataFrame, engine) -> None:
    """preset의 baseline arm은 Phase 6의 baseline 행과 같은 키를 쓴다. 덮어쓰기 전에 값이 같은지
    확인해 다르면 경고한다(기준선은 k와 무관하므로 같아야 한다)."""
    from stocker.db import read_backtest_runs

    new = df[df["mode"] == "baseline"]
    if new.empty:
        return
    old = read_backtest_runs(modes=["baseline"], engine=engine)
    if old.empty:
        typer.echo("  기존 baseline 행 없음 (새로 저장)")
        return
    keys = ["as_of", "horizon"]
    m = old.merge(new, on=keys, suffixes=("_old", "_new"))
    diff = 0
    for c in ("median", "p10", "p90", "n"):
        a, b = m[f"{c}_old"].astype("float64"), m[f"{c}_new"].astype("float64")
        diff += int((~((a - b).abs() < 1e-12) & ~(a.isna() & b.isna())).sum())
    if diff:
        typer.echo(
            f"  경고: 기존 baseline 행과 값이 다른 셀 {diff}개 "
            "(시세가 갱신됐을 수 있음). 덮어쓴다.",
            err=True,
        )
    else:
        typer.echo(f"  기존 baseline 행 {len(m)}개와 분포 값 동일 (덮어써도 결과 불변)")


@app.command()
def run(
    modes: str = typer.Option(
        ",".join(ALL_MODES),
        help="쉼표 구분: baseline,shape,shape_env,regime (shape_env는 참고 모드)",
    ),
    preset: str | None = typer.Option(
        None, help=f"사전 등록 preset ({', '.join(PRESETS)}). 주면 --modes/--top-k는 무시"
    ),
    start: str = typer.Option(DEFAULT_START, help="시작 월 YYYY-MM"),
    end: str = typer.Option(DEFAULT_END, help="끝 월 YYYY-MM (포함)"),
    horizons: str = typer.Option(
        "21,63,126,252", "--horizons", "--horizon", help="저장할 horizon들"
    ),
    window: int = typer.Option(120, help="창 길이(거래일)"),
    top_k: int = typer.Option(5, help="상위 K (preset이 없을 때)"),
) -> None:
    """매월 말 거래일마다 각 모드(또는 preset의 arm)를 실행해 backtest_runs에 upsert한다."""
    from stocker.db import get_engine, write_backtest_runs

    hz = tuple(int(h) for h in horizons.split(",") if h.strip())
    engine = get_engine()
    t0 = time.perf_counter()

    if preset is not None:
        from stocker.backtest.harness import run_preset

        arms = get_preset(preset)
        cfg = SimilarityConfig(window=window, horizons=hz)
        df = run_preset(preset, start, end, cfg, hz, engine, _progress)
        _check_baseline_unchanged(df, engine)
        n_written = _write_preset_runs(df, engine)
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        df.to_csv(_runs_csv(preset), index=False)
        elapsed = time.perf_counter() - t0
        n_dates = df["as_of"].nunique() if not df.empty else 0
        typer.echo(
            f"preset={preset} arm {len(arms)}개 × 시점 {n_dates}개 × horizon {len(hz)}개 → "
            f"{len(df)}행 (DB upsert {n_written}, 사본 {_runs_csv(preset)})  elapsed={elapsed:.1f}s"
        )
        return

    from stocker.backtest.harness import run_backtest

    mode_list = [m.strip() for m in modes.split(",") if m.strip()]
    bad = [m for m in mode_list if m not in ALL_MODES]
    if bad:
        raise typer.BadParameter(f"알 수 없는 모드: {bad}")
    cfg = SimilarityConfig(window=window, top_k=top_k, horizons=hz)
    df = run_backtest(mode_list, start, end, cfg, hz, engine, _progress)
    n_written = write_backtest_runs(df, engine)
    elapsed = time.perf_counter() - t0
    n_dates = df["as_of"].nunique() if not df.empty else 0
    typer.echo(
        f"modes={mode_list} 시점 {n_dates}개 × horizon {len(hz)}개 → {len(df)}행 "
        f"(DB upsert {n_written})  elapsed={elapsed:.1f}s"
    )


def _attach_n_eff(runs: pd.DataFrame, preset: str) -> pd.DataFrame:
    """DB에서 읽은 행(n_eff 없음)에 run이 남긴 CSV 사본의 n_eff를 붙인다. 사본이 없으면 NaN."""
    path = _runs_csv(preset)
    if not path.exists():
        typer.echo(f"  {path} 없음 → 실질 표본 수는 n/a", err=True)
        runs = runs.copy()
        runs["n_eff"] = float("nan")
        return runs
    side = pd.read_csv(path, usecols=["mode", "as_of", "horizon", "n_eff"])
    side["as_of"] = pd.to_datetime(side["as_of"])
    return runs.merge(side, on=["mode", "as_of", "horizon"], how="left")


@app.command()
def report(
    horizon: int = typer.Option(252, help="채점 horizon(거래일)"),
    out: Path | None = typer.Option(None, help="출력 Markdown 경로"),
    preset: str | None = typer.Option(
        None, help=f"사전 등록 preset ({', '.join(PRESETS)}) → docs/BACKTEST_RESULTS_K.md"
    ),
) -> None:
    """backtest_runs를 읽어 결과 문서를 생성한다 (기본 Phase 6, --preset이면 그 arm들만)."""
    from stocker.db import get_engine, read_backtest_runs

    engine = get_engine()
    if preset is not None:
        from stocker.backtest.phase6b import write_report_k

        arms = get_preset(preset)
        runs = read_backtest_runs(modes=[a.name for a in arms], engine=engine)
        if runs.empty:
            typer.echo(f"preset={preset} 결과가 없다. 먼저 `run --preset {preset}`.", err=True)
            raise typer.Exit(code=1)
        runs = _attach_n_eff(runs, preset)
        path = write_report_k(runs, out or DEFAULT_PATH_K, horizon, preset)
        typer.echo(f"wrote {path} (preset={preset}, horizon={horizon}, rows={len(runs)})")
        return

    from stocker.backtest.report import write_report

    # Phase 6 문서는 Phase 6 모드만 (preset arm 행이 같은 표에 섞이지 않도록)
    runs = read_backtest_runs(modes=list(ALL_MODES), engine=engine)
    if runs.empty:
        typer.echo("backtest_runs가 비어 있다. 먼저 `run`.", err=True)
        raise typer.Exit(code=1)
    path = write_report(runs, out or DEFAULT_PATH, horizon)
    typer.echo(f"wrote {path} (horizon={horizon}, rows={len(runs)})")


if __name__ == "__main__":
    app()

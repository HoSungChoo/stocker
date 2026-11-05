"""CLI: python -m stocker.similarity --as-of 2026-11-04 [--mode shape|regime] [--window 120]
        [--top-k 5] [--weights 0.5,0.3,0.2] [--min-gap 250] [--cycle-lag-days 0]

DB에서 SOX 종가·매크로·산업 사이클(·이벤트)을 읽어 유사 국면 상위 K와 이후 수익 분포를 표로
보여준다. 국면마다 적합 상태(strong/ok/weak, 계약 23절)를 함께 표시하고, 충분히 닮은 과거가
없으면(applicable=False) 그 사실을 맨 위에 적는다.
"""

from __future__ import annotations

import math
import time

import pandas as pd
import typer

from stocker.similarity.config import SimilarityConfig
from stocker.similarity.engine import LAYERS, SimilarityResult

app = typer.Typer(add_completion=False, help="SOX 국면 유사도 검색")

HORIZON_LABEL = {21: "1m", 63: "3m", 126: "6m", 252: "12m"}
FIT_BADGE = {"strong": "strong ●●●", "ok": "ok ●●○", "weak": "weak ○○○"}
NOT_APPLICABLE_MSG = "현재와 충분히 닮은 과거 사례가 없다. 아래 분포는 전체 과거 기준이다."
FEW_APPLICABLE_MSG = (
    "현재와 충분히 닮은 과거 사례가 {n}개뿐이다(기준 {k}개). 아래 분포는 그 {n}개로만 계산했다."
)
FIT_UNKNOWN_MSG = (
    "적합 기준을 판정할 수 없다(금리·업황 입력 없음). 아래 분포는 상위 K 전체 기준이다."
)


def _pct(x: float) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.1%}"


def _num(x: float) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.3f}"


def _parse_weights(text: str | None) -> dict[str, float] | None:
    if not text:
        return None
    parts = [float(p) for p in text.split(",")]
    if len(parts) != len(LAYERS):
        raise typer.BadParameter(f"--weights는 값 {len(LAYERS)}개: price_shape,macro,semi_cycle")
    return dict(zip(LAYERS, parts, strict=True))


def _layers(res: SimilarityResult) -> list[str]:
    """표에 보일 레이어(그룹) 열. 모양 우선은 LAYERS, 환경 우선은 regime.GROUPS."""
    if getattr(res, "mode", "shape") == "regime":
        from stocker.similarity.regime import GROUPS

        return list(GROUPS)
    return list(LAYERS)


def _fit_cell(res: SimilarityResult, i: int) -> str:
    fits = getattr(res, "fit", None) or []
    if i >= len(fits):
        return "n/a"
    f = fits[i]
    return f"{FIT_BADGE.get(f.status, f.status)} {f.n_passed}/5"


def fit_banner(res: SimilarityResult) -> str | None:
    """applicable 상태에 따라 맨 위에 보일 문장. 보일 것이 없으면 None."""
    applicable = getattr(res, "applicable", None)
    if applicable is None:
        return FIT_UNKNOWN_MSG if getattr(res, "fit", None) else None
    if applicable:
        return None
    if getattr(res, "forward_summary_source", "matches") == "baseline":
        return NOT_APPLICABLE_MSG
    return FEW_APPLICABLE_MSG.format(n=res.applicable_n, k=res.config.fit_min_applicable)


def _rows(res: SimilarityResult) -> tuple[list[list[str]], list[list[str]]]:
    hz = list(res.config.horizons)
    layers = _layers(res)
    match_rows = []
    for i, m in enumerate(res.matches, 1):
        start = m.start_date.date() if m.start_date is not None else ""
        row = [str(i), f"{start} → {m.end_date.date()}"]  # noqa: RUF001
        row += [_num(m.scores.get(layer, float("nan"))) for layer in layers]
        row += [_num(m.combined), _fit_cell(res, i - 1)]
        row += [_pct(m.forward.get(h, float("nan"))) for h in hz]
        match_rows.append(row)
    summary_rows = []
    for h, r in res.forward_summary.iterrows():
        summary_rows.append(
            [
                f"{h}d ({HORIZON_LABEL.get(int(h), '')})",
                _pct(r["median"]),
                _pct(r["p10"]),
                _pct(r["p90"]),
                str(int(r["n_up"])),
                str(int(r["n_down"])),
                str(int(r["n"])),
            ]
        )
    return match_rows, summary_rows


def print_result(res: SimilarityResult, elapsed: float | None = None) -> None:
    hz = list(res.config.horizons)
    mode = getattr(res, "mode", "shape")
    match_hdr = ["#", "구간 (시작 → 끝)", *_layers(res), "combined", "적합"] + [
        f"fwd {HORIZON_LABEL.get(h, h)}" for h in hz
    ]
    summary_hdr = ["horizon", "median", "p10", "p90", "n_up", "n_down", "n"]
    match_rows, summary_rows = _rows(res)
    banner = fit_banner(res)
    header = (
        f"as_of={res.as_of.date()}"
        + (
            f" (요청 {res.requested_as_of.date()})"
            if res.requested_as_of is not None and res.requested_as_of != res.as_of
            else ""
        )
        + f"  mode={mode}  window={res.config.window}  top_k={res.config.top_k}"
        f"  min_gap={res.config.min_gap_days}\n"
        f"n_candidates={res.n_candidates}  layers_missing={res.layers_missing}  "
        f"weights_used={ {k: round(v, 3) for k, v in res.weights_used.items()} }\n"
        f"applicable={getattr(res, 'applicable', None)}  applicable_n={res.applicable_n}  "
        f"forward_summary_source={res.forward_summary_source}"
        + (f"  elapsed={elapsed:.2f}s" if elapsed is not None else "")
    )
    summary_title = (
        "전체 과거 SOX 수익 분포 (기준선 — 닮은 사례 없음)"
        if res.forward_summary_source == "baseline"
        else "유사 국면 이후 SOX 수익 분포 (forward_summary, ok 이상만)"
    )
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console(width=None if Console().is_terminal else 170)
        if banner:
            console.print(f"[bold yellow]⚠ {banner}[/]")
        console.print(header)
        t1 = Table(title=f"유사 국면 상위 {len(res.matches)} (n={len(res.matches)})")
        for i, h in enumerate(match_hdr):
            t1.add_column(h, justify="left" if i <= 1 or h == "적합" else "right")
        for r in match_rows:
            t1.add_row(*r)
        console.print(t1)
        t2 = Table(title=summary_title)
        for i, h in enumerate(summary_hdr):
            t2.add_column(h, justify="left" if i == 0 else "right")
        for r in summary_rows:
            t2.add_row(*r)
        console.print(t2)
    except ImportError:
        if banner:
            print(f"! {banner}")
        print(header)
        print("\t".join(match_hdr))
        for r in match_rows:
            print("\t".join(r))
        print(summary_title)
        print("\t".join(summary_hdr))
        for r in summary_rows:
            print("\t".join(r))


@app.command()
def main(
    as_of: str = typer.Option(..., "--as-of", help="기준일 YYYY-MM-DD (이하 마지막 거래일 사용)"),
    window: int = typer.Option(120, help="창 길이(거래일). 60/120/250"),
    top_k: int = typer.Option(5, help="상위 K"),
    weights: str | None = typer.Option(
        None, help="price_shape,macro,semi_cycle 가중치 예: 0.5,0.3,0.2"
    ),
    min_gap: int = typer.Option(250, help="as_of·선택 국면 간 최소 간격(거래일)"),
    cycle_lag_days: int = typer.Option(
        45, help="산업 사이클 월간 값의 발표 지연(일). WSTS 월말값은 다음 달 중순 공개"
    ),
    mode: str = typer.Option("shape", help="shape(모양 우선, 기존) | regime(환경 우선)"),
) -> None:
    from stocker.similarity.loaders import run

    if mode not in ("shape", "regime"):
        raise typer.BadParameter("--mode는 shape 또는 regime")
    kw = dict(
        window=window, top_k=top_k, min_gap_days=min_gap, cycle_lag_days=cycle_lag_days, mode=mode
    )
    w = _parse_weights(weights)
    if w is not None:
        kw["weights"] = w
    cfg = SimilarityConfig(**kw)
    t0 = time.perf_counter()
    res = run(pd.Timestamp(as_of), cfg)
    print_result(res, elapsed=time.perf_counter() - t0)


if __name__ == "__main__":
    app()

"""CLI: python -m stocker.events impact"""

from __future__ import annotations

import typer

from stocker.db import get_engine, read_event_impact
from stocker.events.impact import DEFAULT_KINDS, compute_event_impact

app = typer.Typer(add_completion=False, no_args_is_help=True, help="이벤트 영향")


@app.callback()
def _main() -> None:
    """이벤트 영향."""


def _pct(v) -> str:
    return "  -  " if v != v or v is None else f"{v * 100:+.1f}%"


@app.command()
def impact(
    kinds: str = typer.Option(",".join(DEFAULT_KINDS), "--kinds", help="쉼표 구분 이벤트 종류"),
) -> None:
    """이벤트 전후 ^SOX 수익률을 계산해 event_impact에 적재하고 |ret5| 상위 5개를 출력."""
    ks = [k.strip() for k in kinds.split(",") if k.strip()]
    engine = get_engine()
    rows = compute_event_impact(ks, engine=engine)
    typer.echo(f"계산 {rows}행 ({', '.join(ks)})")
    top = read_event_impact(limit=5, kinds=ks, engine=engine)
    typer.echo(f"{'날짜':<11}{'종류':<9}{'ret5':>8}{'1M':>8}{'3M':>8}  제목")
    for r in top.itertuples():
        typer.echo(
            f"{r.date:%Y-%m-%d}  {r.kind:<8}{_pct(r.ret5):>8}{_pct(r.ret_1m):>8}"
            f"{_pct(r.ret_3m):>8}  {r.title}"
        )


if __name__ == "__main__":
    app()

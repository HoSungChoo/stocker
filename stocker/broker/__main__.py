"""CLI: python -m stocker.broker check|snapshot (읽기 전용)"""

from __future__ import annotations

import typer

from stocker.broker.positions import money, snapshot_positions
from stocker.broker.toss import TossApiError, from_env

app = typer.Typer(add_completion=False, no_args_is_help=True, help="토스증권 읽기 전용 연동")

NO_CREDENTIALS = (
    "TOSS_CLIENT_ID/SECRET이 없다. WTS 설정→Open API에서 발급하고 허용 IP를 등록하라. "
    "발급한 값은 .env에 넣는다."
)


@app.callback()
def _main() -> None:
    """토스증권 읽기 전용 연동."""


def _client():
    client = from_env()
    if client is None:
        typer.echo(NO_CREDENTIALS, err=True)
        raise typer.Exit(1)
    return client


def _fail(exc: TossApiError) -> typer.Exit:
    hint = (
        " 허용 IP에 이 컴퓨터의 공인 IP를 등록했는지 확인하라."
        if exc.code == "ip-not-allowed"
        else ""
    )
    typer.echo(
        f"토스 API 오류 [{exc.code}] {exc.message} (requestId={exc.request_id}).{hint}", err=True
    )
    return typer.Exit(2)


@app.command()
def check() -> None:
    """계좌 목록, 보유 종목, 매수가능금액을 출력해 연동을 확인한다."""
    client = _client()
    try:
        accounts = client.accounts()
        typer.echo("계좌")
        for a in accounts:
            typer.echo(f"  {a.get('accountNo')}  seq={a.get('accountSeq')}  {a.get('accountType')}")
        h = client.holdings()
        power = client.buying_power()
    except TossApiError as exc:
        raise _fail(exc) from None
    typer.echo(f"\n사용 계좌 seq={client.account_seq()}\n")
    items = h.get("items") or []
    typer.echo(f"보유 종목 {len(items)}개")
    typer.echo(f"  {'종목':<8}{'수량':>10}{'평단':>14}{'현재가':>14}{'평가액':>16}{'손익률':>9}")
    for it in items:
        cur = it.get("currency")
        qty = float(it.get("quantity") or 0)
        rate = (it.get("profitLoss") or {}).get("rate")
        typer.echo(
            f"  {it['symbol']:<8}{qty:>10,.4f}"
            f"{money(_num(it.get('averagePurchasePrice')), cur):>14}"
            f"{money(_num(it.get('lastPrice')), cur):>14}"
            f"{money(_num((it.get('marketValue') or {}).get('amount')), cur):>16}"
            f"{(f'{float(rate) * 100:+.2f}%' if rate is not None else '—'):>9}"
        )
    typer.echo(f"\n매수가능금액 {money(_num(power.get('cashBuyingPower')), power.get('currency'))}")


def _num(x):
    return None if x is None or x == "" else float(x)


@app.command()
def snapshot() -> None:
    """보유 종목을 positions_snapshot에 저장하고 행 수를 출력한다."""
    client = _client()
    try:
        df = snapshot_positions(client)
    except TossApiError as exc:
        raise _fail(exc) from None
    typer.echo(f"저장 {len(df)}행 (positions_snapshot)")


if __name__ == "__main__":
    app()

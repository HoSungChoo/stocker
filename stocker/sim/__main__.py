"""CLI: python -m stocker.sim --index SOXX --leverage 3 --start 2022-01-01 [--compare SOXL]

--source db (기본): stocker.db에서 읽는다 (DB 의존성은 함수 안에서 지연 import).
--source yfinance: yfinance로 직접 받는다 (auto_adjust=False, Adj Close 사용).
"""

from __future__ import annotations

import os
import sys
from enum import Enum

import pandas as pd
import typer

from stocker.sim.leveraged import calibrate, simulate_leveraged

app = typer.Typer(add_completion=False, help="레버리지 ETF NAV 시뮬레이션/캘리브레이션")


class Source(str, Enum):
    db = "db"
    yfinance = "yfinance"


def _warn(msg: str) -> None:
    print(f"[warn] {msg}", file=sys.stderr)


def _load_adj_close_db(symbol: str, start: str, end: str | None) -> pd.Series:
    from stocker.db import read_prices  # 지연 import: DB 의존성이 없어도 모듈은 import 됨

    df = read_prices(symbol, start=start, end=end)
    if df.empty:
        raise typer.BadParameter(f"DB에 {symbol} 시세가 없다 (ingest 먼저 실행)")
    return df["adj_close"].dropna().rename(symbol)


def _load_adj_close_yf(symbol: str, start: str, end: str | None) -> pd.Series:
    import yfinance as yf

    # yfinance의 end는 배타적이므로 하루 더한다.
    end_excl = None if end is None else (pd.Timestamp(end) + pd.Timedelta(days=1))
    df = yf.download(symbol, start=start, end=end_excl, auto_adjust=False, progress=False)
    if df is None or df.empty:
        raise typer.BadParameter(f"yfinance에서 {symbol} 데이터를 받지 못했다")
    adj = df["Adj Close"]
    if isinstance(adj, pd.DataFrame):  # 최신 yfinance는 (field, ticker) MultiIndex
        adj = adj.iloc[:, 0]
    idx = pd.DatetimeIndex(adj.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    adj.index = idx.normalize()
    return adj.dropna().astype(float).rename(symbol)


def _fred_api_key() -> str | None:
    try:
        from stocker.config import settings

        key = getattr(settings, "fred_api_key", None)
        if key:
            return str(key)
    except Exception:  # config 모듈이 없거나 설정 오류
        pass
    return os.environ.get("FRED_API_KEY") or None


def _load_rf(series_id: str, source: Source, start: str, end: str | None):
    """금융금리 Series(퍼센트 단위) 또는 0.0. ffill 여유를 위해 시작 14일 전부터."""
    rf_start = (pd.Timestamp(start) - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
    if source is Source.db:
        try:
            from stocker.db import read_macro

            s = read_macro(series_id, start=rf_start, end=end)
            if s is not None and not s.empty:
                return s.astype(float)
            _warn(f"DB에 {series_id}가 없다")
        except Exception as e:  # noqa: BLE001
            _warn(f"DB에서 {series_id} 읽기 실패: {e}")
    key = _fred_api_key()
    if key:
        try:
            from fredapi import Fred

            s = Fred(api_key=key).get_series(
                series_id, observation_start=rf_start, observation_end=end
            )
            return s.dropna().astype(float)
        except Exception as e:  # noqa: BLE001
            _warn(f"FRED에서 {series_id} 받기 실패: {e}")
    _warn(f"{series_id}를 구하지 못해 금융금리 0으로 계산한다")
    return 0.0


def _print_table(rows: list[tuple[str, str]], title: str) -> None:
    try:
        from rich.console import Console
        from rich.table import Table

        table = Table(title=title)
        table.add_column("metric")
        table.add_column("value", justify="right")
        for k, v in rows:
            table.add_row(k, v)
        Console().print(table)
    except ImportError:
        print(title)
        width = max(len(k) for k, _ in rows)
        for k, v in rows:
            print(f"  {k.ljust(width)}  {v}")


@app.command()
def main(
    index: str = typer.Option("SOXX", help="지수 프록시 심볼 (조정종가 사용)"),
    leverage: float = typer.Option(3.0, help="레버리지 배수"),
    start: str = typer.Option("2022-01-01", help="시작일 YYYY-MM-DD"),
    end: str | None = typer.Option(None, help="종료일 YYYY-MM-DD (포함)"),
    compare: str | None = typer.Option(None, help="비교·캘리브레이션 대상 (예: SOXL)"),
    rf: str | None = typer.Option(None, help="금융금리 FRED 시리즈 (예: DFF, 퍼센트)"),
    source: Source = typer.Option(Source.db, help="데이터 소스"),
    expense_ratio: float = typer.Option(0.0075, help="연 총보수 (소수)"),
    spread: float = typer.Option(0.0, help="캘리브레이션 전 금융 스프레드 (연율 소수)"),
) -> None:
    load = _load_adj_close_db if source is Source.db else _load_adj_close_yf
    px = load(index, start, end)
    rets = px.pct_change()  # 첫 날 NaN → NAV 100에서 시작

    rf_val: pd.Series | float = 0.0
    if rf:
        rf_val = _load_rf(rf, source, start, end)
    # rf가 DFF 같은 퍼센트 시리즈라 가정 (0.08% 같은 저금리 구간 오판 방지).
    rate_scale = "percent" if isinstance(rf_val, pd.Series) else "decimal"

    sim = simulate_leveraged(
        rets,
        leverage=leverage,
        expense_ratio=expense_ratio,
        financing_rate=rf_val,
        financing_spread=spread,
        rate_scale=rate_scale,
    )
    rf_label = rf or "0 (constant)"
    if isinstance(rf_val, float) and rf:
        rf_label = f"{rf} unavailable -> 0"
    rows: list[tuple[str, str]] = [
        ("source", source.value),
        ("index / leverage", f"{index} x{leverage:g}"),
        ("financing rate", rf_label),
        ("expense ratio", f"{expense_ratio:.4%}"),
    ]

    if compare is None:
        rows += [
            ("start", f"{sim.index[0].date()}"),
            ("end", f"{sim.index[-1].date()}"),
            ("n_days", f"{len(sim) - 1}"),
            (f"sim final NAV (spread {spread:.2%})", f"{sim.iloc[-1]:.2f}"),
            (f"{index} final (norm 100)", f"{px.iloc[-1] / px.iloc[0] * 100:.2f}"),
        ]
        _print_table(rows, f"{index} x{leverage:g} simulation")
        return

    actual = load(compare, start, end)
    common = rets.index.intersection(actual.index)
    if len(common) < 3:
        raise typer.BadParameter(f"{index}와 {compare}의 공통 거래일이 부족하다")
    first, last = common[0], common[-1]
    res = calibrate(
        rets,
        actual,
        leverage=leverage,
        expense_ratio=expense_ratio,
        financing_rate=rf_val,
        rate_scale=rate_scale,
    )
    win = rets.loc[first:last].copy()
    win.iloc[0] = float("nan")
    kw = dict(
        leverage=leverage,
        expense_ratio=expense_ratio,
        financing_rate=rf_val,
        rate_scale=rate_scale,
    )
    sim_raw = simulate_leveraged(win, financing_spread=spread, **kw)
    sim_cal = simulate_leveraged(win, financing_spread=res.financing_spread, **kw)
    act_norm = actual.loc[last] / actual.loc[first] * 100.0

    rows += [
        ("start", f"{first.date()}"),
        ("end", f"{last.date()}"),
        ("n_days", f"{res.n_days}"),
        (f"sim final NAV (spread {spread:.2%})", f"{sim_raw.loc[last]:.2f}"),
        ("sim final NAV (calibrated)", f"{sim_cal.loc[last]:.2f}"),
        (f"{compare} actual final NAV", f"{act_norm:.2f}"),
        ("calibrated spread (annual)", f"{res.financing_spread:+.4%}"),
        ("annual tracking error", f"{res.annual_tracking_error:.4%}"),
        ("daily corr", f"{res.daily_corr:.5f}"),
        ("mean annual drift (sim-actual)", f"{res.mean_annual_drift:+.4%}"),
    ]
    _print_table(rows, f"{index} x{leverage:g} vs {compare}")


if __name__ == "__main__":
    app()

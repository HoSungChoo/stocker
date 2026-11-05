"""백테스트: 기준선·모양 우선·환경 우선을 같은 과거 시점들에서 비교한다.

계약: PHASE6_CONTRACT 25절.
"""

from stocker.backtest.harness import month_end_dates, run_backtest_from_inputs
from stocker.backtest.metrics import (
    ERAS,
    aggregate,
    interval_score,
    verdict,
)
from stocker.backtest.report import build_report

__all__ = [
    "ERAS",
    "aggregate",
    "build_report",
    "interval_score",
    "month_end_dates",
    "run_backtest_from_inputs",
    "verdict",
]

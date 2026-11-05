"""적합 기준 ("알맞은 과거가 없으면 적용하지 않는다"). 계약: docs/PHASE6_CONTRACT.md 23절.

유사도 점수는 상대 점수라 1위는 늘 0.95 안팎이다. 그래서 과거 국면이 지금과 "충분히" 닮았는지를
원 단위(퍼센트포인트, 거래일 수)로 따로 검사한다. 조건 5개:

| 조건 | 기준 |
|---|---|
| 낙폭 깊이 | 고점 대비 낙폭 차이 ≤ 8%p |
| 낙폭 기간 | 고점 이후 거래일 수 차이 ≤ 40% (긴 쪽 기준) |
| 금리 환경 | 기준금리 수준 차이 ≤ 2.0%p 그리고 최근 6개월 방향(인상/동결/인하) 같음 |
| 업황 방향 | WSTS 매출 YoY 부호 같음 |
| 변동성 | 60일 실현 변동성 차이 ≤ 40% (큰 쪽 기준) |

status: strong(5개), ok(3~4개), weak(2개 이하). 데이터가 없어 검사할 수 없는 조건은 통과로 치지
않는다(`passed=None`, 보수적). 모든 함수는 순수 함수이고 룩어헤드가 없다(각 날짜의 값은 그 날짜
이하의 데이터만으로 계산).

용어: 실현 변동성 = 최근 60거래일 일일 로그수익률의 표준편차 × √252 (연율, 소수).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from stocker.sim.leveraged import forward_returns
from stocker.similarity.config import SimilarityConfig
from stocker.similarity.features import WSTS_METRIC, _clean_series, build_cycle_features

TRADING_DAYS = 252
RATE_DIR_LABEL = {"up": "인상", "flat": "동결", "down": "인하"}
STATUS_ORDER = {"weak": 0, "ok": 1, "strong": 2}

# fit 특징 표의 컬럼
F_DRAWDOWN = "drawdown"  # 고점 대비 낙폭 (소수, 0 이하)
F_DD_DAYS = "dd_days"  # 고점 이후 거래일 수
F_RATE = "rate_level"  # 기준금리 DFF (%)
F_RATE_DIR = "rate_dir"  # "up" | "flat" | "down"
F_WSTS_YOY = "wsts_yoy"  # WSTS 매출 YoY (%)
F_VOL = "vol"  # 60일 실현 변동성 (연율, 소수)
FIT_COLUMNS = (F_DRAWDOWN, F_DD_DAYS, F_RATE, F_RATE_DIR, F_WSTS_YOY, F_VOL)


@dataclass
class FitCondition:
    name: str  # drawdown | dd_days | rate | cycle | volatility
    label: str  # 한국어 이름
    then: float | None  # 과거 국면 값 (원 단위)
    now: float | None  # 기준일 값 (원 단위)
    tolerance: float  # 허용 차이 (unit 단위)
    unit: str  # "%p" | "%" | "sign"
    passed: bool | None  # None = 데이터가 없어 검사 불가 (통과로 치지 않음)
    note: str | None = None  # 보조 정보 (예: 금리 방향 "인상 vs 동결")


@dataclass
class FitCheck:
    conditions: list[FitCondition]
    n_passed: int
    status: str  # strong | ok | weak
    n_evaluable: int = 5  # 데이터가 있어 실제로 검사한 조건 수

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FitFeatures:
    """한 시점의 적합 검사 입력. 없는 값은 None."""

    drawdown: float | None = None
    dd_days: int | None = None
    rate_level: float | None = None
    rate_dir: str | None = None
    wsts_yoy: float | None = None
    vol: float | None = None
    extra: dict = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return all(
            v is not None
            for v in (
                self.drawdown,
                self.dd_days,
                self.rate_level,
                self.rate_dir,
                self.wsts_yoy,
                self.vol,
            )
        )


# --------------------------------------------------------------------------- 특징
def _opt(x) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _bday_ffill(s: pd.Series, grid: pd.DatetimeIndex) -> pd.Series:
    return s.reindex(s.index.union(grid)).ffill().reindex(grid)


def rate_direction(change: pd.Series, threshold_pp: float) -> pd.Series:
    """DFF의 `change_days` 변화량(%p) → "up"/"flat"/"down". NaN은 None."""
    out = pd.Series(
        np.where(change > threshold_pp, "up", np.where(change < -threshold_pp, "down", "flat")),
        index=change.index,
        dtype=object,
    )
    return out.where(change.notna(), None)


def build_fit_features(
    close: pd.Series,
    macro: dict[str, pd.Series] | None,
    cycle: dict[str, pd.Series] | None,
    config: SimilarityConfig | None = None,
) -> pd.DataFrame:
    """거래일마다 적합 검사에 쓰는 원 단위 값. index=close의 거래일, columns=FIT_COLUMNS.

    close·macro·cycle은 호출자가 as_of 이하로 잘라 넘긴다(engine.score가 그렇게 한다). 각 행은 그
    날짜 이하의 데이터만 쓴다(running max, 과거 방향 변화, 월간 값의 발표 지연 ffill).
    """
    cfg = config or SimilarityConfig()
    s = _clean_series(close)
    idx = s.index
    out = pd.DataFrame(index=idx)
    out.index.name = "date"

    px = s.to_numpy()
    running_max = np.maximum.accumulate(px)
    out[F_DRAWDOWN] = px / running_max - 1.0
    pos = np.arange(len(px))
    high_pos = np.maximum.accumulate(np.where(px >= running_max, pos, 0))
    out[F_DD_DAYS] = (pos - high_pos).astype("float64")
    logret = np.log(s).diff()
    out[F_VOL] = logret.rolling(cfg.fit_vol_days).std(ddof=1) * math.sqrt(TRADING_DAYS)

    out[F_RATE] = np.nan
    out[F_RATE_DIR] = None
    dff = (macro or {}).get("DFF")
    if dff is not None and len(dff) > 0:
        d = _clean_series(dff)
        if not d.empty:
            grid = pd.bdate_range(min(d.index[0], idx[0]), idx[-1])
            on_grid = _bday_ffill(d, grid)
            change = on_grid - on_grid.shift(cfg.change_days)
            direction = rate_direction(change, cfg.fit_rate_dir_pp)
            out[F_RATE] = _bday_ffill(on_grid, idx).to_numpy()
            out[F_RATE_DIR] = (
                direction.reindex(direction.index.union(idx)).ffill().reindex(idx).to_numpy()
            )

    out[F_WSTS_YOY] = np.nan
    if cycle and WSTS_METRIC in cycle and len(cycle[WSTS_METRIC]) > 0:
        cfeats = build_cycle_features({WSTS_METRIC: cycle[WSTS_METRIC]})
        if not cfeats.empty and "wsts_yoy" in cfeats:
            yoy = cfeats["wsts_yoy"].dropna()
            if cfg.cycle_lag_days > 0:
                yoy = yoy.copy()
                yoy.index = yoy.index + pd.Timedelta(days=cfg.cycle_lag_days)
            yoy = yoy.loc[: idx[-1]]
            if not yoy.empty:
                out[F_WSTS_YOY] = _bday_ffill(yoy, idx).to_numpy()
    return out


def features_at(feats: pd.DataFrame, date: pd.Timestamp) -> FitFeatures:
    """build_fit_features 표의 한 행 → FitFeatures. 날짜가 없으면 모두 None."""
    if date not in feats.index:
        return FitFeatures()
    row = feats.loc[date]
    dd_days = _opt(row[F_DD_DAYS])
    rate_dir = row[F_RATE_DIR]
    return FitFeatures(
        drawdown=_opt(row[F_DRAWDOWN]),
        dd_days=None if dd_days is None else int(dd_days),
        rate_level=_opt(row[F_RATE]),
        rate_dir=None
        if rate_dir is None or (isinstance(rate_dir, float) and math.isnan(rate_dir))
        else str(rate_dir),
        wsts_yoy=_opt(row[F_WSTS_YOY]),
        vol=_opt(row[F_VOL]),
    )


# --------------------------------------------------------------------------- 검사
def _as_features(x) -> FitFeatures:
    if isinstance(x, FitFeatures):
        return x
    if isinstance(x, dict):
        known = {
            k: x.get(k)
            for k in ("drawdown", "dd_days", "rate_level", "rate_dir", "wsts_yoy", "vol")
        }
        return FitFeatures(**known)
    raise TypeError("then/now는 FitFeatures 또는 dict")


def _ratio_diff(a: float, b: float) -> float:
    """|a-b| / max(|a|,|b|). 둘 다 0이면 0."""
    m = max(abs(a), abs(b))
    return 0.0 if m == 0 else abs(a - b) / m


def _sign(x: float) -> int:
    return 0 if x == 0 else (1 if x > 0 else -1)


def status_of(n_passed: int) -> str:
    if n_passed >= 5:
        return "strong"
    if n_passed >= 3:
        return "ok"
    return "weak"


def check_fit(then_features, now_features, config: SimilarityConfig | None = None) -> FitCheck:
    """조건 5개를 원 단위로 검사한다. then=과거 국면(창 끝 날짜), now=기준일.

    값이 없는 조건은 passed=None이고 통과로 치지 않는다.
    """
    cfg = config or SimilarityConfig()
    t, n = _as_features(then_features), _as_features(now_features)
    conds: list[FitCondition] = []

    # 1. 낙폭 깊이 (%p)
    if t.drawdown is None or n.drawdown is None:
        conds.append(
            FitCondition(
                "drawdown",
                "낙폭 깊이",
                _pct(t.drawdown),
                _pct(n.drawdown),
                cfg.fit_drawdown_pp,
                "%p",
                None,
            )
        )
    else:
        diff = abs(t.drawdown - n.drawdown) * 100.0
        conds.append(
            FitCondition(
                "drawdown",
                "낙폭 깊이",
                t.drawdown * 100.0,
                n.drawdown * 100.0,
                cfg.fit_drawdown_pp,
                "%p",
                diff <= cfg.fit_drawdown_pp,
            )
        )

    # 2. 낙폭 기간 (거래일, 긴 쪽 기준 비율)
    if t.dd_days is None or n.dd_days is None:
        conds.append(
            FitCondition(
                "dd_days", "낙폭 기간", t.dd_days, n.dd_days, cfg.fit_dd_days_ratio * 100, "%", None
            )
        )
    else:
        conds.append(
            FitCondition(
                "dd_days",
                "낙폭 기간",
                float(t.dd_days),
                float(n.dd_days),
                cfg.fit_dd_days_ratio * 100,
                "%",
                _ratio_diff(t.dd_days, n.dd_days) <= cfg.fit_dd_days_ratio,
            )
        )

    # 3. 금리 환경 (수준 %p 그리고 방향)
    if t.rate_level is None or n.rate_level is None or t.rate_dir is None or n.rate_dir is None:
        conds.append(
            FitCondition(
                "rate",
                "금리 환경",
                t.rate_level,
                n.rate_level,
                cfg.fit_rate_pp,
                "%p",
                None,
                _dir_note(t.rate_dir, n.rate_dir),
            )
        )
    else:
        level_ok = abs(t.rate_level - n.rate_level) <= cfg.fit_rate_pp
        dir_ok = t.rate_dir == n.rate_dir
        conds.append(
            FitCondition(
                "rate",
                "금리 환경",
                t.rate_level,
                n.rate_level,
                cfg.fit_rate_pp,
                "%p",
                bool(level_ok and dir_ok),
                _dir_note(t.rate_dir, n.rate_dir),
            )
        )

    # 4. 업황 방향 (YoY 부호)
    if t.wsts_yoy is None or n.wsts_yoy is None:
        conds.append(FitCondition("cycle", "업황 방향", t.wsts_yoy, n.wsts_yoy, 0.0, "sign", None))
    else:
        conds.append(
            FitCondition(
                "cycle",
                "업황 방향",
                t.wsts_yoy,
                n.wsts_yoy,
                0.0,
                "sign",
                _sign(t.wsts_yoy) == _sign(n.wsts_yoy),
            )
        )

    # 5. 변동성 (연율 %, 큰 쪽 기준 비율)
    if t.vol is None or n.vol is None:
        conds.append(
            FitCondition(
                "volatility", "변동성", _pct(t.vol), _pct(n.vol), cfg.fit_vol_ratio * 100, "%", None
            )
        )
    else:
        conds.append(
            FitCondition(
                "volatility",
                "변동성",
                t.vol * 100.0,
                n.vol * 100.0,
                cfg.fit_vol_ratio * 100,
                "%",
                _ratio_diff(t.vol, n.vol) <= cfg.fit_vol_ratio,
            )
        )

    n_passed = sum(1 for c in conds if c.passed is True)
    n_eval = sum(1 for c in conds if c.passed is not None)
    return FitCheck(
        conditions=conds, n_passed=n_passed, status=status_of(n_passed), n_evaluable=n_eval
    )


def _pct(x: float | None) -> float | None:
    return None if x is None else x * 100.0


def _dir_note(a: str | None, b: str | None) -> str:
    la = RATE_DIR_LABEL.get(a, "불명") if a else "불명"
    lb = RATE_DIR_LABEL.get(b, "불명") if b else "불명"
    return f"방향 {la} vs {lb}"


def is_ok(fit: FitCheck) -> bool:
    return STATUS_ORDER.get(fit.status, 0) >= STATUS_ORDER["ok"]


# --------------------------------------------------------------------------- 기준선
def baseline_forward_summary(hist_close: pd.Series, horizons: tuple[int, ...]) -> pd.DataFrame:
    """매칭 없는 기준선: as_of까지의 전체 과거에서 같은 horizon의 이후 수익률 분포.

    hist_close는 as_of 이하로 잘린 종가. 끝 h행은 미래가 없어 NaN이 되므로 자연히
    "as_of 시점에 결과를 알 수 있었던" 창만 들어간다(룩어헤드 없음).
    """
    from stocker.similarity.engine import summarize_forward

    fwd = forward_returns(hist_close, horizons)
    return summarize_forward(fwd, horizons)


__all__ = [
    "FIT_COLUMNS",
    "FitCheck",
    "FitCondition",
    "FitFeatures",
    "baseline_forward_summary",
    "build_fit_features",
    "check_fit",
    "features_at",
    "is_ok",
    "rate_direction",
    "status_of",
]

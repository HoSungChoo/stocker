"""아침 보고서 파이프라인. 계약: docs/DATA_CONTRACT.md 12절.

흐름: refresh_data → build_report → render_markdown/html → report_runs 저장 → deliver.
계산은 stocker.similarity가 한다. 여기서는 결과를 모으고, 전일과 비교하고, 글로 만든다.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import smtplib
import urllib.request
import uuid
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from stocker.config import settings
from stocker.db import (
    get_engine,
    init_db,
    read_cycle,
    read_events,
    read_macro,
    read_prices,
    read_report_run,
    save_report_run,
)
from stocker.ingest import events as _events
from stocker.ingest import fred as _fred
from stocker.ingest import prices as _prices
from stocker.ingest import semi as _semi
from stocker.report import env
from stocker.report.llm import summarize
from stocker.similarity import SimilarityConfig, SimilarityResult
from stocker.similarity import loaders as _loaders

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).with_name("templates")
DASHBOARD_URL = "http://localhost:8501"
DEFAULT_WINDOWS = (120, 250)
UPCOMING_DAYS = 7
SOX_SYMBOL = "^SOX"

# 같은 국면으로 볼 끝 날짜 차이(달력일). 기준일이 하루 밀리면 최적 끝 날짜도 하루쯤 밀릴 수 있다.
SAME_MATCH_DAYS = 7
# 이 이하의 변화는 "변화 없음"으로 본다 (종합 점수 0~1 기준, 이후 수익 중앙값은 소수 기준).
EPS_COMBINED = 0.005
EPS_MEDIAN = 0.0005

LAYER_NAMES = {"price_shape": "가격 형태", "macro": "매크로", "semi_cycle": "산업 사이클"}
KIND_LABELS = {
    "fomc": "FOMC",
    "cpi": "CPI",
    "jobs": "고용보고서",
    "earnings": "실적",
    "policy": "정책",
    "war": "전쟁",
    "election": "선거",
}
WEEKDAYS = "월화수목금토일"

TELEGRAM_MAX_CHARS = 3500  # 텔레그램 한도는 4,096자. 여유를 둔다.
TELEGRAM_TOP_N = 3
TELEGRAM_MAX_EVENTS = 8
TELEGRAM_TRUNCATED = "\n…(전체는 첨부 파일)"


# ----------------------------------------------------------------- 데이터 구조


@dataclass
class Section:
    window: int
    result: SimilarityResult
    comparison: dict  # compare_with_previous 결과


@dataclass
class Report:
    as_of: pd.Timestamp
    sections: list[Section]
    upcoming_events: list[dict]  # 예정 이벤트(scheduled=true), 날짜 오름차순
    data_status: dict
    llm_summary: str | None = None
    generated_at: dt.datetime = field(default_factory=dt.datetime.now)
    dashboard_url: str = DASHBOARD_URL
    channels: list[str] = field(default_factory=list)  # deliver()가 채운다


class RefreshResult(dict):
    """단계 → 처리 행 수. 실패한 단계는 -1이고 사유가 `errors`에 있다."""

    def __init__(self) -> None:
        super().__init__()
        self.errors: dict[str, str] = {}


# ----------------------------------------------------------------- 직렬화


def _num(x) -> float | None:
    """NaN/None → None (Postgres jsonb는 NaN을 받지 않는다)."""
    if x is None:
        return None
    x = float(x)
    return None if x != x else x


def _iso(ts) -> str | None:
    return None if ts is None or pd.isna(ts) else pd.Timestamp(ts).strftime("%Y-%m-%d")


def result_to_dict(result: SimilarityResult) -> dict:
    """SimilarityResult → JSON 가능한 dict. Timestamp는 ISO 문자열, NaN은 None."""
    cfg = result.config
    summary = result.forward_summary.reset_index()
    return {
        "as_of": _iso(result.as_of),
        "window": cfg.window,
        "top_k": cfg.top_k,
        "min_gap_days": cfg.min_gap_days,
        "cycle_lag_days": cfg.cycle_lag_days,
        "horizons": [int(h) for h in cfg.horizons],
        "n_candidates": int(result.n_candidates),
        "layers_missing": list(result.layers_missing),
        "weights_used": {k: float(v) for k, v in result.weights_used.items()},
        "matches": [
            {
                "start_date": _iso(m.start_date),
                "end_date": _iso(m.end_date),
                "scores": {k: _num(v) for k, v in m.scores.items()},
                "combined": _num(m.combined),
                "forward": {str(h): _num(v) for h, v in m.forward.items()},
            }
            for m in result.matches
        ],
        "forward_summary": [
            {
                "horizon": int(r["horizon"]),
                "median": _num(r["median"]),
                "p10": _num(r["p10"]),
                "p90": _num(r["p90"]),
                "n_up": int(r["n_up"]),
                "n_down": int(r["n_down"]),
                "n": int(r["n"]),
            }
            for r in summary.to_dict("records")
        ],
    }


# ----------------------------------------------------------------- 전일 대비


def _as_dict(result) -> dict:
    return result_to_dict(result) if isinstance(result, SimilarityResult) else result


def _days_apart(a: str, b: str) -> int:
    return abs((pd.Timestamp(a) - pd.Timestamp(b)).days)


def _pair_matches(cur: list[dict], prev: list[dict]) -> list[tuple[dict, dict | None]]:
    """현재 국면마다 끝 날짜가 가장 가까운(SAME_MATCH_DAYS 이내) 전일 국면을 하나씩 짝짓는다."""
    free = list(prev)
    pairs: list[tuple[dict, dict | None]] = []
    for m in cur:
        best = min(free, key=lambda p: _days_apart(m["end_date"], p["end_date"]), default=None)
        if best is not None and _days_apart(m["end_date"], best["end_date"]) <= SAME_MATCH_DAYS:
            free.remove(best)
            pairs.append((m, best))
        else:
            pairs.append((m, None))
    return pairs


def _delta(cur: float | None, prev: float | None) -> float | None:
    return None if cur is None or prev is None else cur - prev


def compare_with_previous(result, previous_result) -> dict:
    """상위 K 구성 변화(새로 진입/이탈), 종합 점수 변화, forward 중앙값 변화.

    result는 SimilarityResult, previous_result는 SimilarityResult 또는 저장된 JSON dict
    (없으면 None → 첫 보고서). 국면은 끝 날짜로 식별하되 SAME_MATCH_DAYS 이내로 움직인 것은
    같은 국면이 하루쯤 이동한 것으로 본다(기준일이 하루 바뀌면 흔히 생긴다).
    """
    cur = _as_dict(result)
    if previous_result is None:
        return {"has_previous": False, "unchanged": False}
    prev = _as_dict(previous_result)

    pairs = _pair_matches(cur["matches"], prev["matches"])
    kept_prev_ends = {p["end_date"] for _, p in pairs if p is not None}
    entered = [
        {"end_date": m["end_date"], "start_date": m["start_date"], "combined": m["combined"]}
        for m, p in pairs
        if p is None
    ]
    left = [
        {"end_date": p["end_date"], "combined": p["combined"]}
        for p in prev["matches"]
        if p["end_date"] not in kept_prev_ends
    ]
    kept = [
        {
            "end_date": m["end_date"],
            "previous_end_date": p["end_date"],
            "shift_days": (pd.Timestamp(m["end_date"]) - pd.Timestamp(p["end_date"])).days,
            "combined": m["combined"],
            "previous_combined": p["combined"],
            "delta": _delta(m["combined"], p["combined"]),
        }
        for m, p in pairs
        if p is not None
    ]

    prev_fs = {r["horizon"]: r for r in prev["forward_summary"]}
    median_changes = []
    for r in cur["forward_summary"]:
        before = prev_fs.get(r["horizon"], {}).get("median")
        median_changes.append(
            {
                "horizon": r["horizon"],
                "current": r["median"],
                "previous": before,
                "delta": _delta(r["median"], before),
            }
        )

    def small(d: float | None, eps: float) -> bool:
        return d is None or abs(d) < eps

    unchanged = (
        not entered
        and not left
        and all(k["shift_days"] == 0 and small(k["delta"], EPS_COMBINED) for k in kept)
        and all(small(c["delta"], EPS_MEDIAN) for c in median_changes)
        and cur["layers_missing"] == prev["layers_missing"]
    )
    return {
        "has_previous": True,
        "previous_as_of": prev["as_of"],
        "same_day": prev["as_of"] == cur["as_of"],
        "entered": entered,
        "left": left,
        "kept": kept,
        "median_changes": median_changes,
        "unchanged": unchanged,
    }


# ----------------------------------------------------------------- 데이터 갱신


def refresh_data(engine=None, today: dt.date | None = None) -> RefreshResult:
    """prices(최근 30일), fred(최근 60일), semi, events 순으로 수집한다.

    모두 upsert라 겹쳐 받아도 중복이 생기지 않는다. 한 단계가 실패해도(네트워크, 키 없음 등)
    사유를 `errors`에 남기고 다음 단계로 간다. 실패한 단계의 값은 -1.
    """
    today = today or dt.date.today()
    steps = {
        "prices": lambda: _prices.ingest_prices(
            _prices.DEFAULT_SYMBOLS,
            start=(today - dt.timedelta(days=30)).isoformat(),
            engine=engine,
        ),
        "fred": lambda: _fred.ingest_fred(
            _fred.DEFAULT_SERIES, start=(today - dt.timedelta(days=60)).isoformat(), engine=engine
        ),
        "semi": lambda: _semi.ingest_semi(engine=engine),
        "events": lambda: _events.ingest_events(engine=engine),
    }
    out = RefreshResult()
    for name, step in steps.items():
        try:
            out[name] = int(step())
        except Exception as exc:  # noqa: BLE001  (한 소스 실패가 보고서 전체를 막지 않게)
            logger.warning("%s 갱신 실패, 건너뜀: %s", name, exc)
            out[name] = -1
            out.errors[name] = f"{exc.__class__.__name__}: {exc}"[:200]
    return out


# ----------------------------------------------------------------- 보고서 만들기


def report_windows() -> tuple[int, ...]:
    return parse_windows(env("REPORT_WINDOWS", ",".join(map(str, DEFAULT_WINDOWS))))


def parse_windows(text: str) -> tuple[int, ...]:
    try:
        windows = tuple(int(t) for t in text.split(",") if t.strip())
    except ValueError as exc:
        raise ValueError(f"REPORT_WINDOWS는 쉼표 구분 정수여야 한다: {text!r}") from exc
    if not windows:
        raise ValueError("REPORT_WINDOWS가 비었다")
    return windows


def _last_date(read, *args, engine) -> str | None:
    try:
        s = read(*args, engine=engine)
    except Exception:  # noqa: BLE001
        return None
    return None if s.empty else _iso(s.index.max())


def collect_data_status(
    engine, config: SimilarityConfig, refresh: RefreshResult | None, layers_missing: list[str]
) -> dict:
    """소스별 마지막 날짜, 오늘 갱신 행 수, 꺼진 레이어."""
    macro_last = [
        d for sid in config.macro_series if (d := _last_date(read_macro, sid, engine=engine))
    ]
    macro_range = None
    if macro_last:
        lo, hi = min(macro_last), max(macro_last)
        macro_range = hi if lo == hi else f"{lo} ~ {hi}"
    ev_last, ev_note = None, ""
    try:
        ev = read_events(engine=engine)
        ev_last, ev_note = _iso(ev["date"].max()), f"총 {len(ev):,}행 (예정 일정 포함)"
    except Exception:  # noqa: BLE001
        pass
    sox_last = _last_date(
        lambda s, engine: read_prices(s, engine=engine)["close"], SOX_SYMBOL, engine=engine
    )
    sources = [
        {"name": f"시세 ({SOX_SYMBOL} 등 4종)", "key": "prices", "last_date": sox_last, "note": ""},
        {
            "name": "FRED 매크로",
            "key": "fred",
            "last_date": macro_range,
            "note": f"{len(macro_last)}/{len(config.macro_series)}개 시리즈",
        },
        {
            "name": "산업 사이클 (WSTS 월 매출)",
            "key": "semi",
            "last_date": _last_date(read_cycle, "wsts_sales_world_usd_m", engine=engine),
            "note": "월간 값이라 한 달 반쯤 늦게 나온다",
        },
        {"name": "이벤트", "key": "events", "last_date": ev_last, "note": ev_note},
    ]
    for s in sources:
        if refresh is None:
            s["refreshed"] = "갱신 안 함"
        elif s["key"] in refresh.errors:
            s["refreshed"] = f"실패 ({refresh.errors[s['key']]})"
        else:
            s["refreshed"] = f"{refresh.get(s['key'], 0):,}행"
    warnings = []
    if sox_last is not None:
        age = (dt.date.today() - dt.date.fromisoformat(sox_last)).days
        if age > 4:
            warnings.append(f"SOX 시세가 {age}일 전 값이다. 시세 갱신이 안 되고 있을 수 있다.")
    return {
        "sources": sources,
        "layers_missing": list(layers_missing),
        "warnings": warnings,
    }


def _upcoming_events(engine, start: dt.date, days: int = UPCOMING_DAYS) -> list[dict]:
    try:
        df = read_events(start=start, end=start + dt.timedelta(days=days), engine=engine)
    except Exception as exc:  # noqa: BLE001
        logger.warning("이벤트 조회 실패, 건너뜀: %s", exc)
        return []
    df = df[df["scheduled"].fillna(False).astype(bool)]
    out = []
    for r in df.itertuples(index=False):
        out.append(
            {
                "date": r.date.strftime("%Y-%m-%d"),
                "weekday": WEEKDAYS[r.date.weekday()],
                "kind": r.kind,
                "label": KIND_LABELS.get(r.kind, r.kind),
                "title": r.title,
                "symbol": None if pd.isna(r.symbol) else r.symbol,
            }
        )
    return out


def _previous_result(engine, window: int, anchor: pd.Timestamp) -> dict | None:
    """전 거래일 보고서의 창별 결과. 없으면 같은 기준일의 이전 실행(다시 실행한 경우)."""
    for kwargs in ({"before": anchor.date()}, {"date": anchor.date()}):
        try:
            row = read_report_run(window=window, engine=engine, **kwargs)
        except Exception as exc:  # noqa: BLE001  (테이블이 아직 없는 DB 등)
            logger.warning("이전 보고서 조회 실패: %s", exc.__class__.__name__)
            return None
        if row and row["result"]:
            return row["result"]
    return None


def build_report(
    as_of=None,
    windows=None,
    engine=None,
    refresh: RefreshResult | None = None,
    today: dt.date | None = None,
    use_llm: bool = True,
) -> Report:
    """창별로 유사도 엔진을 돌리고 전일 결과와 비교해 Report를 만든다.

    as_of=None이면 DB의 마지막 거래일. `today`는 "이번 주 예정 이벤트"의 시작일이며,
    as_of를 직접 준 경우(과거 재현)에는 as_of 다음 날부터 센다.
    """
    engine = engine or get_engine()
    windows = tuple(windows) if windows else report_windows()
    requested = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.today()

    sections: list[Section] = []
    for w in windows:
        result = _loaders.run(requested, SimilarityConfig(window=w), engine=engine)
        prev = _previous_result(engine, w, result.as_of)
        sections.append(Section(w, result, compare_with_previous(result, prev)))
    anchor = sections[0].result.as_of

    layers_missing = sorted({m for s in sections for m in s.result.layers_missing})
    if today is None:
        today = dt.date.today() if as_of is None else (anchor + pd.Timedelta(days=1)).date()
    report = Report(
        as_of=anchor,
        sections=sections,
        upcoming_events=_upcoming_events(engine, today),
        data_status=collect_data_status(engine, sections[0].result.config, refresh, layers_missing),
    )
    if use_llm:
        report.llm_summary = summarize(report)
    return report


# ----------------------------------------------------------------- 렌더링


def pct(x, signed: bool = True) -> str:
    """소수 수익률 → 퍼센트 소수 1자리. 결측은 '—'."""
    if x is None or pd.isna(x):
        return "—"
    return f"{x * 100:+.1f}%" if signed else f"{x * 100:.1f}%"


def pp(x) -> str:
    """소수 차이 → 퍼센트포인트 소수 1자리."""
    return "—" if x is None or pd.isna(x) else f"{x * 100:+.1f}%p"


def score_fmt(x) -> str:
    return "—" if x is None or pd.isna(x) else f"{x:.2f}"


def horizon_label(h: int) -> str:
    months = round(h / 21)
    return f"{months}개월 후"


def _describe_changes(cmp: dict) -> dict:
    """전일 대비 변화를 템플릿이 그대로 쓰는 문장 목록으로 바꾼다."""
    if not cmp["has_previous"]:
        return {"status": "first", "headline": "첫 보고서라 비교할 이전 결과가 없다.", "lines": []}
    basis = cmp["previous_as_of"] + (", 같은 기준일의 이전 실행" if cmp["same_day"] else "")
    if cmp["unchanged"]:
        return {"status": "unchanged", "headline": f"변화 없음 (비교 기준: {basis})", "lines": []}
    lines = []
    for m in cmp["entered"]:
        lines.append(f"새로 들어온 국면: {m['end_date']} (종합 {score_fmt(m['combined'])})")
    for m in cmp["left"]:
        lines.append(f"빠진 국면: {m['end_date']} (이전 종합 {score_fmt(m['combined'])})")
    for k in cmp["kept"]:
        if k["shift_days"]:
            lines.append(
                f"같은 국면이 이동: {k['previous_end_date']} → {k['end_date']} "
                f"({k['shift_days']:+d}일)"
            )
        if k["delta"] is not None and abs(k["delta"]) >= EPS_COMBINED:
            lines.append(
                f"종합 점수 변화 {k['end_date']}: {score_fmt(k['previous_combined'])} → "
                f"{score_fmt(k['combined'])} ({k['delta']:+.2f})"
            )
    for c in cmp["median_changes"]:
        if c["delta"] is not None and abs(c["delta"]) >= EPS_MEDIAN:
            lines.append(
                f"이후 수익 중앙값 {horizon_label(c['horizon'])}: {pct(c['previous'])} → "
                f"{pct(c['current'])} ({pp(c['delta'])})"
            )
    return {"status": "changed", "headline": f"비교 기준: {basis}", "lines": lines}


def _section_view(section: Section) -> dict:
    res = section.result
    cfg = res.config
    long_h = max(cfg.horizons)
    rows = []
    for rank, m in enumerate(res.matches, 1):
        start = _iso(m.start_date)
        rows.append(
            {
                "rank": rank,
                "period": f"{start} ~ {_iso(m.end_date)}" if start else _iso(m.end_date),
                "layers": [score_fmt(m.scores.get(k)) for k in LAYER_NAMES],
                "combined": score_fmt(m.combined),
                "fwd": pct(m.forward.get(long_h)),
            }
        )
    fs = res.forward_summary
    summary_rows = [
        {
            "label": horizon_label(h),
            "median": pct(r["median"]),
            "p10": pct(r["p10"]),
            "p90": pct(r["p90"]),
            "up": int(r["n_up"]),
            "down": int(r["n_down"]),
            "n": int(r["n"]),
        }
        for h, r in fs.iterrows()
    ]
    last = fs.loc[long_h]
    return {
        "window": cfg.window,
        "approx": "약 6개월" if cfg.window == 120 else f"약 {round(cfg.window / 21)}개월",
        "n": len(res.matches),
        "n_candidates": res.n_candidates,
        "layers_missing": [LAYER_NAMES.get(m, m) for m in res.layers_missing],
        "weights": " · ".join(
            f"{LAYER_NAMES.get(k, k)} {v:.0%}" for k, v in res.weights_used.items()
        ),
        "layer_headers": list(LAYER_NAMES.values()),
        "fwd_label": horizon_label(long_h),
        "rows": rows,
        "summary_rows": summary_rows,
        "change": _describe_changes(section.comparison),
        "footer": (
            f"{horizon_label(long_h)} 기준 상승 {int(last['n_up'])}개 · "
            f"하락 {int(last['n_down'])}개 (결과가 나온 {int(last['n'])}개 중)"
        ),
    }


def _context(report: Report, include_summary: bool = True) -> dict:
    return {
        "title": f"SOX 국면 보고서 — {report.as_of:%Y-%m-%d}",
        "as_of": f"{report.as_of:%Y-%m-%d}",
        "generated_at": f"{report.generated_at:%Y-%m-%d %H:%M}",
        "status": report.data_status,
        "sections": [_section_view(s) for s in report.sections],
        "events": report.upcoming_events,
        "summary": report.llm_summary if include_summary else None,
        "dashboard_url": report.dashboard_url,
    }


def _env(autoescape: bool) -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=autoescape,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def render_markdown(report: Report, include_summary: bool = True) -> str:
    """templates/report.md.j2. 계약 12.4의 7개 섹션 순서."""
    return _env(False).get_template("report.md.j2").render(**_context(report, include_summary))


def render_section_markdown(report: Report, window: int) -> str:
    """창 하나의 섹션만 Markdown으로 (report_runs의 window별 행에 저장)."""
    ctx = _context(report)
    section = next(s for s in ctx["sections"] if s["window"] == window)
    module = _env(False).get_template("report.md.j2").make_module(ctx)
    return module.section(section)


def render_telegram(report: Report) -> str:
    """templates/report.telegram.j2. 평문 요약(표 없음, 3,500자 이하). 전체는 .md 첨부로 보낸다."""
    sections = []
    for s in report.sections:
        view = _section_view(s)
        last = view["summary_rows"][-1]  # 가장 긴 기간(12개월)
        sections.append(
            view
            | {
                "top": view["rows"][:TELEGRAM_TOP_N],
                "median": last["median"],
                "up": last["up"],
                "down": last["down"],
            }
        )
    counts = sorted({v["n"] for v in sections})
    ctx = _context(report) | {
        "sections": sections,
        "max_events": TELEGRAM_MAX_EVENTS,
        "samples": "·".join(map(str, counts)),
    }
    text = _env(False).get_template("report.telegram.j2").render(**ctx).strip()
    if len(text) > TELEGRAM_MAX_CHARS:
        text = text[: TELEGRAM_MAX_CHARS - len(TELEGRAM_TRUNCATED)].rstrip() + TELEGRAM_TRUNCATED
    return text


def render_html(report: Report) -> str:
    """templates/report.html.j2. 이메일용이라 CSS는 모두 인라인."""
    return _env(True).get_template("report.html.j2").render(**_context(report))


# ----------------------------------------------------------------- 전달


def _send_email(subject: str, md: str, html: str) -> None:
    host, user, to = env("SMTP_HOST"), env("SMTP_USER"), env("REPORT_EMAIL_TO")
    port = int(env("SMTP_PORT", "587"))
    msg = EmailMessage()
    msg["Subject"], msg["From"] = subject, user
    msg["To"] = ", ".join(t.strip() for t in to.split(",") if t.strip())
    msg.set_content(md)
    msg.add_alternative(html, subtype="html")
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
    with server:
        if port != 465:
            server.starttls()
        server.login(user, env("SMTP_PASSWORD"))
        server.send_message(msg)


def _telegram_post(token: str, method: str, body: bytes, content_type: str) -> None:
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=body,
        headers={"Content-Type": content_type},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def _multipart(fields: dict[str, str], filename: str, content: bytes) -> tuple[bytes, str]:
    """sendDocument용 multipart/form-data 본문 (stdlib만 사용). (본문, Content-Type)을 돌려준다."""
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="document"; '
        f'filename="{filename}"\r\nContent-Type: text/markdown; charset=utf-8\r\n\r\n'.encode()
        + content
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _send_telegram(report: Report, md: str) -> None:
    """(1) 평문 요약 sendMessage, (2) 전체 .md를 sendDocument로 첨부. 계약 12.1."""
    token, chat_id = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")
    message = json.dumps(
        {"chat_id": chat_id, "text": render_telegram(report), "disable_web_page_preview": True}
    ).encode("utf-8")
    _telegram_post(token, "sendMessage", message, "application/json")
    body, content_type = _multipart(
        {"chat_id": chat_id}, f"{report.as_of:%Y-%m-%d}.md", md.encode("utf-8")
    )
    _telegram_post(token, "sendDocument", body, content_type)


def _email_configured() -> bool:
    names = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "REPORT_EMAIL_TO")
    return all(env(n) for n in names)


def _telegram_configured() -> bool:
    return all(env(n) for n in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"))


def deliver(report: Report, md: str, html: str) -> list[str]:
    """파일(항상) + 설정된 채널로 보낸다. 켜진 채널 이름 목록을 돌려준다.

    미설정 채널은 조용히 건너뛰고, 보내다 실패한 채널은 경고만 남기고 목록에서 뺀다.
    """
    out_dir = Path(settings.data_dir) / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{report.as_of:%Y-%m-%d}"
    (out_dir / f"{stem}.md").write_text(md, encoding="utf-8")
    (out_dir / f"{stem}.html").write_text(html, encoding="utf-8")
    channels = ["file"]

    subject = f"SOX 국면 보고서 — {stem}"
    if _email_configured():
        try:
            _send_email(subject, md, html)
            channels.append("email")
        except Exception as exc:  # noqa: BLE001
            logger.warning("이메일 전송 실패: %s", exc.__class__.__name__)
    if _telegram_configured():
        try:
            _send_telegram(report, md)
            channels.append("telegram")
        except Exception as exc:  # noqa: BLE001  (URL에 토큰이 있어 메시지를 남기지 않는다)
            logger.warning("텔레그램 전송 실패: %s", exc.__class__.__name__)
    return channels


# ----------------------------------------------------------------- 저장·실행


def store_report(report: Report, md: str, engine=None) -> None:
    """창별 행(window>0: 결과 JSON + 그 창의 Markdown)과 전체 행(window=0)을 upsert."""
    run_date = report.as_of.date()
    for s in report.sections:
        payload = result_to_dict(s.result) | {"comparison": s.comparison}
        save_report_run(
            run_date,
            s.window,
            payload,
            render_section_markdown(report, s.window),
            engine=engine,
        )
    overview = {
        "as_of": _iso(report.as_of),
        "windows": [s.window for s in report.sections],
        "generated_at": report.generated_at.isoformat(timespec="seconds"),
        "data_status": report.data_status,
        "upcoming_events": report.upcoming_events,
        "llm_summary": report.llm_summary,
    }
    save_report_run(run_date, 0, overview, md, engine=engine)


def run_daily(
    engine=None,
    as_of=None,
    windows=None,
    refresh: bool = True,
    send: bool = True,
    today: dt.date | None = None,
) -> Report:
    """refresh → build → render → store(report_runs) → deliver.

    send=False면 파일도 쓰지 않고 DB에만 남긴다. 켜진 전달 채널은 `report.channels`.
    """
    engine = engine or get_engine()
    init_db(engine)  # report_runs가 없는 예전 DB도 그대로 돌아가게 (멱등)
    refreshed = refresh_data(engine, today=today) if refresh else None
    report = build_report(as_of, windows, engine=engine, refresh=refreshed, today=today)
    md, html = render_markdown(report), render_html(report)
    store_report(report, md, engine=engine)
    if send:
        report.channels = deliver(report, md, html)
    return report

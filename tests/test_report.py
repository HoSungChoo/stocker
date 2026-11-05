"""아침 보고서 테스트. 네트워크·LLM 호출 없이 합성 데이터로 검증한다 (계약 12.6)."""

import datetime as dt
import json
import urllib.request
from email import message_from_bytes

import pandas as pd
import pytest
import sqlalchemy as sa

from stocker.config import settings
from stocker.report import env as report_env
from stocker.report import llm, pipeline, scheduler
from stocker.similarity import Match, SimilarityConfig, SimilarityResult

HORIZONS = (21, 63, 126, 252)


@pytest.fixture(autouse=True)
def _no_external_config(monkeypatch):
    """개발 머신의 환경변수·.env가 테스트에 섞이지 않게 한다 (특히 OPENAI_API_KEY)."""
    for name in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "OPENAI_BASE_URL",
        "REPORT_LLM",
        "REPORT_TIME",
        "REPORT_WINDOWS",
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "REPORT_EMAIL_TO",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("stocker.report.dotenv_values", lambda *_a, **_k: {})


def make_result(
    as_of="2026-11-04",
    ends=("2000-08-08", "2021-11-02", "2024-09-26"),
    combined=(0.95, 0.93, 0.92),
    median_252=0.25,
    window=120,
    layers_missing=(),
) -> SimilarityResult:
    cfg = SimilarityConfig(window=window)
    matches = [
        Match(
            end_date=pd.Timestamp(e),
            scores={"price_shape": 0.9, "macro": float("nan"), "semi_cycle": 0.7},
            combined=c,
            forward={21: 0.01, 63: -0.02, 126: 0.1, 252: float("nan") if i == 2 else 0.3},
            start_date=pd.Timestamp(e) - pd.Timedelta(days=170),
        )
        for i, (e, c) in enumerate(zip(ends, combined, strict=True))
    ]
    fs = pd.DataFrame(
        {
            "median": [0.0, 0.01, 0.05, median_252],
            "p10": [-0.1, -0.1, -0.2, -0.3],
            "p90": [0.1, 0.1, 0.3, 0.5],
            "n_up": [2, 2, 2, 2],
            "n_down": [1, 1, 1, 0],
            "n": [3, 3, 3, 2],
        },
        index=pd.Index(HORIZONS, name="horizon"),
    )
    return SimilarityResult(
        as_of=pd.Timestamp(as_of),
        config=cfg,
        matches=matches,
        n_candidates=7000,
        layers_missing=list(layers_missing),
        forward_summary=fs,
        weights_used={"price_shape": 0.7, "semi_cycle": 0.3},
    )


def make_report(summary: str | None = "요약 문장입니다.", previous=None) -> pipeline.Report:
    res = make_result()
    sections = [
        pipeline.Section(120, res, pipeline.compare_with_previous(res, previous)),
        pipeline.Section(
            250, make_result(window=250), pipeline.compare_with_previous(res, previous)
        ),
    ]
    status = {
        "sources": [
            {
                "name": "시세",
                "key": "prices",
                "last_date": "2026-11-04",
                "note": "",
                "refreshed": "10행",
            }
        ],
        "layers_missing": ["macro"],
        "warnings": [],
    }
    events = [
        {
            "date": "2026-11-06",
            "weekday": "금",
            "kind": "jobs",
            "label": "고용보고서",
            "title": "발표",
            "symbol": None,
        }
    ]
    return pipeline.Report(
        as_of=res.as_of,
        sections=sections,
        upcoming_events=events,
        data_status=status,
        llm_summary=summary,
        generated_at=dt.datetime(2026, 11, 5, 7, 30),
    )


# --- 직렬화


def test_result_to_dict_is_strict_json_and_nan_becomes_none():
    d = pipeline.result_to_dict(make_result())
    json.dumps(d, allow_nan=False)  # NaN이 남아 있으면 ValueError
    assert d["as_of"] == "2026-11-04"
    assert d["matches"][0]["scores"]["macro"] is None
    assert d["matches"][2]["forward"]["252"] is None
    assert d["forward_summary"][-1] == {
        "horizon": 252,
        "median": 0.25,
        "p10": -0.3,
        "p90": 0.5,
        "n_up": 2,
        "n_down": 0,
        "n": 2,
    }


# --- 렌더링


def test_markdown_has_seven_sections_in_order():
    md = pipeline.render_markdown(make_report())
    markers = [
        "# SOX 국면 보고서 — 2026-11-04",
        "## 데이터 상태",
        "## 창별 결과",
        "## 이번 주 예정 이벤트",
        "## 요약 (AI)",
        "## 읽는 법과 한계",
        "## 대시보드",
    ]
    positions = [md.index(m) for m in markers]
    assert positions == sorted(positions)
    assert "### 120거래일 창" in md and "### 250거래일 창" in md
    assert "**전일 대비 변화**" in md
    assert "http://localhost:8501" in md
    assert "예측하지 않는다" in md
    assert "+30.0%" in md  # 퍼센트 소수 1자리
    assert "—" in md  # 결측(NaN) 표시


def test_markdown_omits_summary_section_without_llm():
    md = pipeline.render_markdown(make_report(summary=None))
    assert "## 요약 (AI)" not in md


def test_html_is_self_contained_and_escapes():
    report = make_report(summary="<script>alert(1)</script>")
    html = pipeline.render_html(report)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert 'style="' in html and "<link" not in html


def test_section_markdown_is_just_one_window():
    md = pipeline.render_section_markdown(make_report(), 250)
    assert md.startswith("### 250거래일 창")
    assert "120거래일" not in md


# --- 전일 대비


def test_compare_first_report():
    cmp = pipeline.compare_with_previous(make_result(), None)
    assert cmp == {"has_previous": False, "unchanged": False}
    change = pipeline._describe_changes(cmp)
    assert change["status"] == "first"


def test_compare_entered_and_left():
    prev = make_result(
        as_of="2026-11-03",
        ends=("2000-08-08", "2021-11-02", "2010-06-01"),
        combined=(0.95, 0.93, 0.90),
    )
    cur = make_result(ends=("2000-08-08", "2021-11-02", "2024-09-26"), combined=(0.95, 0.93, 0.92))
    cmp = pipeline.compare_with_previous(cur, pipeline.result_to_dict(prev))
    assert [m["end_date"] for m in cmp["entered"]] == ["2024-09-26"]
    assert [m["end_date"] for m in cmp["left"]] == ["2010-06-01"]
    assert not cmp["unchanged"]
    assert cmp["same_day"] is False
    lines = pipeline._describe_changes(cmp)["lines"]
    assert any("새로 들어온 국면: 2024-09-26" in x for x in lines)
    assert any("빠진 국면: 2010-06-01" in x for x in lines)


def test_compare_score_and_median_changes_are_reported():
    prev = make_result(as_of="2026-11-03", combined=(0.95, 0.93, 0.80), median_252=0.20)
    cmp = pipeline.compare_with_previous(make_result(), prev)
    assert cmp["entered"] == [] and cmp["left"] == []
    by_end = {k["end_date"]: k for k in cmp["kept"]}
    assert by_end["2024-09-26"]["delta"] == pytest.approx(0.12)
    med = {c["horizon"]: c for c in cmp["median_changes"]}
    assert med[252]["delta"] == pytest.approx(0.05)
    lines = pipeline._describe_changes(cmp)["lines"]
    assert any("12개월 후" in x and "+5.0%p" in x for x in lines)


def test_compare_identical_is_unchanged_even_from_stored_json():
    res = make_result()
    stored = json.loads(json.dumps(pipeline.result_to_dict(res)))  # DB 왕복
    cmp = pipeline.compare_with_previous(res, stored)
    assert cmp["unchanged"] and cmp["same_day"]
    assert pipeline._describe_changes(cmp)["status"] == "unchanged"


def test_compare_treats_one_day_shift_as_same_match():
    prev = make_result(as_of="2026-11-03", ends=("2000-08-09", "2021-11-02", "2024-09-26"))
    cmp = pipeline.compare_with_previous(make_result(), prev)
    assert cmp["entered"] == [] and cmp["left"] == []
    assert cmp["kept"][0]["shift_days"] == -1
    assert not cmp["unchanged"]


# --- 전달


def test_deliver_without_channels_writes_files_only(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(pipeline, "_send_email", lambda *a: pytest.fail("이메일이 불렸다"))
    monkeypatch.setattr(pipeline, "_send_telegram", lambda *a: pytest.fail("텔레그램이 불렸다"))
    channels = pipeline.deliver(make_report(), "# md", "<html></html>")
    assert channels == ["file"]
    assert (tmp_path / "reports" / "2026-11-04.md").read_text(encoding="utf-8") == "# md"
    assert (tmp_path / "reports" / "2026-11-04.html").exists()


def test_deliver_partial_email_config_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")  # 나머지가 없으면 꺼진 채널
    monkeypatch.setattr(pipeline, "_send_email", lambda *a: pytest.fail("이메일이 불렸다"))
    assert pipeline.deliver(make_report(), "md", "html") == ["file"]


def test_deliver_enabled_channels_are_listed_and_failures_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    for k, v in {
        "SMTP_HOST": "h",
        "SMTP_PORT": "587",
        "SMTP_USER": "u",
        "SMTP_PASSWORD": "p",
        "REPORT_EMAIL_TO": "a@example.com",
        "TELEGRAM_BOT_TOKEN": "t",
        "TELEGRAM_CHAT_ID": "1",
    }.items():
        monkeypatch.setenv(k, v)
    sent = []
    monkeypatch.setattr(pipeline, "_send_email", lambda *a: sent.append("email"))

    def boom(*_a):
        raise OSError("network down")

    monkeypatch.setattr(pipeline, "_send_telegram", boom)
    assert pipeline.deliver(make_report(), "md", "html") == ["file", "email"]
    assert sent == ["email"]


# --- 텔레그램


def test_telegram_text_is_short_plain_and_has_required_parts():
    text = pipeline.render_telegram(make_report())
    assert len(text) <= 3500
    assert "|" not in text and "---" not in text  # 표·Markdown 구분선 없음
    assert "SOX 국면 보고서 — 2026-11-04" in text and "2026-11-04" in text
    assert "[120거래일 창" in text and "[250거래일 창" in text
    assert "2000-02-20 ~ 2000-08-08 → 12개월 후 +30.0%" in text  # 구간 → 12개월 후 수익
    assert "상승 2개 / 하락 0개" in text and "중앙값 +25.0%" in text
    assert "11-06(금) 고용보고서" in text  # 이번 주 이벤트
    assert "요약 문장입니다." in text  # LLM 요약
    assert "http://localhost:8501" in text
    assert text.splitlines()[-1] == "표본 3개. 결과는 갈릴 수 있으며 예측하지 않는다."


def test_telegram_text_shows_top3_only_and_omits_summary_when_absent():
    report = make_report(summary=None)
    res = make_result(
        ends=("2000-08-08", "2021-11-02", "2024-09-26", "2010-01-04"),
        combined=(0.95, 0.93, 0.92, 0.91),
    )
    report.sections[0] = pipeline.Section(120, res, report.sections[0].comparison)
    text = pipeline.render_telegram(report)
    assert "2010" not in text  # 4번째 국면은 요약에 넣지 않는다
    assert "요약 (AI)" not in text


def test_telegram_text_is_truncated_with_pointer_to_attachment():
    report = make_report(summary="가" * 5000)
    text = pipeline.render_telegram(report)
    assert len(text) <= 3500
    assert text.endswith("…(전체는 첨부 파일)")


def _capture_urlopen(monkeypatch):
    calls = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self):
            return b"{}"

    def fake(req, timeout=None):
        calls.append(req)
        return Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return calls


def test_send_telegram_posts_message_then_document(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TOK")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    calls = _capture_urlopen(monkeypatch)
    pipeline._send_telegram(make_report(), "# 전체 보고서\n| a | b |\n")

    assert [c.full_url for c in calls] == [
        "https://api.telegram.org/botTOK/sendMessage",
        "https://api.telegram.org/botTOK/sendDocument",
    ]
    msg = json.loads(calls[0].data)
    assert msg["chat_id"] == "42" and "parse_mode" not in msg  # 평문
    assert msg["text"] == pipeline.render_telegram(make_report())

    doc = calls[1]
    ctype = doc.get_header("Content-type")
    assert ctype.startswith("multipart/form-data; boundary=")
    raw = b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + doc.data
    parsed = message_from_bytes(raw)
    parts = {p.get_param("name", header="content-disposition"): p for p in parsed.get_payload()}
    assert parts["chat_id"].get_payload(decode=True) == b"42"
    document = parts["document"]
    assert document.get_filename() == "2026-11-04.md"
    assert document.get_payload(decode=True).decode("utf-8") == "# 전체 보고서\n| a | b |\n"


def test_send_telegram_not_called_without_token(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")  # 토큰이 없으면 꺼진 채널
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: pytest.fail("텔레그램 요청이 나갔다")
    )
    assert pipeline.deliver(make_report(), "md", "html") == ["file"]


def test_deliver_lists_telegram_when_both_requests_succeed(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TOK")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    calls = _capture_urlopen(monkeypatch)
    assert pipeline.deliver(make_report(), "md", "html") == ["file", "telegram"]
    assert len(calls) == 2


# --- LLM 요약


def test_summarize_without_key_returns_none_and_never_calls_api(monkeypatch):
    def forbidden(*_a, **_k):
        raise AssertionError("키가 없는데 API 클라이언트를 만들었다")

    monkeypatch.setattr(llm.openai, "OpenAI", forbidden)
    monkeypatch.setenv("REPORT_LLM", "on")  # 스위치만 켜져 있고 키가 없다
    assert llm.summarize(make_report()) is None


def test_summarize_key_without_switch_never_creates_client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")  # 스위치(REPORT_LLM)가 off
    monkeypatch.setattr(
        llm.openai, "OpenAI", lambda **kw: (_ for _ in ()).throw(AssertionError("호출 금지"))
    )
    assert llm.summarize(make_report()) is None


def _fake_openai(monkeypatch, seen, output_text=" 요약입니다. "):
    class FakeResponses:
        def create(self, **kw):
            seen.update(kw)
            return type("R", (), {"output_text": output_text})()

    class FakeClient:
        def __init__(self, api_key):
            seen["api_key"] = api_key
            self.responses = FakeResponses()

    monkeypatch.setattr(llm.openai, "OpenAI", FakeClient)


def test_summarize_uses_facts_only_prompt_and_default_model(monkeypatch):
    monkeypatch.setenv("REPORT_LLM", "on")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    seen = {}
    _fake_openai(monkeypatch, seen)
    assert llm.summarize(make_report(summary=None)) == "요약입니다."
    assert seen["api_key"] == "test-key"
    assert seen["model"] == "gpt-6.1-sol" and seen["max_output_tokens"] == 600
    assert "권고" in seen["instructions"] and "4~6문장" in seen["instructions"]
    assert "2000-08-08" in seen["input"]  # 보고서의 사실이 프롬프트에 들어간다
    assert "요약 (AI)" not in seen["input"]  # 요약 자신은 입력에서 제외


def test_summarize_model_can_be_overridden_by_env(monkeypatch):
    monkeypatch.setenv("REPORT_LLM", "on")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "other-model")
    seen = {}
    _fake_openai(monkeypatch, seen)
    llm.summarize(make_report(summary=None))
    assert seen["model"] == "other-model"


def test_summarize_api_failure_returns_none(monkeypatch):
    monkeypatch.setenv("REPORT_LLM", "on")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    class Broken:
        def __init__(self, api_key):
            raise RuntimeError("boom")

    monkeypatch.setattr(llm.openai, "OpenAI", Broken)
    assert llm.summarize(make_report(summary=None)) is None


def test_summarize_call_failure_returns_none(monkeypatch):
    monkeypatch.setenv("REPORT_LLM", "on")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    class FakeClient:
        def __init__(self, api_key):
            self.responses = self

        def create(self, **_kw):
            raise TimeoutError("slow")

    monkeypatch.setattr(llm.openai, "OpenAI", FakeClient)
    assert llm.summarize(make_report(summary=None)) is None


# --- 스케줄러·설정


def _fields(trigger):
    return {f.name: str(f) for f in trigger.fields}


def test_cron_default_is_0730_weekdays_kst():
    trig = scheduler.cron_trigger()
    f = _fields(trig)
    assert (f["hour"], f["minute"], f["day_of_week"]) == ("7", "30", "tue-sat")
    assert str(trig.timezone) == "Asia/Seoul"


def test_cron_follows_report_time_env(monkeypatch):
    monkeypatch.setenv("REPORT_TIME", "18:05")
    f = _fields(scheduler.cron_trigger())
    assert (f["hour"], f["minute"]) == ("18", "5")


@pytest.mark.parametrize("bad", ["7", "25:00", "07:60", "abc", "7:3"])
def test_cron_rejects_bad_time(bad):
    with pytest.raises(ValueError, match="HH:MM"):
        scheduler.parse_report_time(bad)


def test_report_windows_env(monkeypatch):
    assert pipeline.report_windows() == (120, 250)
    monkeypatch.setenv("REPORT_WINDOWS", "60, 120,250")
    assert pipeline.report_windows() == (60, 120, 250)
    with pytest.raises(ValueError):
        pipeline.parse_windows("a,b")


def test_env_blank_value_counts_as_unset(monkeypatch):
    monkeypatch.setenv("REPORT_TIME", "")
    assert report_env("REPORT_TIME", "07:30") == "07:30"


# --- 데이터 갱신


def test_refresh_data_records_failure_and_continues(monkeypatch):
    calls = []

    def ok(name, rows):
        def f(*a, **k):
            calls.append((name, k))
            return rows

        return f

    def fail(*_a, **_k):
        raise RuntimeError("FRED_API_KEY가 설정되지 않았다")

    monkeypatch.setattr(pipeline._prices, "ingest_prices", ok("prices", 40))
    monkeypatch.setattr(pipeline._fred, "ingest_fred", fail)
    monkeypatch.setattr(pipeline._semi, "ingest_semi", ok("semi", 498))
    monkeypatch.setattr(pipeline._events, "ingest_events", ok("events", 1800))
    out = pipeline.refresh_data(today=dt.date(2026, 11, 5))
    assert dict(out) == {"prices": 40, "fred": -1, "semi": 498, "events": 1800}
    assert "FRED_API_KEY" in out.errors["fred"]
    assert calls[0][1]["start"] == "2026-10-06"  # 오늘 - 30일


# --- DB (docker compose up -d db)


@pytest.mark.db
def test_report_runs_round_trip_and_upsert(engine):
    from stocker.db import init_db, read_report_run, save_report_run

    init_db(engine)
    day = dt.date(1999, 1, 4)
    try:
        save_report_run(day, 120, {"a": 1}, "first", engine=engine)
        save_report_run(day, 120, {"a": 2}, "second", engine=engine)  # PK 충돌 없이 갱신
        row = read_report_run(day, 120, engine=engine)
        assert row["result"] == {"a": 2} and row["report_md"] == "second"
        assert read_report_run(dt.date(1999, 1, 5), 120, engine=engine) is None
        assert (
            read_report_run(before=dt.date(1999, 1, 5), window=120, engine=engine)["run_date"]
            == day
        )
        assert read_report_run(before=day, window=120, engine=engine) is None
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql("DELETE FROM report_runs WHERE run_date = '1999-01-04'")


@pytest.mark.db
def test_run_daily_twice_updates_without_conflict(engine):
    from stocker.db import init_db, read_report_run

    init_db(engine)
    as_of = "2018-12-20"  # 실제 최신 보고서를 건드리지 않도록 과거 기준일
    try:
        first = pipeline.run_daily(engine, as_of=as_of, windows=(60,), refresh=False, send=False)
        assert first.channels == []
        assert first.sections[0].comparison["has_previous"] is False
        row = read_report_run(first.as_of.date(), 0, engine=engine)
        assert row["report_md"].startswith("# SOX 국면 보고서")
        assert read_report_run(first.as_of.date(), 60, engine=engine)["result"]["window"] == 60

        second = pipeline.run_daily(engine, as_of=as_of, windows=(60,), refresh=False, send=False)
        cmp = second.sections[0].comparison
        assert cmp["has_previous"] and cmp["unchanged"] and cmp["same_day"]
        assert "변화 없음" in read_report_run(second.as_of.date(), 0, engine=engine)["report_md"]
    finally:
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM report_runs WHERE run_date = '2018-12-20'"))

"""LLM 요약 (선택). REPORT_LLM=on 이고 ANTHROPIC_API_KEY가 있을 때만 API를 부른다.

둘 중 하나라도 없으면 None (과금 없음이 기본). 키만으로는 켜지지 않는다:
개발 환경의 프로세스 변수에 키가 들어 있어 우발 호출이 난 적이 있다."""

from __future__ import annotations

import logging

import anthropic

from stocker.report import env

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 600

SYSTEM_PROMPT = (
    "너는 반도체 지수(SOX) 국면 유사도 보고서를 요약하는 도우미다. "
    "아래 규칙을 반드시 지켜라.\n"
    "1. 보고서에 적힌 사실만 요약한다. 보고서에 없는 숫자·사건·원인을 만들지 않는다.\n"
    "2. 매수·매도·비중 조절 같은 투자 권고, 전망, 예측을 하지 않는다. "
    "'오를 것이다/내릴 것이다'도 쓰지 않는다.\n"
    "3. 표본 수가 작다는 점과 결과가 갈렸다는 점(상승·하락 개수)을 숨기지 않는다.\n"
    "4. 한국어로 4~6문장, 한 단락으로 쓴다. 전문용어는 쉬운 말로 풀어 쓴다."
)


def summarize(report) -> str | None:
    """보고서(사실)를 한 단락으로 요약. 키가 없거나 호출이 실패하면 None."""
    if (env("REPORT_LLM", "off") or "off").lower() not in ("on", "1", "true"):
        return None
    key = env("ANTHROPIC_API_KEY")
    if not key:
        return None
    # 순환 import 회피: pipeline이 이 모듈을 import한다
    from stocker.report.pipeline import render_markdown

    facts = render_markdown(report, include_summary=False)
    try:
        client = anthropic.Anthropic(api_key=key)
        msg = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"다음 보고서를 요약하라.\n\n{facts}"}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    except Exception as exc:  # noqa: BLE001  (요약 실패가 보고서 전체를 막지 않게)
        logger.warning("LLM 요약 실패, 건너뜀: %s", exc.__class__.__name__)
        return None
    return text or None

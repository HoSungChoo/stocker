"""평가표 코멘트: 왜 비슷한지(와 어디가 다른지)를 한국어 4문장으로. 계약: PHASE6_CONTRACT.md 26절.

규칙 기반이다. LLM·네트워크·난수를 쓰지 않으므로 같은 입력은 항상 같은 문장을 낸다.
입력은 `Explanation`(그때 값 / 지금 값)과, 있으면 적합 판정(`FitCheck`, 23절).

문장 구조 (값이 없는 지표는 건너뛴다):
  1. 닮은 점   — 차이가 작은 지표를 원 값과 함께 (최대 3개)
  2. 다른 점   — 차이가 큰 지표 (최대 2개)
  3. 판정      — fit.status가 있으면 그 상태, 없으면 닮은/다른 지표 개수로 대체
  4. 결과      — 그 국면 뒤 SOX가 실제로 어땠는지 (사실만. 예측·권고 문구는 쓰지 않는다)

임계값(26절):
  낙폭 차이   ≤3%p "거의 같다" / ≤8%p "비슷하다" / 그 밖 "다르다"
  금리류 차이 ≤0.5%p "같은 수준" / ≤2%p "비슷" / 그 밖 "다르다"   (기준금리·10년물·장단기 스프레드)
  비율형      큰 쪽/작은 쪽 ≥2배면 "N배 차이" (유가·VIX·달러지수·반도체 매출 YoY)
계약에 값이 없어 이 파일이 정한 것: 낙폭 지속일 상대 차이 ≤40%면 비슷(23절 적합 기준과 같은 값),
비율형이 1.25배 이내면 비슷, 그 사이(1.25~2배)는 어느 쪽 문장에도 넣지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from stocker.similarity.explain import Explanation

# --- 임계값 (계약 26절) ---
DD_SAME_PP = 3.0
DD_SIMILAR_PP = 8.0
RATE_SAME_PP = 0.5
RATE_SIMILAR_PP = 2.0
RATIO_DIFF = 2.0
# --- 이 파일이 정한 값 ---
DAYS_SIMILAR_REL = 0.40
RATIO_SIMILAR = 1.25
MAX_SIMILAR = 3
MAX_DIFFERENT = 2

SAME, SIMILAR, DIFFERENT = "same", "similar", "different"

FORBIDDEN_PHRASES = (
    "오를", "오른다", "내릴", "내린다", "사야", "팔아", "매수", "매도", "추천", "예상", "전망",
)  # fmt: skip

STATUS_SENTENCE = {
    "strong": "위치·환경 모두 닮은 사례다.",
    "ok": "위치는 닮았지만 환경은 다른 사례다.",
    "weak": "닮았다고 보기 어려운 사례(참고용)다.",
}
# 적합 판정이 없을 때의 대체 문장: (위치가 닮았나, 환경이 닮았나)
FALLBACK_SENTENCE = {
    (True, True): STATUS_SENTENCE["strong"],
    (True, False): STATUS_SENTENCE["ok"],
    (False, True): "환경은 닮았지만 위치는 다른 사례다.",
    (False, False): STATUS_SENTENCE["weak"],
}
HORIZON_LABEL = {252: "12개월", 126: "6개월", 63: "3개월", 21: "1개월"}


@dataclass(frozen=True)
class Frag:
    """문장 조각. stem 뒤에 "고"/"다"를 붙인다. noun=True면 받침에 따라 "이"를 끼운다."""

    stem: str
    noun: bool = False

    def _joined(self, tail: str) -> str:
        if self.noun and _has_batchim(self.stem):
            return f"{self.stem}이{tail}"
        return f"{self.stem}{tail}"

    def cont(self) -> str:
        return self._joined("고")

    def end(self) -> str:
        return self._joined("다")


@dataclass
class Item:
    """지표 하나의 비교 결과. grade가 None이면 어느 문장에도 넣지 않는다."""

    key: str
    group: str  # "position" | "env"
    grade: str | None
    similar: Frag | None = None
    different: Frag | None = None


def _has_batchim(text: str) -> bool:
    ch = text[-1]
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28 != 0
    return ch in "0136780LMNR"  # 숫자·영문으로 끝나는 경우의 대략적인 읽기 (일·삼·육·칠·팔·영…)


def _topic(label: str) -> str:
    """주제 조사 은/는."""
    return label + ("은" if _has_batchim(label) else "는")


def _join(frags: list[Frag]) -> str:
    """조각들을 "A고, B고, C다"로 잇는다."""
    parts = [f.cont() for f in frags[:-1]] + [frags[-1].end()]
    return ", ".join(parts)


def _pct(v: float, digits: int = 1) -> str:
    return f"{v * 100:.{digits}f}%"


def _signed_pct(v: float, digits: int = 1) -> str:
    return f"{v * 100:+.{digits}f}%"


def _num(v: float) -> str:
    """수준값 표기: 10 이상은 정수, 그 밖은 소수 1자리."""
    return f"{v:.0f}" if abs(v) >= 10 else f"{v:.1f}"


def _ratio(a: float, b: float) -> float | None:
    """큰 쪽 / 작은 쪽. 부호가 다르거나 0이면 None."""
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    return max(a, b) / min(a, b)


def _ratio_text(r: float) -> str:
    return f"{r:.0f}배" if r >= 10 else f"{r:.1f}배"


# ----------------------------------------------------------------------------- 지표별 비교


def _drawdown_item(exp: Explanation) -> Item | None:
    t, n = exp.price.drawdown.then, exp.price.drawdown.now
    if t is None or n is None:
        return None
    diff = round(abs(t - n) * 100, 6)  # 부동소수 오차(3.0000000000000027)로 경계가 뒤집히지 않게
    pair = f"{_pct(t)} 대 {_pct(n)}"
    if diff <= DD_SAME_PP:
        return Item("drawdown", "position", SAME, similar=Frag(f"낙폭이 {pair}로 거의 같"))
    if diff <= DD_SIMILAR_PP:
        return Item("drawdown", "position", SIMILAR, similar=Frag(f"낙폭이 {pair}로 비슷하"))
    return Item("drawdown", "position", DIFFERENT, different=Frag(f"낙폭이 {pair}로 다르"))


def _days_item(exp: Explanation) -> Item | None:
    t, n = exp.price.drawdown_days.then, exp.price.drawdown_days.now
    if t is None or n is None or max(t, n) <= 0:
        return None
    rel = round(abs(t - n) / max(t, n), 6)
    pair = f"{t}일 대 {n}일"
    if rel <= DAYS_SIMILAR_REL:
        return Item("days", "position", SIMILAR, similar=Frag(f"고점 이후 {pair}로 기간이 비슷하"))
    return Item("days", "position", DIFFERENT, different=Frag(f"고점 이후 {pair}로 기간이 다르"))


def _rate_item(m, key: str) -> Item | None:
    """금리류(%p 단위 차이). m은 MacroExplanation."""
    if m.then is None or m.now is None:
        return None
    diff = round(abs(m.then - m.now), 6)
    pair = f"{m.then:.1f}% 대 {m.now:.1f}%"
    if m.unit == "%p":  # 스프레드는 '%'를 붙이지 않는다
        pair = f"{m.then:+.2f}%p 대 {m.now:+.2f}%p"
    if diff <= RATE_SAME_PP:
        return Item(key, "env", SAME, similar=Frag(f"{m.label} {pair}로 같은 수준", True))
    if diff <= RATE_SIMILAR_PP:
        return Item(key, "env", SIMILAR, similar=Frag(f"{m.label} {pair}로 비슷하"))
    # 다른 점: 어느 쪽이 높았는지 (기준금리는 계약 예시처럼 '그때가 N%p 높았')
    higher = "높았" if m.then > m.now else "낮았"
    text = f"{_topic(m.label)} 그때가 {diff:.1f}%p {higher}"
    return Item(key, "env", DIFFERENT, different=Frag(text))


def _level_ratio_item(m, key: str) -> Item | None:
    """유가·VIX·달러지수: 큰 쪽/작은 쪽 비율."""
    r = _ratio(m.then, m.now)
    if r is None:
        return None
    r = round(r, 6)
    unit = "달러" if m.unit == "$" else ""
    pair = f"{_num(m.then)}{unit} 대 {_num(m.now)}{unit}"
    if r >= RATIO_DIFF:
        text = f"{_topic(m.label)} {pair}로 {_ratio_text(r)} 벌어졌"
        return Item(key, "env", DIFFERENT, different=Frag(text))
    if r <= RATIO_SIMILAR:
        return Item(key, "env", SIMILAR, similar=Frag(f"{_topic(m.label)} {pair}로 비슷하"))
    return Item(key, "env", None)


def _wsts_item(exp: Explanation) -> Item | None:
    row = next((c for c in exp.cycle if c.metric == "wsts_sales_yoy"), None)
    if row is None or row.then is None or row.now is None:
        return None
    t, n = row.then, row.now
    if t * n < 0:  # 부호가 다르면 증가기/감소기가 엇갈린다
        then_word = "증가기" if t > 0 else "감소기"
        now_word = "증가기" if n > 0 else "감소기"
        return Item(
            "wsts",
            "env",
            DIFFERENT,
            different=Frag(f"반도체 매출은 그때 {then_word}, 지금 {now_word}로 방향이 엇갈리"),
        )
    word = "증가기" if t > 0 else "감소기"
    sign_frag = Frag(f"둘 다 반도체 매출 {word}", True)
    r = _ratio(abs(t), abs(n))
    if r is not None and r >= RATIO_DIFF:
        pair = f"{_signed_pct(t, 0)} 대 {_signed_pct(n, 0)}"
        noun = "증가율" if t > 0 else "감소율"
        text = f"매출 {noun}은 {pair}로 {_ratio_text(r)} 벌어졌"
        return Item("wsts", "env", DIFFERENT, similar=sign_frag, different=Frag(text))
    return Item("wsts", "env", SIMILAR, similar=sign_frag)


def build_items(exp: Explanation) -> list[Item]:
    """우선순위 순서로 지표별 비교 결과를 만든다. 값이 없는 지표는 빠진다."""
    macro = {m.series: m for m in exp.macro}
    items: list[Item | None] = [_drawdown_item(exp), _days_item(exp)]
    if "DFF" in macro:
        items.append(_rate_item(macro["DFF"], "DFF"))
    items.append(_wsts_item(exp))
    for sid in ("DGS10", "T10Y2Y"):
        if sid in macro:
            items.append(_rate_item(macro[sid], sid))
    for sid in ("DCOILWTICO", "VIXCLS", "DTWEXBGS"):
        if sid in macro:
            items.append(_level_ratio_item(macro[sid], sid))
    return [i for i in items if i is not None]


# ----------------------------------------------------------------------------- 문장


def _similar_sentence(items: list[Item]) -> str:
    frags = [i.similar for i in items if i.similar is not None]  # 우선순위 순서(위치 → 환경) 그대로
    frags = frags[:MAX_SIMILAR]
    if not frags:
        return "뚜렷하게 닮은 지표는 찾기 어렵다."
    return _join(frags) + "."


def _different_sentence(items: list[Item]) -> str:
    frags = [i.different for i in items if i.different is not None][:MAX_DIFFERENT]
    if not frags:
        return "눈에 띄게 다른 지표는 없다."
    return "다만 " + _join(frags) + "."  # type: ignore[arg-type]


def _status_of(fit: Any) -> str | None:
    if fit is None:
        return None
    status = fit.get("status") if isinstance(fit, dict) else getattr(fit, "status", None)
    return status if status in STATUS_SENTENCE else None


def _verdict_sentence(items: list[Item], fit: Any) -> str:
    status = _status_of(fit)
    if status is not None:
        return "이 국면은 " + STATUS_SENTENCE[status]
    # 적합 판정이 없으면 개수로 대체: 낙폭 깊이가 닮았나, 환경 지표 중 다른 것이 절반 미만인가
    depth = next((i for i in items if i.key == "drawdown"), None)
    pos_ok = depth is not None and depth.grade in (SAME, SIMILAR)
    env = [i for i in items if i.group == "env" and i.grade is not None]
    n_diff = sum(1 for i in env if i.grade == DIFFERENT)
    env_ok = bool(env) and n_diff * 2 < len(env)
    return "이 국면은 " + FALLBACK_SENTENCE[(pos_ok, env_ok)]


def _result_sentence(exp: Explanation) -> str | None:
    for h in (252, 126, 63, 21):
        v = exp.forward.get(h)
        if v is not None:
            return f"이 국면 뒤 {HORIZON_LABEL[h]} SOX는 {_signed_pct(v)}였다."
    return None


def build_comment(explanation: Explanation, fit: Any = None) -> str:
    """평가표 코멘트(2~4문장). fit은 FitCheck(또는 같은 모양의 dict)이고 없으면 None."""
    items = build_items(explanation)
    sentences = [
        _similar_sentence(items),
        _different_sentence(items),
        _verdict_sentence(items, fit),
    ]
    tail = _result_sentence(explanation)
    if tail:
        sentences.append(tail)
    return " ".join(sentences)

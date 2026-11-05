import type { Signal, SignalGroup } from '../api'

/** 체크리스트 그룹 순서·소제목 (계약 PHASE6C 30.3절) */
export const GROUP_ORDER: SignalGroup[] = ['bottom', 'bubble', 'heat', 'crisis']
export const GROUP_LABEL: Record<SignalGroup, string> = {
  bottom: '바닥 신호',
  bubble: '버블 끝 신호',
  heat: '과열 신호',
  crisis: '위기 신호',
}

export const CHECKLIST_NOTE =
  '체크리스트는 점수에 쓰이지 않는다. 1990년대 사례에서 쓰인 신호를 규칙으로 옮긴 것이다.'

const OP_SYMBOL: Record<Signal['op'], string> = { '>=': '≥', '<=': '≤', '<': '<' }

function withUnit(v: number, unit: string): string {
  const n = Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2).replace(/\.?0+$/, '')
  return unit === '배' ? `${n}배` : `${n}${unit}`
}

/** "≤ -2.7%p" 같은 켜짐 조건 */
export function conditionText(s: Signal): string {
  return `${OP_SYMBOL[s.op]} ${withUnit(s.threshold, s.unit)}`
}

/** 호버 문구: 값과 임계값 */
export function signalTitle(s: Signal): string {
  if (s.on == null || s.value == null) return `${s.label}: 데이터 없음 (켜짐 조건 ${conditionText(s)})`
  return `${s.label}: 값 ${withUnit(s.value, s.unit)} · 켜짐 조건 ${conditionText(s)} → ${s.on ? '켜짐' : '꺼짐'}`
}

export function signalState(s: Signal): 'on' | 'off' | 'na' {
  return s.on == null ? 'na' : s.on ? 'on' : 'off'
}

export const STATE_SYMBOL = { on: '●', off: '○', na: '–' } as const
export const STATE_NAME = { on: '켜짐', off: '꺼짐', na: '데이터 없음' } as const

/** 켜진 신호만 */
export function onSignals(list: Signal[]): Signal[] {
  return list.filter((s) => s.on === true)
}

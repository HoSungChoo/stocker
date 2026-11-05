export interface Weights {
  price: number
  macro: number
  cycle: number
}

export const DEFAULT_WEIGHTS: Weights = { price: 50, macro: 30, cycle: 20 }

/**
 * 슬라이더 하나를 value(0~100)로 옮기면 나머지 둘을 기존 비율대로 줄이거나 늘려 합을 100으로 맞춘다.
 * 나머지가 둘 다 0이면 반반 나눈다. 모두 정수.
 */
export function rebalance(w: Weights, key: keyof Weights, value: number): Weights {
  const v = Math.min(100, Math.max(0, Math.round(value)))
  const others = (['price', 'macro', 'cycle'] as const).filter((k) => k !== key)
  const [a, b] = others
  const rest = 100 - v
  const sum = w[a] + w[b]
  let na: number
  if (sum === 0) na = Math.round(rest / 2)
  else na = Math.round((rest * w[a]) / sum)
  const nb = rest - na
  return { ...w, [key]: v, [a]: na, [b]: nb } as Weights
}

/** 선형 보간 날짜. 점의 정확한 날짜는 API가 주지 않아 창 시작~끝 사이를 거래일 수로 비례 배분한다. */
export function approxDate(start: string, end: string, d: number, windowLen: number): string {
  const t0 = Date.parse(`${start}T00:00:00Z`)
  const t1 = Date.parse(`${end}T00:00:00Z`)
  if (Number.isNaN(t0) || Number.isNaN(t1) || windowLen <= 1) return start
  const t = t0 + ((t1 - t0) * d) / (windowLen - 1)
  return new Date(t).toISOString().slice(0, 10)
}

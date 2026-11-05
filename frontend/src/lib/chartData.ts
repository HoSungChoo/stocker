import type { OverlayResponse, SimilarityResponse } from '../api'

export interface SeriesSpec {
  key: string
  rank: number | null // null = 현재
  after: boolean
}

/** Recharts 한 행: d 와 시리즈별 값 */
export type Row = { d: number } & Record<string, number | undefined>

export function currentKey() {
  return 'cur'
}
export function winKey(rank: number) {
  return `m${rank}`
}
export function afterKey(rank: number) {
  return `m${rank}a`
}

/**
 * overlay 응답을 d 기준으로 병합한다.
 * 과거 창은 실선(창 안)과 점선(창 끝 이후) 두 시리즈로 나누고, 점선은 끊겨 보이지 않게
 * 창의 마지막 점(d = window-1)에서 시작한다.
 */
export function buildRows(overlay: OverlayResponse, hidden: ReadonlySet<number> = new Set()): Row[] {
  const byD = new Map<number, Row>()
  const row = (d: number): Row => {
    let r = byD.get(d)
    if (!r) {
      r = { d }
      byD.set(d, r)
    }
    return r
  }
  for (const p of overlay.current.points) row(p.d)[currentKey()] = p.v
  for (const m of overlay.matches) {
    if (hidden.has(m.rank)) continue
    for (const p of m.window_points) row(p.d)[winKey(m.rank)] = p.v
    const last = m.window_points[m.window_points.length - 1]
    if (last) row(last.d)[afterKey(m.rank)] = last.v
    for (const p of m.after_points) row(p.d)[afterKey(m.rank)] = p.v
  }
  return [...byD.values()].sort((a, b) => a.d - b.d)
}

export function seriesFor(overlay: OverlayResponse, hidden: ReadonlySet<number> = new Set()): SeriesSpec[] {
  const out: SeriesSpec[] = [{ key: currentKey(), rank: null, after: false }]
  for (const m of overlay.matches) {
    if (hidden.has(m.rank)) continue
    out.push({ key: winKey(m.rank), rank: m.rank, after: false })
    out.push({ key: afterKey(m.rank), rank: m.rank, after: true })
  }
  return out
}

export function combinedOf(sim: SimilarityResponse | undefined, rank: number): number | undefined {
  return sim?.matches.find((m) => m.rank === rank)?.combined
}

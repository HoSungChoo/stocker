import { describe, expect, it } from 'vitest'
import { readNote } from '../lib/readNote'
import { rebalance } from '../lib/weights'
import { addDays, dPlus, mmdd, weekday } from '../lib/dates'
import { fmtMacro, pct } from '../lib/format'
import { markerShape, matchesGroup } from '../lib/kinds'
import { buildRows, seriesFor } from '../lib/chartData'
import overlay from '../mocks/overlay.json'
import type { OverlayResponse } from '../api'

describe('readNote (읽는 법 규칙)', () => {
  it('then 이 null 이면 데이터 없음', () => {
    expect(readNote(null, 121.7)).toBe('데이터 없음')
    expect(readNote(undefined, 1)).toBe('데이터 없음')
    expect(readNote(3, null)).toBe('데이터 없음')
  })
  it('부호가 다르면 방향이 다름', () => {
    expect(readNote(-0.23, 0.32)).toBe('방향이 다름')
    expect(readNote(0.5, -0.1)).toBe('방향이 다름')
  })
  it('151일 vs 135일은 상대 차이 10.6% 라서 "비슷"이 아니다 (목업의 "비슷"과 다른 점)', () => {
    expect(readNote(151, 135)).toBe('그때가 높음')
  })
  it('10% 경계: 이내면 비슷, 넘으면 높음/낮음', () => {
    expect(readNote(100, 90)).toBe('비슷') // 차이 10/100 = 10% → 이내
    expect(readNote(100, 89)).toBe('그때가 높음') // 11%
    expect(readNote(89, 100)).toBe('그때가 낮음')
  })
  it('높음/낮음', () => {
    expect(readNote(6.44, 3.88)).toBe('그때가 높음')
    expect(readNote(0.33, 3.88)).toBe('그때가 낮음')
    expect(readNote(29, 100)).toBe('그때가 낮음')
  })
  it('둘 다 0 이거나 같으면 비슷', () => {
    expect(readNote(0, 0)).toBe('비슷')
    expect(readNote(100, 100)).toBe('비슷')
    expect(readNote(-0.2, -0.2)).toBe('비슷')
  })
  it('0 과 양수는 방향이 다른 것이 아니라 크기 차이로 본다', () => {
    expect(readNote(0, 0.3)).toBe('그때가 낮음')
  })
})

describe('rebalance', () => {
  it('합이 항상 100', () => {
    const w = rebalance({ price: 50, macro: 30, cycle: 20 }, 'price', 80)
    expect(w.price + w.macro + w.cycle).toBe(100)
    expect(w.price).toBe(80)
    expect(w.macro).toBe(12) // 20 * 30/50
    expect(w.cycle).toBe(8)
  })
  it('나머지가 0이면 반반', () => {
    const w = rebalance({ price: 100, macro: 0, cycle: 0 }, 'price', 40)
    expect(w).toEqual({ price: 40, macro: 30, cycle: 30 })
  })
  it('범위를 벗어나면 0~100 으로 자른다', () => {
    expect(rebalance({ price: 50, macro: 30, cycle: 20 }, 'macro', 150).macro).toBe(100)
  })
})

describe('날짜·형식', () => {
  it('D+n', () => {
    expect(dPlus('2026-11-06', '2026-11-05')).toBe('D+1')
    expect(dPlus('2026-11-05', '2026-11-05')).toBe('D-day')
    expect(addDays('2026-11-05', 14)).toBe('2026-11-19')
    expect(weekday('2026-11-06')).toBe('금')
    expect(mmdd('2026-11-06')).toBe('11/06')
  })
  it('pct / fmtMacro', () => {
    expect(pct(0.25)).toBe('+25.0%')
    expect(pct(-0.287)).toBe('-28.7%')
    expect(pct(null)).toBe('—')
    expect(fmtMacro(6.44, '%')).toBe('6.44%')
    expect(fmtMacro(0.32, '%p')).toBe('+0.32')
    expect(fmtMacro(29.4, '$')).toBe('$29')
    expect(fmtMacro(18.8, '')).toBe('18.8')
  })
  it('이벤트 종류 그룹·마커 모양', () => {
    expect(matchesGroup('cpi', 'data')).toBe(true)
    expect(matchesGroup('jobs', 'data')).toBe(true)
    expect(matchesGroup('war', 'shock')).toBe(true)
    expect(matchesGroup('fomc', 'shock')).toBe(false)
    expect(matchesGroup('unknown', 'all')).toBe(true)
    expect(markerShape('fomc')).toBe('circle')
    expect(markerShape('cpi')).toBe('triangle')
    expect(markerShape('earnings')).toBe('diamond')
    expect(markerShape('election')).toBe('square')
  })
})

describe('chartData', () => {
  const ov = overlay as unknown as OverlayResponse
  it('시리즈: 현재 1 + 순위당 2 (창 안 / 이후)', () => {
    expect(seriesFor(ov)).toHaveLength(1 + 5 * 2)
    expect(seriesFor(ov, new Set([1, 2]))).toHaveLength(1 + 3 * 2)
  })
  it('행은 d 오름차순, 총 372개(0~371)', () => {
    const rows = buildRows(ov)
    expect(rows).toHaveLength(372)
    expect(rows[0].d).toBe(0)
    expect(rows[rows.length - 1].d).toBe(371)
    // 점선은 창 마지막 점에서 이어진다
    expect(rows[119].m1a).toBe(rows[119].m1)
  })
})

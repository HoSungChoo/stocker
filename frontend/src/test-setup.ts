import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => cleanup())

// Recharts ResponsiveContainer 가 jsdom 에서 크기를 얻도록 ResizeObserver 와 크기를 흉내 낸다.
class RO {
  observe() {}
  unobserve() {}
  disconnect() {}
}
;(globalThis as unknown as { ResizeObserver: typeof RO }).ResizeObserver = RO

// jsdom 은 레이아웃이 없어 모든 요소 크기가 0이다. 차트가 그려지도록 고정 크기를 돌려준다.
for (const [prop, value] of [
  ['offsetWidth', 900],
  ['offsetHeight', 360],
  ['clientWidth', 900],
  ['clientHeight', 360],
] as const) {
  Object.defineProperty(HTMLElement.prototype, prop, { configurable: true, get: () => value })
}
HTMLElement.prototype.getBoundingClientRect = () =>
  ({ width: 900, height: 360, top: 0, left: 0, right: 900, bottom: 360, x: 0, y: 0, toJSON() {} }) as DOMRect

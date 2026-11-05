import { useState } from 'react'
import type { OverlayResponse, SimilarityResponse } from '../api'
import { rankColor } from '../lib/palette'
import OverlayChart from './OverlayChart'

export default function OverlayCard({
  overlay,
  sim,
  window,
}: {
  overlay: OverlayResponse
  sim?: SimilarityResponse
  window: number
}) {
  const [hidden, setHidden] = useState<ReadonlySet<number>>(new Set())
  const toggle = (rank: number) =>
    setHidden((prev) => {
      const n = new Set(prev)
      if (n.has(rank)) n.delete(rank)
      else n.add(rank)
      return n
    })
  return (
    <section className="card p-4 sm:p-6">
      <div className="flex flex-wrap items-baseline gap-3 mb-2">
        <h2 className="m-0 text-lg">지금 국면 vs 닮은 과거 국면</h2>
        <span className="muted text-[13px]">창 시작 = 100 · 실선은 창 안, 점선은 창이 끝난 뒤 1년</span>
      </div>
      <figure className="m-0 flex flex-col gap-3">
        <OverlayChart overlay={overlay} sim={sim} window={window} hidden={hidden} />
        <figcaption className="flex flex-wrap gap-x-[18px] gap-y-2.5 text-[13px] items-center">
          <span>
            <i className="lg inline-block w-3.5 h-1.5 rounded align-middle mr-1.5" style={{ background: '#5B8DEF' }} />
            <strong>현재</strong> {overlay.current.start} ~ {overlay.current.end}
          </span>
          {overlay.matches.map((m) => (
            <label key={m.rank} className="inline-flex items-center gap-1.5 min-h-[28px]">
              <input type="checkbox" checked={!hidden.has(m.rank)} onChange={() => toggle(m.rank)} />
              <i className="inline-block w-3.5 h-1 rounded" style={{ background: rankColor(m.rank) }} />
              {m.rank}위 {m.start} ~ {m.end}
            </label>
          ))}
          <span className="muted sm:ml-auto">마커: ● FOMC &nbsp;▲ CPI·고용 &nbsp;◆ 실적 &nbsp;■ 정책·전쟁·선거</span>
        </figcaption>
      </figure>
    </section>
  )
}

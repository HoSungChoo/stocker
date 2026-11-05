import { useState } from 'react'
import type { OverlayResponse, SimilarityResponse } from '../api'
import { GROUP_PILLS, MARKER_GROUPS, type KindGroup } from '../lib/kinds'
import { rankColor } from '../lib/palette'
import OverlayChart from './OverlayChart'

const MARKER_SYMBOL: Record<KindGroup, string> = { fomc: '●', data: '▲', earnings: '◆', shock: '■' }
const MARKER_LABEL = Object.fromEntries(GROUP_PILLS.map((p) => [p.key, p.label])) as Record<string, string>

export default function OverlayCard({
  overlay,
  sim,
  window,
  markers,
  onMarkersChange,
}: {
  overlay: OverlayResponse
  sim?: SimilarityResponse
  window: number
  /** 켜진 이벤트 그룹 (URL `markers=` 가 원천). 없으면 전부 켜짐 */
  markers?: ReadonlySet<KindGroup>
  onMarkersChange?: (next: ReadonlySet<KindGroup>) => void
}) {
  const [localMarkers, setLocalMarkers] = useState<ReadonlySet<KindGroup>>(new Set(MARKER_GROUPS))
  const shown = markers ?? localMarkers
  const toggleMarker = (g: KindGroup) => {
    const n = new Set(shown)
    if (n.has(g)) n.delete(g)
    else n.add(g)
    if (onMarkersChange) onMarkersChange(n)
    else setLocalMarkers(n)
  }
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
        <OverlayChart overlay={overlay} sim={sim} window={window} hidden={hidden} markers={shown} />
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
          <span className="inline-flex flex-wrap items-center gap-1.5 sm:ml-auto" role="group" aria-label="이벤트 마커">
            <span className="muted">마커</span>
            {MARKER_GROUPS.map((g) => (
              <button
                key={g}
                type="button"
                className="btn !min-h-[28px] !px-2.5 !py-0.5 !text-[12px]"
                aria-pressed={shown.has(g)}
                style={shown.has(g) ? undefined : { opacity: 0.45, textDecoration: 'line-through' }}
                onClick={() => toggleMarker(g)}
              >
                {MARKER_SYMBOL[g]} {MARKER_LABEL[g]}
              </button>
            ))}
          </span>
        </figcaption>
      </figure>
    </section>
  )
}

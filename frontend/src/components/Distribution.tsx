import type { ForwardSummary } from '../api'
import { HORIZONS, HORIZON_LABEL, pct, signClass } from '../lib/format'

export default function Distribution({ summary, n }: { summary: ForwardSummary[]; n: number }) {
  return (
    <section className="card px-4 sm:px-6 py-5">
      <div className="flex flex-wrap items-baseline gap-3 mb-3">
        <h2 className="m-0 text-lg">닮은 국면 {n}개가 끝난 뒤, SOX는</h2>
        <span className="muted text-[13px]">하위/상위 10%는 표본이 작아 극단값에 가깝다. 상승·하락 개수가 갈리는지 먼저 보라.</span>
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {HORIZONS.map((h) => {
          const s = summary.find((x) => x.horizon === h)
          return (
            <div key={h} className="border border-line rounded-[10px] px-4 py-3.5" data-testid="dist-cell">
              <div className="muted text-[13px]">{HORIZON_LABEL[h]} 후</div>
              <div className={`mono text-2xl ${signClass(s?.median)}`}>{s ? pct(s.median) : '—'}</div>
              <div className="muted mono text-xs">
                {s ? `${pct(s.p10)} ~ ${pct(s.p90)} · 상승 ${s.n_up} / 하락 ${s.n_down}` : '데이터 없음'}
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}

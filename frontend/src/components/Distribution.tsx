import type { ForwardSummary } from '../api'
import { HORIZONS, HORIZON_LABEL, pct, signClass } from '../lib/format'

export default function Distribution({
  summary,
  n,
  baseline = false,
}: {
  summary: ForwardSummary[]
  n: number
  /** true 면 닮은 국면이 아니라 전체 과거 기준선 분포다 (계약 PHASE6 23절) */
  baseline?: boolean
}) {
  return (
    <section className="card px-4 sm:px-6 py-5">
      <div className="flex flex-wrap items-baseline gap-3 mb-3">
        <h2 className="m-0 text-lg">{baseline ? '전체 과거 기준으로, SOX는' : `닮은 국면 ${n}개가 끝난 뒤, SOX는`}</h2>
        <span className="muted text-[13px]">
          {baseline
            ? '닮은 국면이 아니라 모든 과거 시점의 같은 기간 수익률 분포다.'
            : '하위/상위 10%는 표본이 작아 극단값에 가깝다. 상승·하락 개수가 갈리는지 먼저 보라.'}
        </span>
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {HORIZONS.map((h) => {
          const s = summary.find((x) => x.horizon === h)
          return (
            <div key={h} className="border border-line rounded-[10px] px-4 py-3.5" data-testid="dist-cell">
              <div className="muted text-[13px] flex items-center gap-2">
                {HORIZON_LABEL[h]} 후
                {baseline && (
                  <span className="text-[11px] rounded border border-edge px-1.5" data-testid="baseline-tag">
                    기준선
                  </span>
                )}
              </div>
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

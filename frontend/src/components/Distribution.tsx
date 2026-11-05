import type { ForwardSummary } from '../api'
import { HORIZONS, HORIZON_LABEL, pct, signClass } from '../lib/format'

function Row({
  testId,
  label,
  s,
  emptyText,
  tag,
}: {
  testId: string
  label: string
  s?: ForwardSummary
  emptyText: string
  tag?: boolean
}) {
  return (
    <div data-testid={testId}>
      <div className="muted text-[12px] flex items-center gap-2">
        {label}
        {tag && (
          <span className="text-[11px] rounded border border-edge px-1.5" data-testid="baseline-tag">
            기준선
          </span>
        )}
      </div>
      <div className={`mono text-2xl ${signClass(s?.median)}`}>{s ? pct(s.median) : '—'}</div>
      <div className="muted mono text-xs">
        {s ? `${pct(s.p10)} ~ ${pct(s.p90)} · 상승 ${s.n_up} / 하락 ${s.n_down}` : emptyText}
      </div>
    </div>
  )
}

export default function Distribution({
  summary,
  baseline,
  n,
  noMatches = false,
}: {
  /** 닮은 국면(적합한 것)으로 만든 분포 */
  summary: ForwardSummary[]
  /** 전체 과거 기준선 분포. 서버가 안 내면 기준선 줄을 숨긴다 */
  baseline?: ForwardSummary[] | null
  /** 분포에 실제 쓰인 닮은 국면 수 */
  n: number
  /** true 면 적합한 닮은 국면이 없어 윗줄이 비어 있다 (계약 PHASE6 23절) */
  noMatches?: boolean
}) {
  return (
    <section className="card px-4 sm:px-6 py-5">
      <div className="flex flex-wrap items-baseline gap-3 mb-3">
        <h2 className="m-0 text-lg">{`닮은 국면 ${n}개의 그 뒤 (사례)`}</h2>
        <span className="muted text-[13px]">
          하위/상위 10%는 표본이 작아 극단값에 가깝다. 상승·하락 개수가 갈리는지 먼저 보라.
        </span>
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {HORIZONS.map((h) => {
          const s = noMatches ? undefined : summary.find((x) => x.horizon === h)
          const b = baseline?.find((x) => x.horizon === h)
          return (
            <div key={h} className="border border-line rounded-[10px] px-4 py-3.5 flex flex-col gap-2" data-testid="dist-cell">
              <div className="muted text-[13px]">{HORIZON_LABEL[h]} 후</div>
              <Row
                testId="dist-row-similar"
                label={`닮은 국면 ${s?.n ?? 0}개`}
                s={s}
                emptyText={noMatches ? '적합한 국면 없음' : '데이터 없음'}
              />
              {baseline && (
                <div className="border-t border-line pt-2">
                  <Row
                    testId="dist-row-baseline"
                    label={`전체 과거 ${b?.n ?? 0}개`}
                    s={b}
                    emptyText="데이터 없음"
                    tag
                  />
                </div>
              )}
            </div>
          )
        })}
      </div>
      {baseline && (
        <p className="muted text-[13px] mt-3 mb-0" data-testid="baseline-note">
          백테스트(1999~2025)에서 닮은 5개의 분포는 기준선보다 12개월 결과를 더 잘 설명하지 못했다. 두 줄을 함께 보라.
        </p>
      )}
    </section>
  )
}

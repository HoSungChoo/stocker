import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import Controls, { type ControlState } from '../components/Controls'
import Distribution from '../components/Distribution'
import EvalCards from '../components/EvalCards'
import OverlayCard from '../components/OverlayCard'
import { ErrorBox, Honesty, LayersBanner, Loading, NotApplicableBanner } from '../components/Status'
import { useHealth, useOverlay, useSimilarity } from '../hooks'
import { useDebounced } from '../lib/useDebounced'
import { DEFAULT_WEIGHTS } from '../lib/weights'
import type { SimilarityQuery } from '../api'

export default function MainPage() {
  const [params, setParams] = useSearchParams()
  const [ctl, setCtl] = useState<ControlState>({
    asOf: params.get('as_of') ?? '',
    window: 120,
    topK: 5,
    weights: DEFAULT_WEIGHTS,
  })

  const onChange = (s: ControlState) => {
    setCtl(s)
    // 기준일만 URL에 반영해 링크로 공유할 수 있게 한다.
    const next = new URLSearchParams(params)
    if (s.asOf) next.set('as_of', s.asOf)
    else next.delete('as_of')
    setParams(next, { replace: true })
  }

  // 슬라이더를 끌 때마다 API를 부르지 않도록 값이 멈춘 뒤 0.3초 후에 요청한다.
  const settled = useDebounced(ctl, 300)
  const q: SimilarityQuery = useMemo(
    () => ({
      as_of: settled.asOf || undefined,
      window: settled.window,
      top_k: settled.topK,
      w_price: settled.weights.price / 100,
      w_macro: settled.weights.macro / 100,
      w_cycle: settled.weights.cycle / 100,
      mode: settled.mode,
    }),
    [settled],
  )

  const health = useHealth()
  const sim = useSimilarity(q)
  const overlay = useOverlay(q)

  const s12 = sim.data?.forward_summary.find((x) => x.horizon === 252)
  const n = sim.data?.matches.length
  const missing = sim.data?.layers_missing ?? health.data?.layers_missing ?? []
  const baseline = sim.data?.forward_summary_source === 'baseline'
  const asOfShown = sim.data?.as_of ?? (ctl.asOf || health.data?.last_trading_day || '—')

  return (
    <>
      {sim.data?.applicable === false && <NotApplicableBanner />}
      <LayersBanner missing={missing} />
      <Controls
        state={ctl}
        onChange={onChange}
        asOfShown={asOfShown}
        candidates={sim.data?.n_candidates}
        summary={s12}
        layersMissing={missing}
        n={n}
        serverMode={sim.data?.mode}
      />

      {sim.isError && <ErrorBox error={sim.error} what="유사 국면" />}
      {overlay.isError && <ErrorBox error={overlay.error} what="겹친 차트" />}
      {(sim.isLoading || overlay.isLoading) && <Loading what="유사 국면을 찾는 중…" />}

      {overlay.data && <OverlayCard overlay={overlay.data} sim={sim.data} window={settled.window} />}
      {sim.data && (
        <>
          <Distribution summary={sim.data.forward_summary} n={sim.data.matches.length} baseline={baseline} />
          <EvalCards matches={sim.data.matches} fit={sim.data.fit} q={q} />
          <Honesty>
            <strong>
              {baseline ? `표본 ${s12?.n ?? 0}개(전체 과거 기준선).` : `표본 ${sim.data.matches.length}개.`}
            </strong>
            <span>유사 국면의 결과는 갈릴 수 있으며, 이 도구는 예측하지 않는다.</span>
            {s12 && (
              <span className="muted">
                12개월 뒤 기준 상승 {s12.n_up} · 하락 {s12.n_down}
              </span>
            )}
            <span className="muted sm:ml-auto">
              데이터 마지막 날 {health.data?.last_trading_day ?? sim.data.as_of} · 매크로는 FRED, 매출은 WSTS
            </span>
          </Honesty>
        </>
      )}
    </>
  )
}

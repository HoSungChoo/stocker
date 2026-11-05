import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import ChecklistPanel from '../components/ChecklistPanel'
import Controls, { type ControlState } from '../components/Controls'
import Distribution from '../components/Distribution'
import EvalCards from '../components/EvalCards'
import OverlayCard from '../components/OverlayCard'
import { ErrorBox, Honesty, LayersBanner, Loading, NotApplicableBanner } from '../components/Status'
import { useChecklist, useHealth, useOverlay, useSimilarity } from '../hooks'
import { parseMarkers, serializeMarkers, type KindGroup } from '../lib/kinds'
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
  const checklist = useChecklist(q)

  // 이벤트 마커 토글은 URL 쿼리(markers=fomc,cpi)가 원천이라 새로고침해도 유지된다.
  const markers = useMemo(() => parseMarkers(params.get('markers')), [params])
  const onMarkersChange = (next: ReadonlySet<KindGroup>) => {
    const p = new URLSearchParams(params)
    const v = serializeMarkers(next)
    if (v == null) p.delete('markers')
    else p.set('markers', v)
    setParams(p, { replace: true })
  }

  const noMatches = sim.data?.forward_summary_source === 'baseline'
  // 기준선으로 대체된 경우 forward_summary 는 닮은 국면이 아니라 전체 과거 분포이므로 헤더·푸터의 상승/하락은 비운다.
  const s12 = noMatches ? undefined : sim.data?.forward_summary.find((x) => x.horizon === 252)
  const topK = sim.data?.matches.length
  // 분포에 실제 쓰인 국면 수. 적합 판정이 있으면 applicable_n(상위 K 중 적합한 수),
  // 판정 불가(null)거나 서버가 안 내면 상위 K 전체가 쓰인다.
  const hasFit = sim.data?.applicable != null && sim.data?.applicable_n != null
  const usedN = hasFit ? (sim.data?.applicable_n ?? 0) : topK
  const missing = sim.data?.layers_missing ?? health.data?.layers_missing ?? []
  let sampleLabel: string | undefined
  let sampleNote: string | undefined
  if (noMatches) {
    sampleLabel = '0'
    sampleNote = '(기준선 사용)'
  } else if (usedN != null) {
    sampleLabel = String(usedN)
    if (hasFit) sampleNote = `(상위 ${topK} 중)`
  }
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
        sampleLabel={sampleLabel}
        sampleNote={sampleNote}
        serverMode={sim.data?.mode}
      />

      {sim.isError && <ErrorBox error={sim.error} what="유사 국면" />}
      {overlay.isError && <ErrorBox error={overlay.error} what="겹친 차트" />}
      {(sim.isLoading || overlay.isLoading) && <Loading what="유사 국면을 찾는 중…" />}

      {overlay.data && (
        <OverlayCard
          overlay={overlay.data}
          sim={sim.data}
          window={settled.window}
          markers={markers}
          onMarkersChange={onMarkersChange}
        />
      )}
      {sim.data && (
        <>
          <Distribution
            summary={sim.data.forward_summary}
            baseline={sim.data.forward_summary_baseline}
            n={noMatches ? 0 : (usedN ?? 0)}
            noMatches={noMatches}
          />
          {checklist.isError && <ErrorBox error={checklist.error} what="체크리스트" />}
          {checklist.data && <ChecklistPanel data={checklist.data} />}
          <EvalCards matches={sim.data.matches} fit={sim.data.fit} q={q} checklist={checklist.data} />
          <Honesty>
            <strong>
              {noMatches ? '표본 0개(기준선 사용).' : `표본 ${usedN}개.`}
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

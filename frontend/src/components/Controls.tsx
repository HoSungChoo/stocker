import type { ForwardSummary, Mode } from '../api'
import { LAYER_LABEL } from '../lib/format'
import { rebalance, type Weights } from '../lib/weights'

export interface ControlState {
  asOf: string // '' = 최신
  window: number
  topK: number
  weights: Weights
  /** undefined = 서버 기본 */
  mode?: Mode
}

const MODES: { key: Mode; label: string }[] = [
  { key: 'shape', label: '모양 우선' },
  { key: 'regime', label: '환경 우선' },
]
const WINDOWS = [60, 120, 250]
const TOPKS = [3, 5, 7, 10]

interface Props {
  state: ControlState
  onChange: (s: ControlState) => void
  asOfShown: string
  candidates?: number
  summary?: ForwardSummary
  layersMissing: string[]
  n?: number
  /** 서버가 알려 준 현재 모드. null/undefined 면 서버가 mode 를 지원하지 않는 것이므로 토글을 숨긴다. */
  serverMode?: Mode | null
}

export default function Controls({ state, onChange, asOfShown, candidates, summary, layersMissing, n, serverMode }: Props) {
  const set = (patch: Partial<ControlState>) => onChange({ ...state, ...patch })
  const sliders: { key: keyof Weights; label: string }[] = [
    { key: 'price', label: '가격 형태' },
    { key: 'macro', label: '매크로' },
    { key: 'cycle', label: '산업 사이클' },
  ]
  const layers = ['price_shape', 'macro', 'semi_cycle'].map((k) => LAYER_LABEL[k])
  return (
    <section className="card px-4 sm:px-6 py-5 flex flex-wrap gap-6 items-start" aria-label="컨트롤">
      <div className="flex flex-col gap-1.5 min-w-[200px]">
        <label htmlFor="as-of" className="muted text-[13px]">
          기준일
        </label>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="mono text-[28px] font-bold" data-testid="as-of">
            {asOfShown}
          </span>
          <input
            id="as-of"
            type="date"
            value={state.asOf}
            onChange={(e) => set({ asOf: e.target.value })}
            className="bg-card border border-edge rounded-lg px-2 min-h-[44px] text-fg"
            aria-label="기준일 선택"
          />
          {state.asOf && (
            <button type="button" className="btn" onClick={() => set({ asOf: '' })}>
              최신으로
            </button>
          )}
        </div>
        <span className="muted text-[13px]">
          SOX 지수(^SOX) 최근 {state.window}거래일을 {candidates != null ? `${candidates.toLocaleString('en-US')}개 창` : '과거 창'}과 비교
        </span>
      </div>

      {serverMode && (
        <div className="flex flex-col gap-2">
          <span className="muted text-[13px]">비교 방식</span>
          <div className="flex gap-2" role="group" aria-label="비교 방식">
            {MODES.map((m) => (
              <button
                key={m.key}
                type="button"
                className={`pill ${(state.mode ?? serverMode) === m.key ? 'on' : ''}`}
                aria-pressed={(state.mode ?? serverMode) === m.key}
                onClick={() => set({ mode: m.key })}
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="flex flex-col gap-2">
        <span className="muted text-[13px]">창 길이(거래일)</span>
        <div className="flex gap-2" role="group" aria-label="창 길이">
          {WINDOWS.map((w) => (
            <button
              key={w}
              type="button"
              className={`pill ${state.window === w ? 'on' : ''}`}
              aria-pressed={state.window === w}
              onClick={() => set({ window: w })}
            >
              {w}
            </button>
          ))}
        </div>
        <label className="muted text-[13px] mt-1" htmlFor="top-k">
          표시할 닮은 국면 수
        </label>
        <select
          id="top-k"
          value={state.topK}
          onChange={(e) => set({ topK: Number(e.target.value) })}
          className="bg-card border border-edge rounded-lg px-2 min-h-[44px] text-fg"
        >
          {TOPKS.map((k) => (
            <option key={k} value={k}>
              상위 {k}개
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col gap-2 min-w-[260px] flex-[1_1_260px]">
        <span className="muted text-[13px]">레이어 가중치 (합 100%)</span>
        {sliders.map((s) => (
          <label key={s.key} className="grid grid-cols-[90px_1fr_44px] items-center gap-2.5 text-[13px]">
            {s.label}
            <input
              type="range"
              min={0}
              max={100}
              value={state.weights[s.key]}
              onChange={(e) => set({ weights: rebalance(state.weights, s.key, Number(e.target.value)) })}
              aria-label={`${s.label} 가중치`}
            />
            <span className="mono text-right">{state.weights[s.key]}%</span>
          </label>
        ))}
      </div>

      <div className="grid grid-cols-3 gap-4 min-w-[300px] flex-[1_1_300px]">
        <div>
          <div className="muted text-[13px]">표본 n</div>
          <div className="mono text-2xl font-bold" data-testid="sample-n">
            {n ?? '—'}
          </div>
        </div>
        <div>
          <div className="muted text-[13px]">12개월 후</div>
          <div className="text-2xl font-bold" data-testid="up-down">
            {summary ? (
              <>
                <span className="up">{summary.n_up}</span>
                <span className="muted text-base"> 상승 · </span>
                <span className="down">{summary.n_down}</span>
                <span className="muted text-base"> 하락</span>
              </>
            ) : (
              '—'
            )}
          </div>
        </div>
        <div>
          <div className="muted text-[13px]">레이어</div>
          <div className="text-[15px] font-semibold pt-1.5" data-testid="layer-status">
            {layersMissing.length === 0 ? `${layers.join(' · ')} 모두 켜짐` : `${layersMissing.length}개 꺼짐`}
          </div>
        </div>
      </div>
    </section>
  )
}

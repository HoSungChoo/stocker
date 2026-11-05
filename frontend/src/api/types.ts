// docs/PHASE5_CONTRACT.md 17절 스키마. 날짜는 YYYY-MM-DD 문자열, 수익률은 소수(0.25 = +25%), 점수는 0~1, 결측은 null.

export type LayerKey = 'price_shape' | 'macro' | 'semi_cycle'
export type Horizon = 21 | 63 | 126 | 252
export type WindowLen = 60 | 120 | 250

/** 공통 쿼리: 계약 17절 */
export interface SimilarityQuery {
  as_of?: string
  window?: number
  top_k?: number
  w_price?: number
  w_macro?: number
  w_cycle?: number
  cycle_lag?: number
}

export interface Health {
  ok: boolean
  last_trading_day: string
  layers_missing: string[]
}

export type Scores = Record<LayerKey, number>

export interface Match {
  rank: number
  start: string
  end: string
  scores: Scores
  combined: number
  /** 키는 "21" | "63" | "126" | "252" */
  forward: Record<string, number | null>
}

export interface ForwardSummary {
  horizon: number
  median: number | null
  p10: number | null
  p90: number | null
  n_up: number
  n_down: number
  n: number
}

export interface SimilarityResponse {
  as_of: string
  window: number
  top_k: number
  n_candidates: number
  layers_missing: string[]
  weights_used: Scores
  matches: Match[]
  forward_summary: ForwardSummary[]
}

export interface Point {
  d: number
  v: number
}

export interface OverlayEvent {
  d: number
  date: string
  kind: string
  title: string
}

export interface OverlayResponse {
  current: { start: string; end: string; points: Point[]; events: OverlayEvent[] }
  matches: {
    rank: number
    start: string
    end: string
    window_points: Point[]
    after_points: Point[]
    events: OverlayEvent[]
  }[]
}

export interface MacroRow {
  series: string
  label: string
  unit: string
  then: number | null
  now: number | null
  z_then: number | null
  z_now: number | null
}

export interface CycleRow {
  metric: string
  label: string
  then: number | null
  now: number | null
}

export interface ExplainResponse {
  match: { start: string; end: string; combined: number; scores: Scores }
  price: {
    dtw_rank: number
    euclid_rank: number
    drawdown: { then: number | null; now: number | null }
    drawdown_days: { then: number | null; now: number | null }
  }
  macro: MacroRow[]
  cycle: CycleRow[]
  forward: Record<string, number | null>
}

export interface UpcomingEvent {
  date: string
  kind: string
  title: string
  symbol: string | null
  scheduled: boolean
  confirmed: boolean
}

export interface ImpactEvent {
  id: number
  date: string
  kind: string
  title: string
  ret5: number | null
  ret_1m: number | null
  ret_3m: number | null
  source: string | null
}

export interface EventPath {
  event: { id: number; date: string; kind: string; title: string }
  points: Point[]
}

export interface PositionItem {
  symbol: string
  quantity: number
  avg_price: number | null
  last_price: number | null
  market_value: number | null
  pnl: number | null
  pnl_rate: number | null
  currency: string
}

export interface PositionsLatest {
  snapshot_date: string
  items: PositionItem[]
}

export interface ApplyRow {
  basis: 'sox' | 'lev3'
  horizon: number
  median: number | null
  p10: number | null
  p90: number | null
}

export interface PositionsApply {
  position_value: number
  currency: string
  n: number
  rows: ApplyRow[]
}

export interface ReportLatest {
  run_date: string
  markdown: string
}

/** 프론트가 쓰는 API 표면. 실제 구현(client.ts)과 mock(mock.ts)이 같은 모양을 가진다. */
export interface Api {
  health(): Promise<Health>
  similarity(q: SimilarityQuery): Promise<SimilarityResponse>
  overlay(q: SimilarityQuery): Promise<OverlayResponse>
  explain(end: string, q: SimilarityQuery): Promise<ExplainResponse>
  eventsUpcoming(days: number): Promise<UpcomingEvent[]>
  eventsImpact(opts: { limit: number; kinds?: string[] }): Promise<ImpactEvent[]>
  eventPath(id: number, days: number): Promise<EventPath>
  positionsLatest(): Promise<PositionsLatest | null>
  positionsApply(q: SimilarityQuery): Promise<PositionsApply>
  reportLatest(): Promise<ReportLatest>
}

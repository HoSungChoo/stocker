// VITE_USE_MOCK=true 일 때 쓰는 어댑터. 쿼리 값(window, 가중치 등)은 무시하고 고정 mock 을 돌려준다.
// mock 은 scripts/gen-mocks.mjs 가 만든다 (17절 스키마와 같은 모양).
import type {
  Api,
  ChecklistResponse,
  ExplainResponse,
  EventPath,
  Health,
  ImpactEvent,
  OverlayResponse,
  PositionsApply,
  PositionsLatest,
  ReportLatest,
  SimilarityResponse,
  UpcomingEvent,
} from './types'
import health from '../mocks/health.json'
import similarity from '../mocks/similarity.json'
import overlay from '../mocks/overlay.json'
import explain from '../mocks/explain.json'
import checklist from '../mocks/checklist.json'
import upcoming from '../mocks/events-upcoming.json'
import impact from '../mocks/events-impact.json'
import eventPath from '../mocks/event-path.json'
import positionsLatest from '../mocks/positions-latest.json'
import positionsApply from '../mocks/positions-apply.json'
import report from '../mocks/report.json'

const delay = <T>(v: T, ms: number): Promise<T> =>
  ms > 0 ? new Promise((r) => setTimeout(() => r(v), ms)) : Promise.resolve(v)

const toIso = (t: number) => new Date(t).toISOString().slice(0, 10)
const toMs = (d: string) => Date.parse(`${d}T00:00:00Z`)

/** mock 일정은 2026-11-05 기준이므로, "앞으로 14일"이 항상 보이도록 오늘 기준으로 평행이동한다. */
function shiftUpcoming(rows: UpcomingEvent[], todayIso: string): UpcomingEvent[] {
  const base = toMs('2026-11-05')
  const today = toMs(todayIso)
  return rows.map((r) => ({ ...r, date: toIso(today + (toMs(r.date) - base)) }))
}

export interface MockOptions {
  /** 응답 지연(ms). 테스트에서는 0. */
  latency?: number
  /** "오늘" 날짜 YYYY-MM-DD (기본 실제 오늘) */
  today?: string
}

export function createMockApi({ latency = 150, today }: MockOptions = {}): Api {
  const todayIso = today ?? toIso(Date.now())
  const explainMap = explain as unknown as Record<string, ExplainResponse>
  return {
    health: () => delay(health as Health, latency),
    similarity: () => delay(similarity as unknown as SimilarityResponse, latency),
    overlay: () => delay(overlay as unknown as OverlayResponse, latency),
    checklist: () => delay(checklist as unknown as ChecklistResponse, latency),
    explain: (end) => {
      const e = explainMap[end]
      return e ? delay(e, latency) : Promise.reject(new Error(`mock: explain ${end} 없음`))
    },
    eventsUpcoming: () => delay(shiftUpcoming(upcoming as UpcomingEvent[], todayIso), latency),
    eventsImpact: ({ limit }) => delay((impact as ImpactEvent[]).slice(0, limit), latency),
    eventPath: (id) => {
      const ev = (impact as ImpactEvent[]).find((e) => e.id === id)
      const base = eventPath as unknown as EventPath
      const out: EventPath = ev
        ? { ...base, event: { id: ev.id, date: ev.date, kind: ev.kind, title: ev.title } }
        : base
      return delay(out, latency)
    },
    positionsLatest: () => delay(positionsLatest as unknown as PositionsLatest | null, latency),
    positionsApply: (q) => {
      const byWindow = positionsApply as unknown as Record<string, PositionsApply>
      return delay(byWindow[String(q.window ?? 120)] ?? byWindow['120'], latency)
    },
    reportLatest: () => delay(report as ReportLatest, latency),
  }
}

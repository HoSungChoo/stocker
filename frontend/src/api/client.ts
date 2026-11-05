import type { Api, SimilarityQuery } from './types'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export const API_BASE: string = (import.meta.env.VITE_API_BASE ?? '/api').replace(/\/$/, '')

type Params = Record<string, string | number | undefined | null>

function qs(params: Params): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') sp.set(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

async function getJson<T>(path: string, params: Params = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}${qs(params)}`, { headers: { Accept: 'application/json' } })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body?.detail === 'string' ? body.detail : JSON.stringify(body)
    } catch {
      /* 본문이 JSON이 아니면 statusText 사용 */
    }
    throw new ApiError(res.status, `API ${res.status}: ${detail}`)
  }
  return (await res.json()) as T
}

export function commonParams(q: SimilarityQuery): Params {
  return { ...q }
}

export const realApi: Api = {
  health: () => getJson('/health'),
  similarity: (q) => getJson('/similarity', commonParams(q)),
  overlay: (q) => getJson('/overlay', commonParams(q)),
  explain: (end, q) => getJson('/explain', { ...commonParams(q), end }),
  eventsUpcoming: (days) => getJson('/events/upcoming', { days }),
  eventsImpact: ({ limit, kinds }) => getJson('/events/impact', { limit, kinds: kinds?.join(',') }),
  eventPath: (id, days) => getJson(`/events/${id}/path`, { days }),
  positionsLatest: () => getJson('/positions/latest'),
  positionsApply: (q) => getJson('/positions/apply', commonParams(q)),
  reportLatest: () => getJson('/report/latest'),
}

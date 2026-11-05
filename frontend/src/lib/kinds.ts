/** 이벤트 kind → 필터 그룹 / 태그 / 마커 모양 / 출처 */

export type KindGroup = 'fomc' | 'data' | 'earnings' | 'shock'

export const KIND_GROUP: Record<string, KindGroup> = {
  fomc: 'fomc',
  cpi: 'data',
  jobs: 'data',
  earnings: 'earnings',
  policy: 'shock',
  war: 'shock',
  election: 'shock',
  shock: 'shock',
}

export const GROUP_PILLS: { key: 'all' | KindGroup; label: string }[] = [
  { key: 'all', label: '전체' },
  { key: 'fomc', label: 'FOMC' },
  { key: 'data', label: 'CPI·고용' },
  { key: 'earnings', label: '실적' },
  { key: 'shock', label: '정책·전쟁·선거' },
]

export function matchesGroup(kind: string, group: 'all' | KindGroup): boolean {
  return group === 'all' || KIND_GROUP[kind] === group
}

/** 이벤트 kind 가 속한 마커 그룹. 모르는 kind 는 마커 모양과 같게 정책·전쟁·선거 쪽으로 본다. */
export function markerGroup(kind: string): KindGroup {
  return KIND_GROUP[kind] ?? 'shock'
}

/** 차트 마커 토글: GROUP_PILLS 의 그룹(`all` 제외)과 같은 그룹·라벨·모양을 쓴다. */
export const MARKER_GROUPS: KindGroup[] = ['fomc', 'data', 'earnings', 'shock']

/** URL 쿼리 `markers=` 에 쓰는 이름. 그룹 키와 다른 것만 따로 둔다 (`data`→`cpi`, `shock`→`policy`). */
const MARKER_TOKEN: Record<KindGroup, string> = { fomc: 'fomc', data: 'cpi', earnings: 'earnings', shock: 'policy' }
const TOKEN_GROUP = new Map<string, KindGroup>([
  ...MARKER_GROUPS.map((g) => [MARKER_TOKEN[g], g] as [string, KindGroup]),
  ...MARKER_GROUPS.map((g) => [g, g] as [string, KindGroup]), // 그룹 키 그대로도 받는다
])

/** `markers` 쿼리 값 → 켜진 그룹. 쿼리가 없으면(null) 전부 켜짐, 빈 문자열이면 전부 꺼짐. */
export function parseMarkers(value: string | null): ReadonlySet<KindGroup> {
  if (value == null) return new Set(MARKER_GROUPS)
  const on = value.split(',').flatMap((x) => TOKEN_GROUP.get(x.trim().toLowerCase()) ?? [])
  return new Set(on)
}

/** 켜진 그룹 → `markers` 쿼리 값. 전부 켜짐이면 null(쿼리에서 뺀다). */
export function serializeMarkers(on: ReadonlySet<KindGroup>): string | null {
  if (MARKER_GROUPS.every((g) => on.has(g))) return null
  return MARKER_GROUPS.filter((g) => on.has(g)).map((g) => MARKER_TOKEN[g]).join(',')
}

export const KIND_LABEL: Record<string, string> = {
  fomc: 'FOMC',
  cpi: 'CPI',
  jobs: '고용',
  earnings: '실적',
  policy: '정책',
  war: '전쟁',
  election: '선거',
  shock: '충격',
}

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind
}

export function tagClass(kind: string): string {
  switch (kind) {
    case 'fomc':
      return 't-fomc'
    case 'cpi':
    case 'jobs':
      return 't-cpi'
    case 'earnings':
      return 't-earn'
    case 'policy':
    case 'shock':
      return 't-pol'
    case 'war':
      return 't-war'
    case 'election':
      return 't-elec'
    default:
      return 't-other'
  }
}

export type MarkerShape = 'circle' | 'triangle' | 'diamond' | 'square'

/** 차트 마커: ● FOMC  ▲ CPI·고용  ◆ 실적  ■ 정책·전쟁·선거 */
export function markerShape(kind: string): MarkerShape {
  switch (markerGroup(kind)) {
    case 'fomc':
      return 'circle'
    case 'data':
      return 'triangle'
    case 'earnings':
      return 'diamond'
    default:
      return 'square'
  }
}

/** 일정 출처(목업 푸터 기준). API가 출처를 주지 않으므로 kind로 정한다. */
export function sourceOf(kind: string): string {
  switch (kind) {
    case 'fomc':
      return '연준'
    case 'cpi':
    case 'jobs':
      return '미 노동통계국(FRED)'
    case 'earnings':
      return '야후 파이낸스'
    default:
      return '수작업 목록'
  }
}

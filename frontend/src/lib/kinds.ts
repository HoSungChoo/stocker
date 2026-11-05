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
  switch (KIND_GROUP[kind]) {
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

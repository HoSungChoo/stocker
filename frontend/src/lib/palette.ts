export const COLORS = {
  bg: '#0F1115',
  card: '#171A21',
  line: '#262B35',
  grid: '#1F242D',
  axis: '#3A4150',
  fg: '#E8EAEE',
  sub: '#9AA3B2',
  current: '#5B8DEF',
  up: '#4CC274',
  down: '#F06A6A',
  eventPath: '#D3A8F0',
} as const

/** 1~5위 색. 6위 이상(top_k > 5)은 순환. */
export const RANK_COLORS = ['#F08A3E', '#3BBFAE', '#B07CD6', '#E35D5D', '#9AA3B2']

export function rankColor(rank: number): string {
  return RANK_COLORS[(Math.max(rank, 1) - 1) % RANK_COLORS.length]
}

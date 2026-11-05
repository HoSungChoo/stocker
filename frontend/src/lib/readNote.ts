/**
 * 평가표 "읽는 법" 열. 계약 19절 규칙:
 *  - then 이 null 이면 "데이터 없음"
 *  - 부호가 다르면 "방향이 다름"
 *  - 두 값의 상대 차이(|then-now| / 더 큰 절댓값)가 10% 이내면 "비슷"
 *  - 그 이상이면 then 이 더 큰지 작은지에 따라 "그때가 높음" / "그때가 낮음"
 */
export const SIMILAR_TOLERANCE = 0.1

export function readNote(then: number | null | undefined, now: number | null | undefined): string {
  if (then == null || now == null || Number.isNaN(then) || Number.isNaN(now)) return '데이터 없음'
  if (then * now < 0) return '방향이 다름'
  const scale = Math.max(Math.abs(then), Math.abs(now))
  if (scale === 0) return '비슷'
  const rel = Math.abs(then - now) / scale
  if (rel <= SIMILAR_TOLERANCE) return '비슷'
  return then > now ? '그때가 높음' : '그때가 낮음'
}

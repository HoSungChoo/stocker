const MS_DAY = 86_400_000
const toMs = (iso: string) => Date.parse(`${iso}T00:00:00Z`)

/** 로컬 달력 기준 오늘 YYYY-MM-DD */
export function localToday(now: Date = new Date()): string {
  const y = now.getFullYear()
  const m = String(now.getMonth() + 1).padStart(2, '0')
  const d = String(now.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

export function addDays(iso: string, n: number): string {
  return new Date(toMs(iso) + n * MS_DAY).toISOString().slice(0, 10)
}

/** 오늘 기준 D+n / D-n / D-day */
export function dPlus(iso: string, todayIso: string): string {
  const n = Math.round((toMs(iso) - toMs(todayIso)) / MS_DAY)
  if (n === 0) return 'D-day'
  return n > 0 ? `D+${n}` : `D${n}`
}

const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토']
export function weekday(iso: string): string {
  return WEEKDAYS[new Date(toMs(iso)).getUTCDay()]
}

/** "2026-11-06" → "11/06" */
export function mmdd(iso: string): string {
  return `${iso.slice(5, 7)}/${iso.slice(8, 10)}`
}

/** 소수 수익률 → "+25.0%" (부호 포함 기본) */
export function pct(x: number | null | undefined, digits = 1, signed = true): string {
  if (x == null || Number.isNaN(x)) return '—'
  const v = (x * 100).toFixed(digits)
  return signed && x > 0 ? `+${v}%` : `${v}%`
}

export function score(x: number | null | undefined): string {
  return x == null ? '—' : x.toFixed(2)
}

export function signClass(x: number | null | undefined): string {
  if (x == null || x === 0) return ''
  return x > 0 ? 'up' : 'down'
}

/** 거시 지표 값: 단위별 표기 (%, %p, $, 무단위) */
export function fmtMacro(v: number | null | undefined, unit: string): string {
  if (v == null) return '—'
  switch (unit) {
    case '%':
      return `${v.toFixed(2)}%`
    case '%p':
      return `${v > 0 ? '+' : ''}${v.toFixed(2)}`
    case '$':
      return `$${Math.round(v)}`
    default:
      return v.toFixed(1)
  }
}

export function fmtDays(v: number | null | undefined): string {
  return v == null ? '—' : `${v}일`
}

export function money(v: number | null | undefined, currency: string, digits = 0): string {
  if (v == null) return '—'
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency,
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(v)
  } catch {
    return `${v.toFixed(digits)} ${currency}`
  }
}

export function num(v: number | null | undefined, digits = 0): string {
  if (v == null) return '—'
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export const HORIZON_LABEL: Record<number, string> = { 21: '1개월', 63: '3개월', 126: '6개월', 252: '12개월' }
export const HORIZONS = [21, 63, 126, 252] as const

export const LAYER_LABEL: Record<string, string> = {
  price_shape: '가격 형태',
  macro: '매크로',
  semi_cycle: '산업 사이클',
}

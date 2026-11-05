import type { ReactNode } from 'react'
import { LAYER_LABEL } from '../lib/format'

export function Loading({ what = '불러오는 중…' }: { what?: string }) {
  return (
    <div className="card p-6 muted text-sm" role="status">
      {what}
    </div>
  )
}

export function ErrorBox({ error, what }: { error: unknown; what: string }) {
  const msg = error instanceof Error ? error.message : String(error)
  return (
    <div className="card p-5 text-sm border-down/60" role="alert">
      <div className="font-semibold text-down">{what}을(를) 불러오지 못했다</div>
      <div className="muted mt-1 break-words">{msg}</div>
      <div className="muted mt-1">
        API(<code>docker compose up -d db api</code>)가 켜져 있는지 확인하거나, <code>VITE_USE_MOCK=true</code>로 mock을 쓴다.
      </div>
    </div>
  )
}

export function LayersBanner({ missing }: { missing: string[] }) {
  if (missing.length === 0) return null
  return (
    <div
      role="alert"
      className="rounded-xl border border-[#6b4f1d] bg-[#2a2113] text-[#f0c070] px-5 py-3 text-sm"
    >
      <b>일부 레이어가 꺼져 있다:</b> {missing.map((m) => LAYER_LABEL[m] ?? m).join(' · ')}. 남은 레이어의 가중치만으로 계산한
      결과다.
    </div>
  )
}

export function Honesty({ children }: { children: ReactNode }) {
  return (
    <footer className="px-5 py-4 border border-dashed border-edge rounded-xl text-sm flex flex-wrap gap-x-6 gap-y-2 items-center">
      {children}
    </footer>
  )
}

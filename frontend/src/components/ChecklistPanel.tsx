import { Fragment } from 'react'
import type { ChecklistResponse, Signal } from '../api'
import {
  CHECKLIST_NOTE,
  GROUP_LABEL,
  GROUP_ORDER,
  STATE_NAME,
  STATE_SYMBOL,
  signalState,
  signalTitle,
} from '../lib/checklist'
import { rankColor } from '../lib/palette'

function Cell({ s }: { s?: Signal }) {
  if (!s) {
    return (
      <td className="text-center muted" data-testid="sig-cell" data-state="na">
        –
      </td>
    )
  }
  const st = signalState(s)
  return (
    <td
      className={`text-center text-[17px] ${st === 'on' ? 'text-cur' : 'text-sub'}`}
      title={signalTitle(s)}
      aria-label={`${s.label} ${STATE_NAME[st]}`}
      data-testid="sig-cell"
      data-state={st}
    >
      {STATE_SYMBOL[st]}
    </td>
  )
}

/** 체크리스트 패널: 행 = 신호, 열 = 지금 + 상위 K 국면. 점수와 무관하다 (계약 PHASE6C 30절). */
export default function ChecklistPanel({ data }: { data: ChecklistResponse }) {
  const byId = (list: Signal[], id: string) => list.find((s) => s.id === id)
  return (
    <section className="card px-4 sm:px-6 py-5" data-testid="checklist-panel">
      <div className="flex flex-wrap items-baseline gap-3 mb-1">
        <h2 className="m-0 text-lg">1990년대 사례 체크리스트</h2>
        <span className="muted text-[13px]">● 켜짐 · ○ 꺼짐 · – 데이터 없음. 칸에 마우스를 올리면 값과 임계값이 보인다.</span>
      </div>
      <p className="muted text-[13px] m-0 mb-3" data-testid="checklist-note">
        {CHECKLIST_NOTE}
      </p>
      <div className="overflow-x-auto">
        <table className="dt min-w-[480px]">
          <thead>
            <tr>
              <th>신호</th>
              <th className="!text-center">지금</th>
              {data.matches.map((m) => (
                <th key={m.rank} className="!text-center whitespace-nowrap">
                  <span style={{ color: rankColor(m.rank) }}>{m.rank}위</span>
                  <div className="mono text-[11px] font-normal">{m.end}</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {GROUP_ORDER.map((g) => {
              const rows = data.now.filter((s) => s.group === g)
              if (rows.length === 0) return null
              return (
                <Fragment key={g}>
                  <tr>
                    <td colSpan={2 + data.matches.length} className="!pt-4 text-[12px] font-semibold text-sub">
                      {GROUP_LABEL[g]}
                    </td>
                  </tr>
                  {rows.map((s) => (
                    <tr key={s.id} data-testid="sig-row" data-signal={s.id}>
                      <td>{s.label}</td>
                      <Cell s={s} />
                      {data.matches.map((m) => (
                        <Cell key={m.rank} s={byId(m.signals, s.id)} />
                      ))}
                    </tr>
                  ))}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}

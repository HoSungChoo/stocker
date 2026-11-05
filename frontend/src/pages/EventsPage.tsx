import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import EventPathChart from '../components/EventPathChart'
import { ErrorBox, Honesty, Loading } from '../components/Status'
import { useEventPath, useImpact, useUpcoming } from '../hooks'
import { addDays, dPlus, localToday, mmdd, weekday } from '../lib/dates'
import { pct, signClass } from '../lib/format'
import { GROUP_PILLS, kindLabel, matchesGroup, sourceOf, tagClass, type KindGroup } from '../lib/kinds'
import { COLORS } from '../lib/palette'

const PAGE = 20

export default function EventsPage() {
  const [group, setGroup] = useState<'all' | KindGroup>('all')
  const [limit, setLimit] = useState(PAGE)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const today = localToday()

  const upcoming = useUpcoming(14)
  const impact = useImpact(limit)

  const upRows = useMemo(() => (upcoming.data ?? []).filter((e) => matchesGroup(e.kind, group)), [upcoming.data, group])
  const impRows = useMemo(() => (impact.data ?? []).filter((e) => matchesGroup(e.kind, group)), [impact.data, group])
  const maxAbs = Math.max(0.0001, ...impRows.map((e) => Math.abs(e.ret5 ?? 0)))

  const selected = impRows.find((e) => e.id === selectedId) ?? impRows[0] ?? null
  const path = useEventPath(selected?.id ?? null, 30)
  const hasFomc = (upcoming.data ?? []).some((e) => e.kind === 'fomc')

  return (
    <>
      <section className="flex flex-wrap gap-3 items-center">
        <h1 className="m-0 text-[22px]">이벤트</h1>
        <span className="muted text-[13px]">일정은 사실만, 과거 사건은 "그때 SOX가 얼마나 움직였나"만 보여준다. 인과를 주장하지 않는다.</span>
        <div className="sm:ml-auto flex gap-2 flex-wrap" role="group" aria-label="종류 필터">
          {GROUP_PILLS.map((p) => (
            <button
              key={p.key}
              type="button"
              className={`pill ${group === p.key ? 'on' : ''}`}
              aria-pressed={group === p.key}
              onClick={() => setGroup(p.key)}
            >
              {p.label}
            </button>
          ))}
        </div>
      </section>

      <div className="flex flex-wrap gap-6 items-start">
        <section className="card flex-[1_1_420px] min-w-0 px-4 sm:px-6 py-5 flex flex-col gap-3" aria-label="앞으로 14일">
          <div className="flex flex-wrap items-baseline gap-3">
            <h2 className="m-0 text-lg">앞으로 14일</h2>
            <span className="muted text-[13px]">
              {today} ~ {addDays(today, 14).slice(5)} · 사전 공개된 일정만
            </span>
          </div>
          {upcoming.isLoading && <Loading />}
          {upcoming.isError && <ErrorBox error={upcoming.error} what="일정" />}
          {upcoming.data && (
            <ol className="list-none m-0 p-0 flex flex-col" data-testid="upcoming-list">
              {upRows.map((e) => (
                <li key={`${e.date}-${e.title}`} className="grid grid-cols-[72px_1fr] gap-3.5 py-3.5 border-b border-line2">
                  <div>
                    <div className="mono text-xl font-bold">{mmdd(e.date)}</div>
                    <div className="muted text-xs">
                      {weekday(e.date)} · {dPlus(e.date, today)}
                    </div>
                  </div>
                  <div className="flex flex-col gap-1">
                    <span className={`tag self-start ${tagClass(e.kind)}`}>{kindLabel(e.kind)}</span>
                    <div className="font-medium">{e.title}</div>
                    <div className="muted text-[13px]">
                      {sourceOf(e.kind)}
                      {e.symbol ? ` · ${e.symbol}` : ''}
                      {!e.confirmed ? ' · 날짜는 추정(미확정)' : ''}
                    </div>
                  </div>
                </li>
              ))}
              {upRows.length === 0 && <li className="muted text-sm py-3">이 종류의 14일 안 일정은 없다.</li>}
            </ol>
          )}
          {upcoming.data && !hasFomc && <div className="muted text-[13px] pt-1">14일 안에 FOMC는 없음.</div>}
        </section>

        <section className="card flex-[1.4_1_520px] min-w-0 px-4 sm:px-6 py-5 flex flex-col gap-3" aria-label="과거 이벤트">
          <div className="flex flex-wrap items-baseline gap-3">
            <h2 className="m-0 text-lg">SOX에 영향을 준 과거 이벤트</h2>
            <span className="muted text-[13px]">이벤트 전후 ±5거래일 SOX 수익률 · 많이 움직인 순 · 행을 누르면 아래에 상세</span>
          </div>
          {impact.isLoading && <Loading />}
          {impact.isError && <ErrorBox error={impact.error} what="영향 표" />}
          {impact.data && (
            <div className="overflow-x-auto">
              <table className="dt min-w-[520px]" data-testid="impact-table">
                <thead>
                  <tr>
                    <th>날짜</th>
                    <th>종류</th>
                    <th>사건</th>
                    <th className="!text-right">전후 ±5일</th>
                    <th className="w-[140px]">크기</th>
                  </tr>
                </thead>
                <tbody>
                  {impRows.map((e) => {
                    const on = selected?.id === e.id
                    const w = e.ret5 == null ? 0 : (Math.abs(e.ret5) / maxAbs) * 100
                    return (
                      <tr
                        key={e.id}
                        data-testid="impact-row"
                        aria-selected={on}
                        tabIndex={0}
                        onClick={() => setSelectedId(e.id)}
                        onKeyDown={(ev) => {
                          if (ev.key === 'Enter' || ev.key === ' ') {
                            ev.preventDefault()
                            setSelectedId(e.id)
                          }
                        }}
                        className={`cursor-pointer hover:bg-[#1c2029] ${on ? 'bg-[#1c2029]' : ''}`}
                      >
                        <td className="mono">{e.date}</td>
                        <td>
                          <span className={`tag ${tagClass(e.kind)}`}>{kindLabel(e.kind)}</span>
                        </td>
                        <td>{e.title}</td>
                        <td className={`mono text-right ${signClass(e.ret5)}`}>{pct(e.ret5)}</td>
                        <td>
                          <div className="relative h-2 rounded bg-line">
                            <i
                              className="absolute top-0 left-0 h-full rounded"
                              style={{ width: `${w}%`, background: (e.ret5 ?? 0) >= 0 ? COLORS.up : COLORS.down }}
                            />
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                  {impRows.length === 0 && (
                    <tr>
                      <td colSpan={5} className="muted">
                        이 종류의 과거 이벤트 영향 값은 아직 없다. (영향 표는 정책·전쟁·선거 사건만 계산한다.)
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
          <div className="flex flex-wrap gap-3 items-center text-[13px]">
            <span className="muted">{impRows.length}건 표시</span>
            {impact.data && impact.data.length >= limit && (
              <button type="button" className="btn ml-auto" onClick={() => setLimit((l) => l + 80)}>
                더 보기
              </button>
            )}
          </div>
        </section>
      </div>

      {selected && (
        <section className="card px-4 sm:px-6 py-5 flex flex-wrap gap-6 items-start" aria-label="선택한 이벤트" data-testid="event-detail">
          <div className="flex-[1_1_320px] flex flex-col gap-2">
            <span className="muted text-[13px]">선택한 이벤트</span>
            <div className="text-lg font-semibold">
              {selected.date} {selected.title}
            </div>
            <div className="muted text-sm leading-relaxed">
              전후 ±5거래일 SOX <b className={signClass(selected.ret5)}>{pct(selected.ret5)}</b>. 그 뒤 1개월{' '}
              <b className={signClass(selected.ret_1m)}>{pct(selected.ret_1m)}</b>, 3개월{' '}
              <b className={signClass(selected.ret_3m)}>{pct(selected.ret_3m)}</b>.
              {selected.source && (
                <>
                  {' '}
                  <a href={selected.source} target="_blank" rel="noreferrer">
                    출처
                  </a>
                </>
              )}
            </div>
            <Link to={`/?as_of=${selected.date}`} className="btn self-start !no-underline !text-fg">
              그때 국면 비교 열기
            </Link>
          </div>
          <figure className="flex-[2_1_480px] m-0 min-w-0">
            {path.isLoading && <Loading />}
            {path.isError && <ErrorBox error={path.error} what="전후 경로" />}
            {path.data && <EventPathChart points={path.data.points} />}
            <figcaption className="muted text-xs mt-1">이벤트 당일 SOX 종가 = 100, 전후 30거래일.</figcaption>
          </figure>
        </section>
      )}

      <Honesty>
        <span>과거 이벤트의 수익률은 "그때 그렇게 움직였다"는 기록이지, 다음에도 그렇다는 뜻이 아니다.</span>
        <span className="muted sm:ml-auto">일정 출처: 연준, 노동통계국(FRED), 야후 · 사건 목록은 수작업</span>
      </Honesty>
    </>
  )
}

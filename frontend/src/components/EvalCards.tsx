import { useState } from 'react'
import type { ExplainResponse, FitCheck, Match, SimilarityQuery } from '../api'
import { useExplain } from '../hooks'
import { fmtDays, fmtMacro, HORIZONS, HORIZON_LABEL, LAYER_LABEL, pct, score, signClass } from '../lib/format'
import { rankColor } from '../lib/palette'
import { readNote } from '../lib/readNote'
import { ErrorBox, Loading } from './Status'

interface EvidenceRow {
  label: string
  then: string
  now: string
  note: string
}

/** explain 응답 → 평가표 근거 행. 읽는 법은 readNote 규칙 함수로 만든다. */
export function evidenceRows(e: ExplainResponse): EvidenceRow[] {
  const { drawdown, drawdown_days } = e.price
  // 낙폭은 음수라서 "높다/낮다"가 헷갈리므로 깊이(절댓값)로 비교한다.
  const abs = (x: number | null) => (x == null ? null : Math.abs(x))
  const rows: EvidenceRow[] = [
    {
      label: '전고점 대비 낙폭 (깊이)',
      then: pct(drawdown.then, 1, false),
      now: pct(drawdown.now, 1, false),
      note: readNote(abs(drawdown.then), abs(drawdown.now)),
    },
    {
      label: '낙폭 지속일',
      then: fmtDays(drawdown_days.then),
      now: fmtDays(drawdown_days.now),
      note: readNote(drawdown_days.then, drawdown_days.now),
    },
  ]
  for (const m of e.macro) {
    rows.push({
      label: m.series === 'DFF' ? `${m.label}(DFF)` : m.label,
      then: m.then == null ? '데이터 없음' : fmtMacro(m.then, m.unit),
      now: fmtMacro(m.now, m.unit),
      note: readNote(m.then, m.now),
    })
  }
  for (const c of e.cycle) {
    const isChange = c.metric.includes('chg')
    const f = (v: number | null) => (v == null ? '데이터 없음' : isChange ? `${pct(v)}p` : pct(v))
    rows.push({ label: c.label, then: f(c.then), now: f(c.now), note: readNote(c.then, c.now) })
  }
  return rows
}

/** 코멘트 블록: 서버가 만든 규칙 기반 문장(계약 PHASE6 26절)을 그대로 보여준다. */
export function CommentBlock({ comment }: { comment: string }) {
  return (
    <p className="m-0 text-[13px] leading-relaxed text-sub border-l-2 border-edge pl-3" data-testid="eval-comment">
      <span className="font-semibold">왜 비슷한가 · </span>
      {comment}
    </p>
  )
}

function EvalCard({
  match,
  fit,
  q,
  defaultExpanded,
}: {
  match: Match
  fit?: FitCheck
  q: SimilarityQuery
  defaultExpanded: boolean
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const explain = useExplain(match.end, q, expanded)
  const color = rankColor(match.rank)
  const layers = ['price_shape', 'macro', 'semi_cycle'] as const
  return (
    <article className="card p-5 flex flex-col gap-3.5" style={{ borderTop: `4px solid ${color}` }} data-testid="eval-card">
      <div className="flex flex-wrap items-baseline gap-3">
        <span className="text-[13px] font-semibold" style={{ color }}>
          {match.rank}위
        </span>
        <span className="mono text-[17px] font-semibold">
          {match.start} ~ {match.end}
        </span>
        {fit?.status === 'weak' && (
          <span
            className="text-[12px] rounded-md border border-[#6b4f1d] bg-[#2a2113] text-[#f0c070] px-2 py-0.5"
            title={`적합 조건 5개 중 ${fit.n_passed}개만 충족 — 닮았다고 보기 어렵다`}
            data-testid="weak-badge"
          >
            참고용
          </span>
        )}
        <span className="mono ml-auto text-[26px] font-bold" title="종합 유사도 (0~1)">
          {score(match.combined)}
        </span>
      </div>
      <div className="grid grid-cols-[90px_1fr_44px] gap-x-2.5 gap-y-2 items-center text-[13px]">
        {layers.map((k) => (
          <FragmentRow key={k} label={LAYER_LABEL[k]} value={match.scores[k]} />
        ))}
      </div>

      {expanded && (
        <>
          {explain.isLoading && <Loading what="근거 계산 중…" />}
          {explain.isError && <ErrorBox error={explain.error} what="근거" />}
          {explain.data && (
            <div className="overflow-x-auto">
              <table className="dt min-w-[420px]">
                <thead>
                  <tr>
                    <th>근거</th>
                    <th className="!text-right">그때</th>
                    <th className="!text-right">지금</th>
                    <th>읽는 법</th>
                  </tr>
                </thead>
                <tbody>
                  {evidenceRows(explain.data).map((r) => (
                    <tr key={r.label}>
                      <td>{r.label}</td>
                      <td className="mono text-right">{r.then}</td>
                      <td className="mono text-right">{r.now}</td>
                      <td className="muted">{r.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      <div className="flex gap-4 text-[13px] flex-wrap">
        <span className="muted">이 국면 뒤</span>
        {HORIZONS.map((h) => {
          const v = match.forward[String(h)]
          return (
            <span key={h}>
              {HORIZON_LABEL[h]} <b className={`mono ${signClass(v)}`}>{pct(v)}</b>
            </span>
          )
        })}
      </div>

      {expanded && explain.data?.comment && <CommentBlock comment={explain.data.comment} />}

      {!defaultExpanded && (
        <button
          type="button"
          className="btn self-start !px-3.5 !rounded-lg"
          aria-expanded={expanded}
          onClick={() => setExpanded((x) => !x)}
        >
          {expanded ? '근거 접기' : '근거 펼치기'}
        </button>
      )}
    </article>
  )
}

function FragmentRow({ label, value }: { label: string; value: number }) {
  return (
    <>
      <span>{label}</span>
      <div className="bar">
        <i style={{ width: `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%` }} />
      </div>
      <span className="mono text-right">{score(value)}</span>
    </>
  )
}

export default function EvalCards({
  matches,
  fit,
  q,
}: {
  matches: Match[]
  /** matches 와 같은 순서의 적합 판정. 서버가 안 내면 undefined/null */
  fit?: FitCheck[] | null
  q: SimilarityQuery
}) {
  const top = matches.slice(0, 3)
  const rest = matches.slice(3)
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="m-0 text-lg">왜 닮았다고 보나 — 평가표</h2>
        <span className="muted text-[13px]">점수는 0~1, 1이 가장 닮음. 레이어별 근거는 "그때 값 / 지금 값"으로 나란히 보여준다.</span>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
        {top.map((m, i) => (
          <EvalCard key={m.end} match={m} fit={fit?.[i]} q={q} defaultExpanded />
        ))}
        {rest.length > 0 && (
          <div className="flex flex-col gap-4">
            {rest.map((m, i) => (
              <EvalCard key={m.end} match={m} fit={fit?.[top.length + i]} q={q} defaultExpanded={false} />
            ))}
          </div>
        )}
      </div>
    </section>
  )
}

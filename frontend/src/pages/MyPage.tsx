import { useState } from 'react'
import { ErrorBox, Honesty, Loading } from '../components/Status'
import { usePositionsApply, usePositionsLatest, useSimilarity } from '../hooks'
import { HORIZON_LABEL, money, num, pct, signClass } from '../lib/format'
import type { ApplyRow, PositionsLatest } from '../api'

const SNAPSHOT_CMD = 'uv run python -m stocker.broker snapshot'

function ConnectionCard({ data }: { data: PositionsLatest | null | undefined }) {
  const [hint, setHint] = useState(false)
  return (
    <section className="card flex-[1_1_360px] min-w-0 px-4 sm:px-6 py-5 flex flex-col gap-3.5" aria-label="증권사 연결">
      <h2 className="m-0 text-lg">증권사 연결</h2>
      <div className="flex items-center gap-3 flex-wrap">
        <span className="font-semibold">토스증권</span>
        {data ? <span className="ok">스냅샷 있음 · 조회만</span> : <span className="tag t-other">스냅샷 없음</span>}
      </div>
      <dl className="m-0 grid grid-cols-[96px_1fr] sm:grid-cols-[120px_1fr] gap-x-3 gap-y-2 text-sm">
        <dt className="muted">권한</dt>
        <dd className="m-0">계좌·보유종목·시세 조회. 주문 권한 없음(코드에 주문 기능 자체가 없음)</dd>
        <dt className="muted">키 보관</dt>
        <dd className="m-0">내 컴퓨터의 .env 파일에만. 저장소(git)에는 올라가지 않음</dd>
        <dt className="muted">마지막 조회</dt>
        <dd className="m-0 mono">{data ? data.snapshot_date : '—'}</dd>
        <dt className="muted">허용 IP</dt>
        <dd className="m-0">토스 WTS에 등록한 IP에서만 호출됨</dd>
      </dl>
      <div className="flex gap-2 flex-wrap">
        <button type="button" className="btn dark" onClick={() => setHint((h) => !h)} aria-expanded={hint}>
          지금 가져오기
        </button>
      </div>
      {hint && (
        <div className="text-[13px] muted" role="note">
          화면에서 바로 가져오는 API는 아직 없다. 터미널에서 실행하면 최신 스냅샷이 저장된다:
          <pre className="mt-1.5 mb-0 p-2.5 rounded-lg bg-bg border border-line text-fg overflow-x-auto">{SNAPSHOT_CMD}</pre>
        </div>
      )}
    </section>
  )
}

function PositionsCard({ data }: { data: PositionsLatest | null | undefined }) {
  return (
    <section className="card flex-[2_1_520px] min-w-0 px-4 sm:px-6 py-5 flex flex-col gap-3" aria-label="보유 포지션">
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="m-0 text-lg">보유 포지션</h2>
        <span className="muted text-[13px]">{data ? `스냅샷 ${data.snapshot_date} · 매일 아침 자동 갱신` : '아직 가져온 스냅샷이 없다'}</span>
      </div>
      {data ? (
        <div className="overflow-x-auto">
          <table className="dt min-w-[520px]" data-testid="positions-table">
            <thead>
              <tr>
                <th>종목</th>
                <th className="!text-right">수량</th>
                <th className="!text-right">평단</th>
                <th className="!text-right">현재가</th>
                <th className="!text-right">평가액</th>
                <th className="!text-right">손익률</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((p) => (
                <tr key={p.symbol} data-testid="position-row">
                  <td>
                    <b>{p.symbol}</b>
                    {p.symbol === 'SOXL' && <span className="muted"> 반도체 3배</span>}
                  </td>
                  <td className="mono text-right">{num(p.quantity, Number.isInteger(p.quantity) ? 0 : 4)}</td>
                  <td className="mono text-right">{money(p.avg_price, p.currency, 2)}</td>
                  <td className="mono text-right">{money(p.last_price, p.currency, 2)}</td>
                  <td className="mono text-right">{money(p.market_value, p.currency, 2)}</td>
                  <td className={`mono text-right ${signClass(p.pnl_rate)}`}>{pct(p.pnl_rate)}</td>
                </tr>
              ))}
              {data.items.length === 0 && (
                <tr>
                  <td colSpan={6} className="muted">
                    스냅샷에 보유 종목이 없다.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="muted text-sm leading-relaxed" data-testid="no-positions">
          아직 저장된 포지션이 없다. <code>.env</code>에 토스 오픈 API 키(<code>TOSS_CLIENT_ID</code>, <code>TOSS_CLIENT_SECRET</code>)를 넣고{' '}
          <code>{SNAPSHOT_CMD}</code>를 한 번 실행하면 여기에 나타난다.
        </div>
      )}
      <div className="muted text-[13px]">국면 비교에 쓰는 종목은 SOXL이다. 다른 종목은 기록용으로만 저장된다.</div>
    </section>
  )
}

function ApplyCard({ windowLen }: { windowLen: number }) {
  const apply = usePositionsApply({ window: windowLen })
  const sim = useSimilarity({ window: windowLen })
  const s12 = sim.data?.forward_summary.find((x) => x.horizon === 252)
  const rows = (apply.data?.rows ?? []).filter((r) => r.horizon === 252)
  const cur = apply.data?.currency ?? 'USD'
  const base = apply.data?.position_value ?? 0
  const cell = (r: ApplyRow, k: 'median' | 'p10' | 'p90') => {
    const v = r[k]
    return v == null ? (
      '—'
    ) : (
      <span className={signClass(v)}>
        {money(base * (1 + v), cur)} ({pct(v)})
      </span>
    )
  }
  return (
    <div className="border border-line rounded-[10px] p-4 flex flex-col gap-2.5 min-w-0" data-testid="apply-card">
      <div className="font-semibold">
        {windowLen}거래일 창 (약 {windowLen === 60 ? 3 : windowLen === 120 ? 6 : 12}개월) — {HORIZON_LABEL[252]} 후
      </div>
      {apply.isLoading && <Loading />}
      {apply.isError && <ErrorBox error={apply.error} what="분포 적용" />}
      {apply.data && (
        <div className="overflow-x-auto">
          <table className="dt min-w-[420px]">
            <thead>
              <tr>
                <th>기준</th>
                <th className="!text-right">중앙값</th>
                <th className="!text-right">하위 10%</th>
                <th className="!text-right">상위 10%</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.basis}>
                  <td>{r.basis === 'lev3' ? <b>3배 레버리지 기준</b> : 'SOX 기준'}</td>
                  <td className="mono text-right">{cell(r, 'median')}</td>
                  <td className="mono text-right">{cell(r, 'p10')}</td>
                  <td className="mono text-right">{cell(r, 'p90')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="muted text-xs">
        표본 {apply.data?.n ?? '—'}
        {s12 ? ` · 상승 ${s12.n_up} / 하락 ${s12.n_down}` : ''}
      </div>
    </div>
  )
}

export default function MyPage() {
  const latest = usePositionsLatest()
  const has = latest.data != null
  const value = latest.data?.items.find((p) => p.symbol === 'SOXL')?.market_value
  return (
    <>
      {latest.isError && <ErrorBox error={latest.error} what="포지션" />}
      <div className="flex flex-wrap gap-6 items-start">
        <ConnectionCard data={latest.data} />
        {latest.isLoading ? <Loading /> : <PositionsCard data={latest.data} />}
      </div>

      <section className="card px-4 sm:px-6 py-5 flex flex-col gap-3.5" aria-label="분포 적용">
        <div className="flex flex-wrap items-baseline gap-3">
          <h2 className="m-0 text-lg">닮은 국면의 결과를 내 SOXL 평가액에 적용하면</h2>
          <span className="muted text-[13px]">
            {has ? `내 평가액 기준${value != null ? ` (SOXL ${money(value, 'USD', 0)})` : ''}` : '포지션 스냅샷이 있어야 계산된다.'}
          </span>
        </div>
        {has ? (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <ApplyCard windowLen={120} />
            <ApplyCard windowLen={250} />
          </div>
        ) : (
          !latest.isLoading && <div className="muted text-sm">위에서 포지션을 가져오면 닮은 국면의 결과가 내 평가액으로 환산된다.</div>
        )}
        <div className="muted text-[13px] leading-relaxed">
          '3배 레버리지 기준'은 닮은 국면마다 SOX 경로를 하루 단위 3배로 따라간 값이라 변동성 손실(레버리지 감가)이 들어 있다. 두
          창의 답이 다르다는 것 자체가 정보다. 표본의 과거 경로를 적용한 값이며 예측이 아니다.
        </div>
      </section>

      <section className="card px-4 sm:px-6 py-5 flex flex-col gap-3" aria-label="매매 복기">
        <div className="flex flex-wrap items-baseline gap-3">
          <h2 className="m-0 text-lg">매매 복기</h2>
          <span className="muted text-[13px]">내가 산 날, 그날 엔진은 어떤 국면이라고 봤나. 체결 내역 연동 후 채워진다.</span>
        </div>
        <div className="overflow-x-auto">
          <table className="dt min-w-[560px]">
            <thead>
              <tr>
                <th>체결일</th>
                <th>매수/매도</th>
                <th className="!text-right">수량</th>
                <th className="!text-right">단가</th>
                <th>그날 1위 유사 국면</th>
                <th className="!text-right">그 국면의 12개월 후</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td colSpan={6} className="muted" data-testid="trades-empty">
                  아직 체결 내역이 연동되지 않았다. 연동되면 매수일마다 그날의 1위 유사 국면과 그 뒤 결과가 여기에 채워진다.
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <Honesty>
        <span>이 페이지만 계좌 데이터를 쓴다. 국면 비교·이벤트 페이지는 공개 데이터만 쓴다.</span>
        <span className="muted sm:ml-auto">계좌번호 미저장 · 키는 .env에만 · 주문 기능 없음</span>
      </Honesty>
    </>
  )
}

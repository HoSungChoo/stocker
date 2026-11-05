import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMockApi } from '../api/mock'
import type { Api } from '../api'
import { renderApp } from './test-utils'

// 계약 PHASE6 26절 · 23절: 코멘트 블록, "참고용" 배지, "해당 없음" 배너와 기준선 표시, 모드 토글.

describe('/ Phase 6', () => {
  it('상위 3개 카드마다 코멘트 블록이 보이고 4문장 구조다', async () => {
    renderApp('/')
    await waitFor(() => expect(screen.getAllByTestId('eval-comment')).toHaveLength(3))
    const comments = screen.getAllByTestId('eval-comment')
    expect(comments[0]).toHaveTextContent('낙폭이 -28.7% 대 -29.8%로 거의 같고')
    expect(comments[0]).toHaveTextContent('이 국면은 위치는 닮았지만 환경은 다른 사례다.')
    expect(comments[0]).toHaveTextContent('이 국면 뒤 12개월 SOX는 -38.5%였다.')
  })

  it('weak 국면(4·5위)에만 "참고용" 배지가 붙고, 적합하면 배너는 없다', async () => {
    renderApp('/')
    const cards = await screen.findAllByTestId('eval-card')
    expect(within(cards[0]).queryByTestId('weak-badge')).toBeNull()
    expect(within(cards[3]).getByTestId('weak-badge')).toHaveTextContent('참고용')
    expect(within(cards[4]).getByTestId('weak-badge')).toBeInTheDocument()
    expect(screen.getAllByTestId('weak-badge')).toHaveLength(2)
    expect(screen.queryByTestId('not-applicable-banner')).toBeNull()
    expect(screen.getAllByTestId('dist-row-baseline')).toHaveLength(4) // 기준선 줄은 적합해도 항상 보인다
  })

  it('applicable=false 이면 맨 위 배너, 윗줄 비움, 기준선 줄, 표본 "0 (기준선 사용)"이 뜬다', async () => {
    const base = createMockApi({ latency: 0 })
    renderApp('/', {
      ...base,
      similarity: async (q) => {
        const r = await base.similarity(q)
        return {
          ...r,
          applicable: false,
          applicable_n: 0,
          forward_summary_source: 'baseline',
          forward_summary: r.forward_summary_baseline ?? r.forward_summary,
        }
      },
    })
    const banner = await screen.findByTestId('not-applicable-banner')
    expect(banner).toHaveTextContent('현재와 충분히 닮은 과거 사례가 없다')
    expect(banner).toHaveTextContent('아래 분포는 전체 과거 기준이다')
    await waitFor(() => expect(screen.getAllByTestId('baseline-tag')).toHaveLength(4))
    expect(screen.getByText('닮은 국면 0개의 그 뒤 (사례)')).toBeInTheDocument()
    expect(screen.getAllByTestId('dist-row-similar')[0]).toHaveTextContent('적합한 국면 없음')
    expect(screen.getByTestId('sample-n')).toHaveTextContent('0 (기준선 사용)')
    expect(screen.getByTestId('up-down')).toHaveTextContent('—')
  })

  it('서버가 Phase 6 필드를 안 내면(null) 배지·배너·토글이 모두 숨겨진다', async () => {
    const base = createMockApi({ latency: 0 })
    renderApp('/', {
      ...base,
      similarity: async (q) => ({
        ...(await base.similarity(q)),
        mode: null,
        fit: null,
        applicable: null,
        applicable_n: null,
        forward_summary_source: null,
      }),
    })
    await screen.findAllByTestId('eval-card')
    expect(screen.queryByTestId('weak-badge')).toBeNull()
    expect(screen.queryByTestId('not-applicable-banner')).toBeNull()
    expect(screen.queryByRole('group', { name: '비교 방식' })).toBeNull()
    // 판정이 없으면 분포에 상위 K 전체가 쓰인 것으로 보고 괄호 설명 없이 K 를 표시한다
    expect(screen.getByTestId('sample-n')).toHaveTextContent(/^5$/)
  })

  it('mode 가 있으면 토글이 보이고, 환경 우선을 누르면 mode=regime 으로 다시 요청한다', async () => {
    const base = createMockApi({ latency: 0 })
    const similarity = vi.fn<Api['similarity']>((q) => base.similarity(q))
    renderApp('/', { ...base, similarity })
    const group = await screen.findByRole('group', { name: '비교 방식' })
    expect(within(group).getByRole('button', { name: '모양 우선' })).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(within(group).getByRole('button', { name: '환경 우선' }))
    await waitFor(() => expect(similarity.mock.calls.some(([q]) => q.mode === 'regime')).toBe(true))
    expect(within(group).getByRole('button', { name: '환경 우선' })).toHaveAttribute('aria-pressed', 'true')
  })
})

describe('/ 기준선 병기 · 사례 문구', () => {
  it('분포 4칸마다 위 "닮은 국면 n개", 아래 "전체 과거 N개" 두 줄이 같은 포맷으로 나온다', async () => {
    renderApp('/')
    const cells = await screen.findAllByTestId('dist-cell')
    expect(cells).toHaveLength(4)
    const last = cells[3]
    const sim = within(last).getByTestId('dist-row-similar')
    const base = within(last).getByTestId('dist-row-baseline')
    expect(sim).toHaveTextContent('닮은 국면 3개')
    expect(sim).toHaveTextContent('-3.9%')
    expect(sim).toHaveTextContent('-31.6% ~ +54.3% · 상승 1 / 하락 2')
    expect(base).toHaveTextContent('전체 과거 7565개')
    expect(base).toHaveTextContent('+17.1%')
    expect(base).toHaveTextContent('-34.1% ~ +79.3% · 상승 5016 / 하락 2547')
    expect(screen.getByTestId('baseline-note')).toHaveTextContent(
      '백테스트(1999~2025)에서 닮은 5개의 분포는 기준선보다 12개월 결과를 더 잘 설명하지 못했다. 두 줄을 함께 보라.',
    )
  })

  it('제목과 평가표 설명이 사례 문구다', async () => {
    renderApp('/')
    expect(await screen.findByText('닮은 국면 3개의 그 뒤 (사례)')).toBeInTheDocument()
    expect(screen.getByText("상위 국면은 '이런 일이 있었다'는 사례다. 통계적 근거로 읽지 않는다.")).toBeInTheDocument()
    expect(screen.getByText('왜 닮았다고 보나 — 평가표')).toBeInTheDocument()
  })

  it('서버가 기준선을 안 내면 기준선 줄과 안내 문구는 숨긴다', async () => {
    const base = createMockApi({ latency: 0 })
    renderApp('/', {
      ...base,
      similarity: async (q) => ({ ...(await base.similarity(q)), forward_summary_baseline: null }),
    })
    await screen.findAllByTestId('dist-cell')
    expect(screen.queryAllByTestId('dist-row-baseline')).toHaveLength(0)
    expect(screen.queryByTestId('baseline-note')).toBeNull()
  })
})

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
    expect(screen.queryAllByTestId('baseline-tag')).toHaveLength(0)
  })

  it('applicable=false 이면 맨 위 배너와 분포 4칸의 "기준선" 표시가 뜬다', async () => {
    const base = createMockApi({ latency: 0 })
    renderApp('/', {
      ...base,
      similarity: async (q) => ({
        ...(await base.similarity(q)),
        applicable: false,
        applicable_n: 0,
        forward_summary_source: 'baseline',
      }),
    })
    const banner = await screen.findByTestId('not-applicable-banner')
    expect(banner).toHaveTextContent('현재와 충분히 닮은 과거 사례가 없다')
    expect(banner).toHaveTextContent('아래 분포는 전체 과거 기준이다')
    await waitFor(() => expect(screen.getAllByTestId('baseline-tag')).toHaveLength(4))
    expect(screen.getByText('전체 과거 기준으로, SOX는')).toBeInTheDocument()
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

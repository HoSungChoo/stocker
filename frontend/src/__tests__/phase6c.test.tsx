import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMockApi } from '../api/mock'
import type { Api } from '../api'
import { parseMarkers, serializeMarkers } from '../lib/kinds'
import { renderApp } from './test-utils'

// 계약 PHASE6C 30·31절: 체크리스트 패널, 평가표 배지, 이벤트 마커 토글.

const markerCount = (chart: HTMLElement) => chart.querySelectorAll('[data-testid="event-marker"]').length

describe('/ 체크리스트 패널', () => {
  it('그룹 4개 소제목, 신호 9행, 지금 + 상위 5 국면 열, 고정 문구가 보인다', async () => {
    renderApp('/')
    const panel = await screen.findByTestId('checklist-panel')
    for (const g of ['바닥 신호', '버블 끝 신호', '과열 신호', '위기 신호']) {
      expect(within(panel).getByText(g)).toBeInTheDocument()
    }
    const rows = within(panel).getAllByTestId('sig-row')
    expect(rows).toHaveLength(9)
    // 열: 신호 이름 + 지금 + 5개 국면
    expect(within(rows[0]).getAllByTestId('sig-cell')).toHaveLength(6)
    expect(within(panel).getByText('1위')).toBeInTheDocument()
    expect(within(panel).getByText('2000-08-08')).toBeInTheDocument()
    expect(screen.getByTestId('checklist-note')).toHaveTextContent(
      '체크리스트는 점수에 쓰이지 않는다. 1990년대 사례에서 쓰인 신호를 규칙으로 옮긴 것이다.',
    )
  })

  it('● 켜짐 ○ 꺼짐 – 데이터 없음, 호버(title)에 값과 임계값이 있다', async () => {
    renderApp('/')
    const panel = await screen.findByTestId('checklist-panel')
    const ipo = within(panel).getAllByTestId('sig-row').find((r) => r.dataset.signal === 'bubble_ipo')!
    const [now, first] = within(ipo).getAllByTestId('sig-cell')
    expect(now).toHaveAttribute('data-state', 'na')
    expect(now).toHaveTextContent('–')
    expect(first).toHaveAttribute('data-state', 'on')
    expect(first).toHaveTextContent('●')
    expect(first.getAttribute('title')).toContain('값 71.2%')
    expect(first.getAttribute('title')).toContain('켜짐 조건 ≥ 50%')
    const cape = within(panel).getAllByTestId('sig-row').find((r) => r.dataset.signal === 'heat_cape')!
    expect(within(cape).getAllByTestId('sig-cell')[0]).toHaveAttribute('data-state', 'on')
    const inv = within(panel).getAllByTestId('sig-row').find((r) => r.dataset.signal === 'bubble_inversion')!
    const invNow = within(inv).getAllByTestId('sig-cell')[0]
    expect(invNow).toHaveAttribute('data-state', 'off')
    expect(invNow).toHaveTextContent('○')
  })

  it('평가표 카드에 그때/지금 켜진 신호 배지 줄이 나온다', async () => {
    renderApp('/')
    const cards = await screen.findAllByTestId('eval-card')
    await waitFor(() => expect(within(cards[0]).getByTestId('signal-badges')).toBeInTheDocument())
    const then = within(cards[0]).getByTestId('badges-then')
    expect(then).toHaveTextContent('그때 켜진 신호')
    expect(then).toHaveTextContent('장단기 금리 역전')
    expect(then).toHaveTextContent('일드 스프레드')
    expect(then).toHaveTextContent('공모주 첫날 수익률')
    expect(within(cards[0]).getByTestId('badges-now')).toHaveTextContent('지금 켜진 신호주가 고평가(CAPE)')
    // 2위(2022-04-25)는 켜진 게 없다
    expect(within(cards[1]).getByTestId('badges-then')).toHaveTextContent('없음')
  })

  it('체크리스트 API 가 실패해도 나머지 화면은 그려지고, 배지 줄은 숨는다', async () => {
    const base = createMockApi({ latency: 0 })
    renderApp('/', { ...base, checklist: () => Promise.reject(new Error('boom')) })
    const cards = await screen.findAllByTestId('eval-card')
    expect(cards).toHaveLength(5)
    expect(await screen.findByText('체크리스트을(를) 불러오지 못했다')).toBeInTheDocument()
    expect(screen.queryByTestId('checklist-panel')).toBeNull()
    expect(screen.queryByTestId('signal-badges')).toBeNull()
  })

  it('공통 쿼리로 요청한다', async () => {
    const base = createMockApi({ latency: 0 })
    const checklist = vi.fn<Api['checklist']>((q) => base.checklist(q))
    renderApp('/?as_of=2000-03-10', { ...base, checklist })
    await screen.findByTestId('checklist-panel')
    expect(checklist.mock.calls[0][0]).toMatchObject({ as_of: '2000-03-10', window: 120, top_k: 5 })
  })
})

describe('/ 이벤트 마커 토글', () => {
  it('버튼 4개가 기본 전부 켜져 있다', async () => {
    renderApp('/')
    const group = await screen.findByRole('group', { name: '이벤트 마커' })
    const buttons = within(group).getAllByRole('button')
    expect(buttons.map((b) => b.textContent)).toEqual(['● FOMC', '▲ CPI·고용', '◆ 실적', '■ 정책·전쟁·선거'])
    for (const b of buttons) expect(b).toHaveAttribute('aria-pressed', 'true')
  })

  it('FOMC 를 끄면 현재·과거 창의 FOMC 마커가 모두 사라지고, URL 에 반영된다', async () => {
    renderApp('/')
    const chart = await screen.findByTestId('overlay-chart')
    await waitFor(() => expect(markerCount(chart)).toBe(6)) // 현재 4 + 1위 2
    await userEvent.click(screen.getByRole('button', { name: /FOMC/ }))
    await waitFor(() => expect(markerCount(chart)).toBe(2)) // cpi 1 + earnings 1
    expect(screen.getByRole('button', { name: /FOMC/ })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByTestId('location')).toHaveTextContent('markers=cpi%2Cearnings%2Cpolicy')
    await userEvent.click(screen.getByRole('button', { name: /FOMC/ }))
    await waitFor(() => expect(markerCount(chart)).toBe(6))
    expect(screen.getByTestId('location')).not.toHaveTextContent('markers=')
  })

  it('URL 쿼리 markers=fomc 로 열면 FOMC 만 켜져 있다 (새로고침 유지)', async () => {
    renderApp('/?markers=fomc')
    const chart = await screen.findByTestId('overlay-chart')
    await waitFor(() => expect(markerCount(chart)).toBe(4))
    expect(screen.getByRole('button', { name: /FOMC/ })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: /CPI/ })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: /실적/ })).toHaveAttribute('aria-pressed', 'false')
  })

  it('markers= (빈 값)이면 전부 꺼져 마커가 없다', async () => {
    renderApp('/?markers=')
    const chart = await screen.findByTestId('overlay-chart')
    await waitFor(() => expect(chart.querySelectorAll('.recharts-line').length).toBe(11))
    expect(markerCount(chart)).toBe(0)
    for (const b of within(screen.getByRole('group', { name: '이벤트 마커' })).getAllByRole('button')) {
      expect(b).toHaveAttribute('aria-pressed', 'false')
    }
  })
})

describe('markers 쿼리 변환', () => {
  it('없으면 전부 켜짐, 빈 값은 전부 꺼짐, 모르는 토큰은 무시', () => {
    expect([...parseMarkers(null)].sort()).toEqual(['data', 'earnings', 'fomc', 'shock'])
    expect(parseMarkers('').size).toBe(0)
    expect([...parseMarkers('fomc,cpi,nope')].sort()).toEqual(['data', 'fomc'])
    expect([...parseMarkers('shock,data')].sort()).toEqual(['data', 'shock']) // 그룹 키도 받는다
  })
  it('전부 켜짐은 쿼리에서 빠지고, 순서는 고정', () => {
    expect(serializeMarkers(parseMarkers(null))).toBeNull()
    expect(serializeMarkers(parseMarkers('policy,fomc'))).toBe('fomc,policy')
    expect(serializeMarkers(new Set())).toBe('')
  })
})

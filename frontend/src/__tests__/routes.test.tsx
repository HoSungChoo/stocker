import { describe, expect, it } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMockApi } from '../api/mock'
import { renderApp } from './test-utils'

describe('/ 국면 비교', () => {
  it('차트 선: 현재 1 + 과거 5×2, 평가표 카드 5개, 분포 4칸', async () => {
    renderApp('/')
    const chart = await screen.findByTestId('overlay-chart')
    await waitFor(() => expect(chart.querySelectorAll('.recharts-line').length).toBe(11))
    expect(await screen.findAllByTestId('eval-card')).toHaveLength(5)
    expect(screen.getAllByTestId('dist-cell')).toHaveLength(4)
    expect(screen.getByTestId('sample-n')).toHaveTextContent('3 (상위 5 중)')
    expect(screen.getByTestId('up-down')).toHaveTextContent('1 상승 · 2 하락')
    expect(screen.getByTestId('layer-status')).toHaveTextContent('모두 켜짐')
    expect(screen.getByText(/이 도구는 예측하지 않는다/)).toBeInTheDocument()
  })

  it('이벤트 마커가 그려진다 (현재 4 + 1위 2)', async () => {
    renderApp('/')
    const chart = await screen.findByTestId('overlay-chart')
    await waitFor(() => expect(chart.querySelectorAll('[data-testid="event-marker"]').length).toBe(6))
  })

  it('범례 체크박스로 과거 창을 끄면 선이 2개 줄어든다', async () => {
    renderApp('/')
    const chart = await screen.findByTestId('overlay-chart')
    await waitFor(() => expect(chart.querySelectorAll('.recharts-line').length).toBe(11))
    await userEvent.click(screen.getByRole('checkbox', { name: /1위/ }))
    await waitFor(() => expect(chart.querySelectorAll('.recharts-line').length).toBe(9))
    await userEvent.click(screen.getByRole('checkbox', { name: /1위/ }))
    await waitFor(() => expect(chart.querySelectorAll('.recharts-line').length).toBe(11))
  })

  it('상위 3개는 근거가 펼쳐지고 읽는 법이 규칙대로 나온다', async () => {
    renderApp('/')
    const cards = await screen.findAllByTestId('eval-card')
    const first = cards[0]
    const dff = await within(first).findByText('기준금리(DFF)')
    const row = dff.closest('tr') as HTMLElement
    expect(within(row).getByText('6.44%')).toBeInTheDocument()
    expect(within(row).getByText('그때가 높음')).toBeInTheDocument()
    // 2000년에는 달러지수가 없다
    const usd = within(first).getByText('달러지수').closest('tr') as HTMLElement
    expect(within(usd).getAllByText('데이터 없음').length).toBeGreaterThanOrEqual(2) // 그때 값 + 읽는 법
    // 부호가 다른 장단기 스프레드
    const spread = within(first).getByText('장단기 스프레드').closest('tr') as HTMLElement
    expect(within(spread).getByText('방향이 다름')).toBeInTheDocument()
  })

  it('4~5위는 접혀 있다가 "근거 펼치기"로 열린다', async () => {
    renderApp('/')
    const cards = await screen.findAllByTestId('eval-card')
    expect(within(cards[3]).queryByRole('table')).toBeNull()
    await userEvent.click(within(cards[3]).getByRole('button', { name: '근거 펼치기' }))
    expect(await within(cards[3]).findByRole('table')).toBeInTheDocument()
  })

  it('layers_missing 이 있으면 배너가 뜬다', async () => {
    const base = createMockApi({ latency: 0 })
    renderApp('/', {
      ...base,
      similarity: async (q) => ({ ...(await base.similarity(q)), layers_missing: ['macro'] }),
    })
    expect(await screen.findByText(/일부 레이어가 꺼져 있다/)).toBeInTheDocument()
  })

  it('API 오류는 오류 상자로 보인다', async () => {
    const base = createMockApi({ latency: 0 })
    renderApp('/', { ...base, similarity: () => Promise.reject(new Error('API 500: boom')) })
    expect(await screen.findByText(/API 500: boom/)).toBeInTheDocument()
  })

  it('슬라이더를 움직이면 합이 100%를 유지한다', async () => {
    renderApp('/')
    const slider = (await screen.findByLabelText('가격 형태 가중치')) as HTMLInputElement
    fireEvent.change(slider, { target: { value: '80' } })
    const vals = ['가격 형태 가중치', '매크로 가중치', '산업 사이클 가중치'].map((n) =>
      Number((screen.getByLabelText(n) as HTMLInputElement).value),
    )
    expect(vals[0]).toBe(80)
    expect(vals.reduce((a, b) => a + b, 0)).toBe(100)
  })

  it('?as_of= 로 기준일이 들어온다', async () => {
    renderApp('/?as_of=2001-09-11')
    const input = (await screen.findByLabelText('기준일 선택')) as HTMLInputElement
    expect(input.value).toBe('2001-09-11')
  })
})

describe('/events', () => {
  it('일정 3건, 영향 표 10행, 첫 행이 선택되어 상세가 보인다', async () => {
    renderApp('/events')
    expect(await screen.findAllByTestId('impact-row')).toHaveLength(10)
    const list = await screen.findByTestId('upcoming-list')
    expect(within(list).getAllByRole('listitem')).toHaveLength(3)
    expect(within(list).getByText(/날짜는 추정\(미확정\)/)).toBeInTheDocument()
    const detail = await screen.findByTestId('event-detail')
    expect(within(detail).getByText(/2001-09-11/)).toBeInTheDocument()
    expect(within(detail).getByRole('link', { name: '그때 국면 비교 열기' })).toHaveAttribute('href', '/?as_of=2001-09-11')
  })

  it('행을 누르면 상세와 링크가 바뀌고 경로 차트가 그려진다', async () => {
    renderApp('/events')
    const rows = await screen.findAllByTestId('impact-row')
    await userEvent.click(rows[8]) // 2022-10-07 수출규제
    const detail = await screen.findByTestId('event-detail')
    await waitFor(() => expect(within(detail).getByText(/2022-10-07/)).toBeInTheDocument())
    expect(within(detail).getByRole('link', { name: '그때 국면 비교 열기' })).toHaveAttribute('href', '/?as_of=2022-10-07')
    await waitFor(() => expect(within(detail).getByTestId('event-path-chart').querySelectorAll('.recharts-line').length).toBe(1))
  })

  it('종류 필터: 실적만 누르면 일정 1건, 영향 표는 비어 안내 문구', async () => {
    renderApp('/events')
    await screen.findAllByTestId('impact-row')
    await userEvent.click(screen.getByRole('button', { name: '실적' }))
    const list = await screen.findByTestId('upcoming-list')
    expect(within(list).getAllByRole('listitem')).toHaveLength(1)
    expect(screen.queryAllByTestId('impact-row')).toHaveLength(0)
    expect(screen.getByText(/영향 값은 아직 없다/)).toBeInTheDocument()
  })

  it('14일 안에 FOMC 가 없으면 안내', async () => {
    renderApp('/events')
    expect(await screen.findByText(/14일 안에 FOMC는 없음/)).toBeInTheDocument()
  })
})

describe('/my', () => {
  it('포지션 2행과 분포 적용 카드 2개', async () => {
    renderApp('/my')
    expect(await screen.findAllByTestId('position-row')).toHaveLength(2)
    const cards = await screen.findAllByTestId('apply-card')
    expect(cards).toHaveLength(2)
    await within(cards[0]).findByText('3배 레버리지 기준')
    // 평가액 5,182.32 × (1+0.25) = $6,478
    expect(within(cards[0]).getByText(/\$6,478/)).toBeInTheDocument()
    expect(screen.getByTestId('trades-empty')).toBeInTheDocument()
  })

  it('positions/latest 가 null 이면 안내 문구', async () => {
    const base = createMockApi({ latency: 0 })
    renderApp('/my', { ...base, positionsLatest: async () => null })
    expect(await screen.findByTestId('no-positions')).toBeInTheDocument()
    expect(screen.queryAllByTestId('apply-card')).toHaveLength(0)
    expect(screen.queryAllByTestId('position-row')).toHaveLength(0)
  })

  it('지금 가져오기는 터미널 명령을 안내한다', async () => {
    renderApp('/my')
    await userEvent.click(await screen.findByRole('button', { name: '지금 가져오기' }))
    expect(screen.getByRole('note')).toHaveTextContent('stocker.broker snapshot')
  })
})

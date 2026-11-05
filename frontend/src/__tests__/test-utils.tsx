import type { ReactElement } from 'react'
import { render } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ApiProvider } from '../api'
import type { Api } from '../api'
import { createMockApi } from '../api/mock'
import App from '../App'

/** 테스트에서 현재 URL(경로+쿼리)을 읽기 위한 숨은 표시 */
function LocationProbe() {
  const { pathname, search } = useLocation()
  return <span hidden data-testid="location">{`${pathname}${search}`}</span>
}

export function renderApp(path: string, api: Api = createMockApi({ latency: 0, today: '2026-11-05' })) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const ui: ReactElement = (
    <ApiProvider api={api}>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <App />
          <LocationProbe />
        </MemoryRouter>
      </QueryClientProvider>
    </ApiProvider>
  )
  return render(ui)
}

import { createContext, useContext, type ReactNode } from 'react'
import { realApi } from './client'
import { createMockApi } from './mock'
import type { Api } from './types'

export * from './types'
export { ApiError } from './client'

/** VITE_USE_MOCK=true 면 mock, 아니면 실제 API(VITE_API_BASE, 기본 /api). */
export const defaultApi: Api = import.meta.env.VITE_USE_MOCK === 'true' ? createMockApi() : realApi

const ApiContext = createContext<Api>(defaultApi)

export function ApiProvider({ api, children }: { api?: Api; children: ReactNode }) {
  return <ApiContext.Provider value={api ?? defaultApi}>{children}</ApiContext.Provider>
}

export function useApi(): Api {
  return useContext(ApiContext)
}

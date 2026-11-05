import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useApi, type SimilarityQuery } from './api'

const HOUR = 60 * 60 * 1000

export function useHealth() {
  const api = useApi()
  return useQuery({ queryKey: ['health'], queryFn: () => api.health(), staleTime: 5 * 60 * 1000 })
}

export function useSimilarity(q: SimilarityQuery) {
  const api = useApi()
  return useQuery({
    queryKey: ['similarity', q],
    queryFn: () => api.similarity(q),
    staleTime: HOUR,
    placeholderData: keepPreviousData,
  })
}

export function useOverlay(q: SimilarityQuery) {
  const api = useApi()
  return useQuery({
    queryKey: ['overlay', q],
    queryFn: () => api.overlay(q),
    staleTime: HOUR,
    placeholderData: keepPreviousData,
  })
}

export function useExplain(end: string, q: SimilarityQuery, enabled = true) {
  const api = useApi()
  return useQuery({
    queryKey: ['explain', end, q],
    queryFn: () => api.explain(end, q),
    staleTime: HOUR,
    enabled,
  })
}

export function useUpcoming(days = 14) {
  const api = useApi()
  return useQuery({ queryKey: ['events-upcoming', days], queryFn: () => api.eventsUpcoming(days) })
}

export function useImpact(limit: number) {
  const api = useApi()
  return useQuery({
    queryKey: ['events-impact', limit],
    queryFn: () => api.eventsImpact({ limit }),
    placeholderData: keepPreviousData,
  })
}

export function useEventPath(id: number | null, days = 30) {
  const api = useApi()
  return useQuery({
    queryKey: ['event-path', id, days],
    queryFn: () => api.eventPath(id as number, days),
    enabled: id != null,
  })
}

export function usePositionsLatest() {
  const api = useApi()
  return useQuery({ queryKey: ['positions-latest'], queryFn: () => api.positionsLatest() })
}

export function usePositionsApply(q: SimilarityQuery, enabled = true) {
  const api = useApi()
  return useQuery({
    queryKey: ['positions-apply', q],
    queryFn: () => api.positionsApply(q),
    enabled,
  })
}

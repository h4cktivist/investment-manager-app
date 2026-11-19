import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  api,
  qs,
  type Allocation,
  type BookPaymentResult,
  type BondsResponse,
  type CalendarResponse,
  type Dashboard,
  type Metrics,
  type Performance,
  type Position,
  type Summary,
} from '../lib/api'
import { useSelection } from '../lib/store'

/** Общий кусок ключа и query-строки: выбранные портфели. */
function useScope() {
  const { selected, period } = useSelection()
  return { selected, period, scope: qs({ portfolio_id: selected }) }
}

export function useDashboard() {
  const { selected, period } = useScope()
  return useQuery({
    queryKey: ['dashboard', selected, period],
    queryFn: () =>
      api.get<Dashboard>(
        `/portfolio/dashboard${qs({
          portfolio_id: selected,
          period,
          benchmark: ['IMOEX', 'RGBITR', 'RUCBTRNS'],
        })}`,
      ),
  })
}

export function useSummary() {
  const { selected, scope } = useScope()
  return useQuery({
    queryKey: ['summary', selected],
    queryFn: () => api.get<Summary>(`/portfolio/summary${scope}`),
  })
}

export function usePositions(includeClosed = false) {
  const { selected } = useScope()
  return useQuery({
    queryKey: ['positions', selected, includeClosed],
    queryFn: () =>
      api.get<Position[]>(
        `/portfolio/positions${qs({ portfolio_id: selected, include_closed: includeClosed })}`,
      ),
  })
}

export function useAllocation() {
  const { selected, scope } = useScope()
  return useQuery({
    queryKey: ['allocation', selected],
    queryFn: () => api.get<Allocation>(`/portfolio/allocation${scope}`),
  })
}

export function usePerformance(period: string, benchmarks: string[]) {
  const { selected } = useScope()
  return useQuery({
    queryKey: ['performance', selected, period, benchmarks],
    queryFn: () =>
      api.get<Performance>(
        `/portfolio/performance${qs({ portfolio_id: selected, period, benchmark: benchmarks })}`,
      ),
  })
}

export function useMetrics(period: string, benchmark = 'IMOEX', riskFree?: number) {
  const { selected } = useScope()
  return useQuery({
    queryKey: ['metrics', selected, period, benchmark, riskFree],
    queryFn: () =>
      api.get<Metrics>(
        `/portfolio/metrics${qs({ portfolio_id: selected, period, benchmark, risk_free: riskFree })}`,
      ),
  })
}

export function useAllPeriodMetrics(benchmark = 'IMOEX') {
  const { selected } = useScope()
  return useQuery({
    queryKey: ['metrics-all', selected, benchmark],
    queryFn: () =>
      api.get<Record<string, Metrics>>(
        `/portfolio/metrics/all-periods${qs({ portfolio_id: selected, benchmark })}`,
      ),
  })
}

export function useBonds() {
  const { selected, scope } = useScope()
  return useQuery({
    queryKey: ['bonds', selected],
    queryFn: () => api.get<BondsResponse>(`/portfolio/bonds${scope}`),
  })
}

export function useCalendar(dateFrom?: string, dateTo?: string) {
  const { selected } = useScope()
  return useQuery({
    queryKey: ['calendar', selected, dateFrom, dateTo],
    queryFn: () =>
      api.get<CalendarResponse>(
        `/calendar${qs({ portfolio_id: selected, date_from: dateFrom, date_to: dateTo })}`,
      ),
  })
}

export interface BookPaymentPayload {
  asset_id: number
  kind: string
  payment_date: string
  portfolio_ids?: number[]
}

/** Зачислить выплату из календаря в сделки. */
export function useBookPayment() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: BookPaymentPayload) =>
      api.post<BookPaymentResult>('/calendar/payments/book', payload),
    onSuccess: () => queryClient.invalidateQueries(),
  })
}

/** Досрочно запустить автозачисление наступивших выплат. */
export function useAutoPostPayments() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () =>
      api.post<{ posted: number; items: unknown[] }>('/calendar/payments/auto-post'),
    onSuccess: () => queryClient.invalidateQueries(),
  })
}

export function usePaymentsHistory() {
  const { selected, scope } = useScope()
  return useQuery({
    queryKey: ['payments-history', selected],
    queryFn: () =>
      api.get<{
        items: {
          id: number
          date: string
          type: string
          type_label: string
          secid: string | null
          name: string | null
          amount: number
          tax: number
          net: number
          currency: string
        }[]
        total: number
        by_month: { month: string; amount: number }[]
      }>(`/calendar/history${scope}`),
  })
}

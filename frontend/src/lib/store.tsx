import { useQuery } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { api, type Portfolio } from './api'

interface SelectionValue {
  portfolios: Portfolio[]
  isLoading: boolean
  selected: number[]
  /** Пустой список = «все портфели». */
  setSelected: (ids: number[]) => void
  toggle: (id: number) => void
  period: string
  setPeriod: (period: string) => void
}

const SelectionContext = createContext<SelectionValue | null>(null)
const KEY_SELECTED = 'im-portfolios'
const KEY_PERIOD = 'im-period'

export function SelectionProvider({ children }: { children: ReactNode }) {
  const { data: portfolios = [], isLoading } = useQuery({
    queryKey: ['portfolios'],
    queryFn: () => api.get<Portfolio[]>('/portfolios'),
  })

  const [selected, setSelectedState] = useState<number[]>(() => {
    try {
      return JSON.parse(localStorage.getItem(KEY_SELECTED) ?? '[]')
    } catch {
      return []
    }
  })
  const [period, setPeriodState] = useState<string>(
    () => localStorage.getItem(KEY_PERIOD) ?? 'ALL',
  )

  useEffect(() => {
    localStorage.setItem(KEY_SELECTED, JSON.stringify(selected))
  }, [selected])
  useEffect(() => {
    localStorage.setItem(KEY_PERIOD, period)
  }, [period])

  // портфель могли удалить — чистим выбор
  useEffect(() => {
    if (!portfolios.length || !selected.length) return
    const alive = new Set(portfolios.map((p) => p.id))
    const filtered = selected.filter((id) => alive.has(id))
    if (filtered.length !== selected.length) setSelectedState(filtered)
  }, [portfolios, selected])

  const toggle = useCallback((id: number) => {
    setSelectedState((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    )
  }, [])

  const value = useMemo(
    () => ({
      portfolios,
      isLoading,
      selected,
      setSelected: setSelectedState,
      toggle,
      period,
      setPeriod: setPeriodState,
    }),
    [portfolios, isLoading, selected, toggle, period],
  )

  return <SelectionContext.Provider value={value}>{children}</SelectionContext.Provider>
}

export function useSelection(): SelectionValue {
  const ctx = useContext(SelectionContext)
  if (!ctx) throw new Error('useSelection вне SelectionProvider')
  return ctx
}

import { useEffect, useState } from 'react'

import { useTheme } from './theme'

/**
 * Токены графиков читаются из CSS-переменных, поэтому светлая и тёмная схемы
 * задаются в одном месте (index.css) и не расходятся с интерфейсом.
 * Обе схемы прогнаны через валидатор палитры: полосы светлоты, порог цветности,
 * различимость соседних слотов при дальтонизме и контраст к подложке.
 */
export interface VizTokens {
  series: string[]
  surface: string
  grid: string
  axis: string
  ink: string
  ink2: string
  muted: string
  good: string
  bad: string
  warning: string
  seq: string[]
}

const VARS = [
  '--series-1',
  '--series-2',
  '--series-3',
  '--series-4',
  '--series-5',
  '--series-6',
  '--series-7',
  '--series-8',
]

function read(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

function readTokens(): VizTokens {
  return {
    series: VARS.map((v, i) => read(v, ['#2a78d6', '#eb6834', '#1baf7a', '#eda100'][i] ?? '#2a78d6')),
    surface: read('--surface-1', '#fcfcfb'),
    grid: read('--gridline', '#e1e0d9'),
    axis: read('--axis', '#c3c2b7'),
    ink: read('--text-primary', '#0b0b0b'),
    ink2: read('--text-secondary', '#52514e'),
    muted: read('--text-muted', '#898781'),
    good: read('--good-text', '#006300'),
    bad: read('--critical', '#d03b3b'),
    warning: read('--warning', '#fab219'),
    seq: [read('--seq-100', '#cde2fb'), read('--seq-300', '#6da7ec'), read('--seq-450', '#2a78d6'), read('--seq-600', '#184f95')],
  }
}

export function useViz(): VizTokens {
  const { resolved } = useTheme()
  const [tokens, setTokens] = useState<VizTokens>(readTokens)
  useEffect(() => {
    // ждём применения атрибута data-theme, затем перечитываем переменные
    const id = requestAnimationFrame(() => setTokens(readTokens()))
    return () => cancelAnimationFrame(id)
  }, [resolved])
  return tokens
}

/** Цвет серии по индексу. Слоты назначаются по порядку и никогда не зацикливаются. */
export function seriesColor(tokens: VizTokens, index: number): string {
  return tokens.series[Math.min(index, tokens.series.length - 1)]
}

/** Общие настройки осей: сетка и подписи должны отступать на второй план. */
export function axisProps(tokens: VizTokens) {
  return {
    stroke: tokens.axis,
    tick: { fill: tokens.muted, fontSize: 11 },
    tickLine: false,
    axisLine: { stroke: tokens.axis },
  }
}

export const CHART_MARGIN = { top: 8, right: 12, bottom: 4, left: 4 }

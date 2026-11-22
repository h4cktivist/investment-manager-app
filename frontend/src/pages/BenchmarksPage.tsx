import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { ReturnComparisonChart } from '../components/charts'
import MonteCarloPanel from '../components/MonteCarloPanel'
import { Card, ErrorBox, Money, SegmentedControl, Spinner, StatTile, cx } from '../components/ui'
import { api, qs, type Performance } from '../lib/api'
import { fmtDate, pctRaw } from '../lib/format'
import { useSelection } from '../lib/store'
import { seriesColor, useViz } from '../lib/viz'

interface BenchmarkInfo {
  id: number
  code: string
  name: string
  kind: string
  kind_label: string
  is_default: boolean
  last_date: string | null
  last_value: number | null
}

interface CompareResponse {
  period: string
  portfolio_return_pct: number | null
  benchmarks: {
    code: string
    name: string
    kind: string
    kind_label: string
    return_pct: number | null
    excess_pct: number | null
  }[]
  chart: Performance
  beta: number | null
  alpha: number | null
  correlation: number | null
}

const PERIODS = [
  { value: '3M', label: '3М' },
  { value: '6M', label: '6М' },
  { value: 'YTD', label: 'С начала года' },
  { value: '1Y', label: '1Г' },
  { value: '3Y', label: '3Г' },
  { value: 'ALL', label: 'Всё время' },
]

export default function BenchmarksPage() {
  const { selected, period, setPeriod } = useSelection()
  const viz = useViz()
  const [codes, setCodes] = useState<string[]>(['IMOEX', 'MCFTR', 'RGBITR', 'RUCBTRNS'])

  const list = useQuery({
    queryKey: ['benchmarks'],
    queryFn: () => api.get<BenchmarkInfo[]>('/benchmarks'),
  })

  const compare = useQuery({
    queryKey: ['benchmark-compare', selected, period, codes],
    queryFn: () =>
      api.get<CompareResponse>(
        `/benchmarks/compare${qs({ portfolio_id: selected, period, codes })}`,
      ),
    enabled: codes.length > 0,
  })

  const toggle = (code: string) =>
    setCodes((prev) =>
      prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code].slice(0, 4),
    )

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-ink">Сравнение с рынком</h1>
          <p className="text-2xs text-muted">
            Индексы полной доходности учитывают реинвестирование купонов и дивидендов — с ними
            сравнение честнее, чем с ценовым IMOEX
          </p>
        </div>
        <SegmentedControl value={period} onChange={setPeriod} options={PERIODS} />
      </div>

      <Card title="Какие индексы показывать" subtitle="До четырёх одновременно">
        {list.isLoading ? (
          <Spinner />
        ) : (
          <div className="flex flex-wrap gap-2">
            {(list.data ?? []).map((b) => {
              const active = codes.includes(b.code)
              const index = codes.indexOf(b.code)
              return (
                <button
                  key={b.code}
                  onClick={() => toggle(b.code)}
                  className={cx(
                    'flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-left text-xs transition',
                    active ? 'border-[var(--series-1)] bg-raised' : 'border-line hover:bg-raised',
                  )}
                >
                  <span
                    aria-hidden
                    className="h-2 w-2 shrink-0 rounded-sm"
                    style={{
                      background: active ? seriesColor(viz, index + 1) : 'var(--gridline)',
                    }}
                  />
                  <span>
                    <span className="block font-medium text-ink">{b.code}</span>
                    <span className="block text-2xs text-muted">{b.kind_label}</span>
                  </span>
                </button>
              )
            })}
          </div>
        )}
      </Card>

      {compare.error && <ErrorBox error={compare.error} />}
      {compare.isLoading ? (
        <Spinner />
      ) : compare.data ? (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <StatTile
              large
              label="Доходность портфеля"
              value={pctRaw(compare.data.portfolio_return_pct)}
              hint="TWR за выбранный период"
            />
            {compare.data.benchmarks.slice(0, 3).map((b) => (
              <StatTile
                key={b.code}
                label={b.code}
                value={pctRaw(b.return_pct)}
                delta={
                  b.excess_pct !== null ? (
                    <>
                      <Money value={b.excess_pct} render={(v) => `${v.toFixed(2)} п.п.`} /> разница
                    </>
                  ) : undefined
                }
                hint={b.kind_label}
              />
            ))}
          </div>

          <Card
            title="Накопленная доходность"
            subtitle="Все ряды приведены к нулю на начало периода"
          >
            <ReturnComparisonChart
              points={compare.data.chart.points}
              benchmarks={compare.data.chart.benchmarks}
              height={360}
            />
          </Card>

          <MonteCarloPanel period={period} />

          <div className="grid gap-4 lg:grid-cols-3">
            <Card title="Связь с рынком" className="lg:col-span-1">
              <dl className="space-y-2 text-xs">
                <Row term="Бета" value={fixed(compare.data.beta)} hint="чувствительность к движению индекса" />
                <Row term="Альфа" value={pctRaw((compare.data.alpha ?? 0) * 100)} hint="годовая, сверх объяснённой бетой" />
                <Row
                  term="Корреляция"
                  value={fixed(compare.data.correlation)}
                  hint="1 — движется вместе с индексом, 0 — независимо"
                />
              </dl>
            </Card>

            <Card title="Итоги по индексам" className="lg:col-span-2" bodyClassName="p-0">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[520px] text-xs">
                  <thead>
                    <tr className="border-b border-line text-left text-2xs uppercase text-muted">
                      <th className="px-4 py-2 font-medium">Индекс</th>
                      <th className="px-4 py-2 font-medium">Класс</th>
                      <th className="px-4 py-2 text-right font-medium">За период</th>
                      <th className="px-4 py-2 text-right font-medium">Портфель против него</th>
                    </tr>
                  </thead>
                  <tbody>
                    {compare.data.benchmarks.map((b) => (
                      <tr key={b.code} className="border-b border-line/60">
                        <td className="px-4 py-2">
                          <div className="font-medium text-ink">{b.code}</div>
                          <div className="text-2xs text-muted">{b.name}</div>
                        </td>
                        <td className="px-4 py-2 text-ink-2">{b.kind_label}</td>
                        <td className="tnum px-4 py-2 text-right text-ink-2">{pctRaw(b.return_pct)}</td>
                        <td className="px-4 py-2 text-right">
                          <Money value={b.excess_pct} render={(v) => `${v.toFixed(2)} п.п.`} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>

          {list.data?.some((b) => b.last_date) && (
            <p className="text-2xs text-muted">
              Данные индексов актуальны на{' '}
              {fmtDate(
                list.data
                  .map((b) => b.last_date)
                  .filter(Boolean)
                  .sort()
                  .at(-1) as string,
              )}
              .
            </p>
          )}
        </>
      ) : null}
    </div>
  )
}

function Row({ term, value, hint }: { term: string; value: string; hint: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-line/60 pb-2 last:border-0">
      <div>
        <dt className="font-medium text-ink">{term}</dt>
        <dd className="text-2xs text-muted">{hint}</dd>
      </div>
      <span className="tnum shrink-0 text-sm font-semibold text-ink">{value}</span>
    </div>
  )
}

function fixed(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return value.toFixed(digits)
}

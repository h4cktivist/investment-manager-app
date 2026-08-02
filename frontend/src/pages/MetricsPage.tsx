import { useState } from 'react'

import { Card, ErrorBox, Spinner, StatTile, cx } from '../components/ui'
import { useAllPeriodMetrics, useMetrics } from '../hooks/useApi'
import type { Metrics } from '../lib/api'
import { daysWord, fmtDate, pct, pctRaw } from '../lib/format'
import { useSelection } from '../lib/store'

const PERIOD_LABELS: Record<string, string> = {
  '1M': '1 месяц',
  '3M': '3 месяца',
  '6M': '6 месяцев',
  YTD: 'С начала года',
  '1Y': '1 год',
  '3Y': '3 года',
  ALL: 'Всё время',
}

export default function MetricsPage() {
  const { period } = useSelection()
  const [riskFree, setRiskFree] = useState<string>('')
  const rf = riskFree === '' ? undefined : Number(riskFree) / 100
  const { data, isLoading, error } = useMetrics(period, 'IMOEX', rf)
  const allPeriods = useAllPeriodMetrics('IMOEX')

  if (isLoading) return <Spinner />
  if (error) return <ErrorBox error={error} />
  if (!data) return null

  if (!data.available) {
    return (
      <Card title="Метрики недоступны">
        <p className="text-sm text-ink-2">{data.reason ?? 'Недостаточно данных'}</p>
      </Card>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-ink">Метрики доходности и риска</h1>
          <p className="text-2xs text-muted">
            {fmtDate(data.start)} → {fmtDate(data.end)} · {daysWord(data.days ?? 0)} · период{' '}
            {PERIOD_LABELS[period] ?? period}
          </p>
        </div>
        <label className="text-2xs text-muted">
          <span className="mb-1 block uppercase tracking-wide">Безрисковая ставка, % годовых</span>
          <input
            type="number"
            step="0.5"
            value={riskFree}
            onChange={(e) => setRiskFree(e.target.value)}
            placeholder={String(((data.risk_free_rate ?? 0) * 100).toFixed(1))}
            className="w-40 rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-ink placeholder:text-muted focus:border-[var(--series-1)] focus:outline-none"
          />
        </label>
      </div>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-ink">Доходность</h2>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <StatTile
            large
            label="TWR за период"
            value={pct(data.twr)}
            hint="доходность, очищенная от вводов и выводов денег"
          />
          <StatTile large label="TWR годовых" value={pct(data.twr_annual)} hint="приведено к году" />
          <StatTile
            large
            label="XIRR"
            value={pct(data.xirr)}
            hint="денежно-взвешенная: учитывает, когда и сколько вы вносили"
          />
          <StatTile
            large
            label={`Опережение ${data.benchmark_code}`}
            value={pct(data.excess_return)}
            hint={`индекс за период: ${pct(data.benchmark_return)}`}
          />
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-ink">Риск</h2>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <StatTile
            label="Волатильность"
            value={pct(data.volatility)}
            hint="годовое стандартное отклонение дневных доходностей"
          />
          <StatTile
            label="Максимальная просадка"
            value={<span className="text-[var(--critical)]">{pct(data.max_drawdown)}</span>}
            hint={
              data.max_drawdown_start
                ? `${fmtDate(data.max_drawdown_start)} → ${fmtDate(data.max_drawdown_end)}`
                : undefined
            }
          />
          <StatTile
            label="Бета к индексу"
            value={fixed(data.beta)}
            hint={
              data.beta !== null && data.beta !== undefined
                ? data.beta < 0.5
                  ? 'портфель слабо повторяет рынок'
                  : data.beta > 1
                    ? 'колеблется сильнее рынка'
                    : 'колеблется слабее рынка'
                : undefined
            }
          />
          <StatTile label="Корреляция с индексом" value={fixed(data.correlation)} />
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-ink">Доходность с поправкой на риск</h2>
        <div className="grid gap-3 sm:grid-cols-3">
          <StatTile
            large
            label="Коэффициент Шарпа"
            value={fixed(data.sharpe)}
            hint={`(TWR годовых − ${pctRaw((data.risk_free_rate ?? 0) * 100, 1)}) ÷ волатильность`}
          />
          <StatTile
            large
            label="Коэффициент Сортино"
            value={fixed(data.sortino)}
            hint="в знаменателе только просадочная волатильность"
          />
          <StatTile large label="Альфа" value={pct(data.alpha)} hint="доходность сверх объяснённой бетой" />
        </div>
        <p className="mt-2 text-2xs text-muted">
          Шарп и Сортино считаются относительно безрисковой ставки{' '}
          {pctRaw((data.risk_free_rate ?? 0) * 100, 1)} годовых. При высокой ключевой ставке
          отрицательный Шарп означает лишь, что портфель отстал от депозита, — это нормально для
          периода падения рынка акций.
        </p>
      </section>

      <Card title="Сравнение по периодам" subtitle="Одни и те же метрики на разных горизонтах">
        {allPeriods.isLoading ? (
          <Spinner />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-xs">
              <thead>
                <tr className="border-b border-line text-left text-2xs uppercase text-muted">
                  <th className="py-2 pr-3 font-medium">Период</th>
                  <th className="py-2 pr-3 text-right font-medium">TWR</th>
                  <th className="py-2 pr-3 text-right font-medium">Годовых</th>
                  <th className="py-2 pr-3 text-right font-medium">XIRR</th>
                  <th className="py-2 pr-3 text-right font-medium">Волатильность</th>
                  <th className="py-2 pr-3 text-right font-medium">Просадка</th>
                  <th className="py-2 pr-3 text-right font-medium">Шарп</th>
                  <th className="py-2 pr-3 text-right font-medium">Сортино</th>
                  <th className="py-2 pr-3 text-right font-medium">Бета</th>
                  <th className="py-2 text-right font-medium">IMOEX</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(allPeriods.data ?? {}).map(([key, m]) => (
                  <MetricRow key={key} label={PERIOD_LABELS[key] ?? key} metrics={m} active={key === period} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}

function MetricRow({
  label,
  metrics,
  active,
}: {
  label: string
  metrics: Metrics
  active: boolean
}) {
  if (!metrics.available) {
    return (
      <tr className="border-b border-line/60">
        <td className="py-2 pr-3 text-ink-2">{label}</td>
        <td colSpan={9} className="py-2 text-muted">
          {metrics.reason}
        </td>
      </tr>
    )
  }
  return (
    <tr className={cx('border-b border-line/60', active && 'bg-raised')}>
      <td className="py-2 pr-3 font-medium text-ink">{label}</td>
      <td className="tnum py-2 pr-3 text-right text-ink">{pct(metrics.twr)}</td>
      <td className="tnum py-2 pr-3 text-right text-ink-2">{pct(metrics.twr_annual)}</td>
      <td className="tnum py-2 pr-3 text-right text-ink-2">{pct(metrics.xirr)}</td>
      <td className="tnum py-2 pr-3 text-right text-ink-2">{pct(metrics.volatility)}</td>
      <td className="tnum py-2 pr-3 text-right text-[var(--critical)]">{pct(metrics.max_drawdown)}</td>
      <td className="tnum py-2 pr-3 text-right text-ink-2">{fixed(metrics.sharpe)}</td>
      <td className="tnum py-2 pr-3 text-right text-ink-2">{fixed(metrics.sortino)}</td>
      <td className="tnum py-2 pr-3 text-right text-ink-2">{fixed(metrics.beta)}</td>
      <td className="tnum py-2 text-right text-ink-2">{pct(metrics.benchmark_return)}</td>
    </tr>
  )
}

function fixed(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return value.toFixed(digits)
}

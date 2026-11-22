import { useState } from 'react'

import { useMonteCarlo } from '../hooks/useApi'
import { fmtDate, pct, rub, rubShort } from '../lib/format'
import { MonteCarloChart } from './charts'
import { Card, ErrorBox, SegmentedControl, Spinner, StatTile } from './ui'

const HORIZONS = [
  { value: '3M', label: '3М' },
  { value: '6M', label: '6М' },
  { value: '1Y', label: '1Г' },
  { value: '3Y', label: '3Г' },
]

const HORIZON_LABELS: Record<string, string> = {
  '3M': 'через 3 месяца',
  '6M': 'через полгода',
  '1Y': 'через год',
  '3Y': 'через 3 года',
}

/**
 * Сценарный прогноз стоимости портфеля. Новые точки считает бэкенд,
 * здесь только выбор горизонта и визуализация распределения.
 */
export default function MonteCarloPanel({ period }: { period: string }) {
  const [horizon, setHorizon] = useState('1Y')
  const { data, isLoading, error } = useMonteCarlo(period, horizon)

  const horizonLabel = HORIZON_LABELS[horizon] ?? horizon

  return (
    <Card
      title="Симуляция Монте-Карло"
      subtitle="Диапазон правдоподобных исходов при сохранении текущей структуры портфеля"
      actions={<SegmentedControl options={HORIZONS} value={horizon} onChange={setHorizon} size="xs" />}
    >
      {isLoading ? (
        <Spinner label="Генерирую сценарии…" />
      ) : error ? (
        <ErrorBox error={error} />
      ) : !data?.available ? (
        <p className="text-sm text-ink-2">
          {data?.reason ?? 'Недостаточно данных для симуляции'}
        </p>
      ) : (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <StatTile
              label="Медианная стоимость"
              value={rub(data.final?.p50, 0)}
              delta={`${pct(data.median_return)} к текущей`}
              deltaTone={
                (data.median_return ?? 0) > 0
                  ? 'good'
                  : (data.median_return ?? 0) < 0
                    ? 'bad'
                    : 'neutral'
              }
              hint={horizonLabel}
            />
            <StatTile
              label="Вероятность убытка"
              value={pct(data.probability_of_loss)}
              hint={`потерять больше 10 %: ${pct(data.probability_of_loss_10)}`}
            />
            <StatTile
              label="VaR 95 % на горизонте"
              value={
                <span className="text-[var(--critical)]">{rub(data.var_95, 0)}</span>
              }
              hint={`${pct(data.var_95_pct)} от текущей стоимости · ES 95 %: ${rub(data.es_95, 0)}`}
            />
            <StatTile
              label="Диапазон 5–95 %"
              value={`${rubShort(data.final?.p5)} – ${rubShort(data.final?.p95)}`}
              hint={`от ${rub(data.final?.p5, 0)} до ${rub(data.final?.p95, 0)}`}
            />
          </div>

          <MonteCarloChart
            points={data.points ?? []}
            initialValue={data.initial_value ?? 0}
          />

          <p className="text-2xs text-muted">
            {data.simulations} сценариев на {data.horizon_days} торговых дней. Дневные
            доходности TWR выбираются случайно с возвращением из истории{' '}
            {fmtDate(data.start)} → {fmtDate(data.end)} ({data.observations} наблюдений),
            стартовая стоимость — {rub(data.initial_value, 0)}. Годовая волатильность на
            этом окне — {pct(data.annual_volatility)}. Метод сохраняет реальные «толстые
            хвосты» доходностей, но не учитывает будущие пополнения, выводы и изменение
            состава портфеля.
          </p>
        </div>
      )}
    </Card>
  )
}

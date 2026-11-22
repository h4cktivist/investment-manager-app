import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import type {
  AllocationSlice,
  BenchmarkSeries,
  MonteCarloPoint,
  PerfPoint,
} from '../lib/api'
import { axisMoney, fmtDate, fmtMonth, num, pctRaw, rub, rubShort } from '../lib/format'
import { CHART_MARGIN, axisProps, seriesColor, useViz } from '../lib/viz'
import { LegendRow, cx } from './ui'

// --------------------------------------------------------------- подсказка

function TooltipShell({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-md border border-line bg-surface px-3 py-2 shadow-lg">
      <div className="mb-1 text-2xs font-medium text-muted">{title}</div>
      <div className="space-y-0.5">{children}</div>
    </div>
  )
}

function TooltipRow({
  color,
  label,
  value,
}: {
  color?: string
  label: string
  value: string
}) {
  return (
    <div className="flex items-center gap-2 text-xs">
      {color && (
        <span
          aria-hidden
          className="inline-block h-2 w-2 shrink-0 rounded-sm"
          style={{ background: color }}
        />
      )}
      <span className="text-ink-2">{label}</span>
      <span className="tnum ml-auto pl-3 font-medium text-ink">{value}</span>
    </div>
  )
}

// ------------------------------------------------- стоимость портфеля

export function PortfolioValueChart({ points }: { points: PerfPoint[] }) {
  const viz = useViz()
  const data = useMemo(
    () => points.map((p) => ({ ...p, ts: new Date(p.date).getTime() })),
    [points],
  )
  if (!data.length) return <ChartEmpty />

  return (
    <>
      <ResponsiveContainer width="100%" height={260}>
        <AreaChart data={data} margin={CHART_MARGIN}>
          <defs>
            <linearGradient id="valueFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={seriesColor(viz, 0)} stopOpacity={0.28} />
              <stop offset="100%" stopColor={seriesColor(viz, 0)} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke={viz.grid} strokeDasharray="0" vertical={false} />
          <XAxis dataKey="date" tickFormatter={(v) => fmtDate(v).slice(3)} minTickGap={48} {...axisProps(viz)} />
          <YAxis tickFormatter={axisMoney} width={46} {...axisProps(viz)} />
          <Tooltip
            cursor={{ stroke: viz.axis, strokeWidth: 1 }}
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null
              const row = payload[0].payload as PerfPoint
              return (
                <TooltipShell title={fmtDate(String(label))}>
                  <TooltipRow color={seriesColor(viz, 0)} label="Стоимость" value={rub(row.total)} />
                  <TooltipRow color={viz.muted} label="Себестоимость позиций" value={rub(row.invested)} />
                  <TooltipRow label="Доходность" value={pctRaw(row.return_pct)} />
                  {row.cash > 1 && <TooltipRow label="Свободные деньги" value={rub(row.cash)} />}
                </TooltipShell>
              )
            }}
          />
          <Area
            type="monotone"
            dataKey="invested"
            stroke={viz.muted}
            strokeWidth={2}
            strokeDasharray="4 3"
            fill="none"
            dot={false}
            activeDot={false}
            isAnimationActive={false}
          />
          <Area
            type="monotone"
            dataKey="total"
            stroke={seriesColor(viz, 0)}
            strokeWidth={2}
            fill="url(#valueFill)"
            dot={false}
            activeDot={{ r: 4, strokeWidth: 2, stroke: viz.surface }}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
      <LegendRow
        items={[
          { color: seriesColor(viz, 0), label: 'Стоимость портфеля', value: rub(data.at(-1)!.total, 0) },
          {
            color: viz.muted,
            label: 'Себестоимость позиций',
            value: rub(data.at(-1)!.invested, 0),
          },
        ]}
      />
    </>
  )
}

// --------------------------------------- доходность против бенчмарков

export function ReturnComparisonChart({
  points,
  benchmarks,
  height = 300,
}: {
  points: PerfPoint[]
  benchmarks: BenchmarkSeries[]
  height?: number
}) {
  const viz = useViz()
  const [showTable, setShowTable] = useState(false)

  const data = useMemo(() => {
    const byDate = new Map<string, Record<string, number | string>>()
    for (const p of points) byDate.set(p.date, { date: p.date, portfolio: p.return_pct })
    for (const bench of benchmarks) {
      for (const p of bench.points) {
        const row = byDate.get(p.date)
        if (row) row[bench.code] = p.return_pct
      }
    }
    return [...byDate.values()]
  }, [points, benchmarks])

  if (!data.length) return <ChartEmpty />

  const series = [
    { key: 'portfolio', name: 'Мой портфель', color: seriesColor(viz, 0), last: points.at(-1)?.return_pct ?? 0 },
    ...benchmarks.map((b, i) => ({
      key: b.code,
      name: `${b.code} — ${shortBenchmark(b.name)}`,
      color: seriesColor(viz, i + 1),
      last: b.total_return_pct ?? 0,
    })),
  ]

  return (
    <>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ ...CHART_MARGIN, right: 16 }}>
          <CartesianGrid stroke={viz.grid} vertical={false} />
          <XAxis dataKey="date" tickFormatter={(v) => fmtDate(v).slice(3)} minTickGap={48} {...axisProps(viz)} />
          <YAxis tickFormatter={(v) => `${Math.round(v)}%`} width={46} {...axisProps(viz)} />
          <ReferenceLine y={0} stroke={viz.axis} strokeWidth={1} />
          <Tooltip
            cursor={{ stroke: viz.axis, strokeWidth: 1 }}
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null
              const sorted = [...payload].sort((a, b) => Number(b.value) - Number(a.value))
              return (
                <TooltipShell title={fmtDate(String(label))}>
                  {sorted.map((item) => (
                    <TooltipRow
                      key={String(item.dataKey)}
                      color={item.color}
                      label={series.find((s) => s.key === item.dataKey)?.name.split(' — ')[0] ?? String(item.dataKey)}
                      value={pctRaw(Number(item.value))}
                    />
                  ))}
                </TooltipShell>
              )
            }}
          />
          {series.map((s) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              stroke={s.color}
              strokeWidth={s.key === 'portfolio' ? 2.5 : 2}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 2, stroke: viz.surface }}
              isAnimationActive={false}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>

      <LegendRow items={series.map((s) => ({ color: s.color, label: s.name, value: pctRaw(s.last) }))} />

      <button
        onClick={() => setShowTable((v) => !v)}
        className="mt-2 text-2xs text-muted underline underline-offset-2 hover:text-ink"
      >
        {showTable ? 'Скрыть таблицу' : 'Показать таблицей'}
      </button>
      {showTable && (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[420px] text-xs">
            <thead>
              <tr className="border-b border-line text-left text-2xs uppercase text-muted">
                <th className="py-1.5 pr-3 font-medium">Инструмент</th>
                <th className="py-1.5 pr-3 text-right font-medium">Доходность за период</th>
              </tr>
            </thead>
            <tbody>
              {series.map((s) => (
                <tr key={s.key} className="border-b border-line/60">
                  <td className="py-1.5 pr-3 text-ink-2">{s.name}</td>
                  <td className="tnum py-1.5 pr-3 text-right font-medium text-ink">{pctRaw(s.last)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

function shortBenchmark(name: string): string {
  return name.replace('Индекс ', '').replace(', полной доходности', ' (полн. дох.)')
}

// --------------------------------------------- веер сценариев Монте-Карло

export function MonteCarloChart({
  points,
  initialValue,
  height = 360,
}: {
  points: MonteCarloPoint[]
  initialValue: number
  height?: number
}) {
  const viz = useViz()
  const data = useMemo(
    () =>
      points.map((point) => ({
        ...point,
        // полосы строятся стопкой: нижний квантиль + ширина полосы
        outer: Math.max(point.p95 - point.p5, 0),
        inner: Math.max(point.p75 - point.p25, 0),
      })),
    [points],
  )
  if (!data.length) return <ChartEmpty />

  const median = seriesColor(viz, 0)
  const band = seriesColor(viz, 4)

  return (
    <>
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={data} margin={{ ...CHART_MARGIN, right: 16 }}>
          <CartesianGrid stroke={viz.grid} vertical={false} />
          <XAxis
            dataKey="date"
            tickFormatter={(value) => fmtDate(String(value)).slice(3)}
            minTickGap={48}
            {...axisProps(viz)}
          />
          <YAxis
            tickFormatter={axisMoney}
            width={52}
            domain={['auto', 'auto']}
            {...axisProps(viz)}
          />
          <Tooltip
            cursor={{ stroke: viz.axis, strokeWidth: 1 }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null
              const row = payload[0].payload as MonteCarloPoint
              return (
                <TooltipShell title={fmtDate(row.date)}>
                  <TooltipRow
                    color={median}
                    label="Медиана"
                    value={`${rub(row.p50, 0)} · ${pctRaw((row.p50 / initialValue - 1) * 100)}`}
                  />
                  <TooltipRow
                    color={band}
                    label="25–75% сценариев"
                    value={`${rub(row.p25, 0)} – ${rub(row.p75, 0)}`}
                  />
                  <TooltipRow
                    color={band}
                    label="5–95% сценариев"
                    value={`${rub(row.p5, 0)} – ${rub(row.p95, 0)}`}
                  />
                </TooltipShell>
              )
            }}
          />
          <Area
            dataKey="p5"
            stackId="outer"
            stroke="none"
            fill="transparent"
            isAnimationActive={false}
          />
          <Area
            dataKey="outer"
            stackId="outer"
            stroke="none"
            fill={band}
            fillOpacity={0.1}
            isAnimationActive={false}
          />
          <Area
            dataKey="p25"
            stackId="inner"
            stroke="none"
            fill="transparent"
            isAnimationActive={false}
          />
          <Area
            dataKey="inner"
            stackId="inner"
            stroke="none"
            fill={band}
            fillOpacity={0.2}
            isAnimationActive={false}
          />
          <ReferenceLine
            y={initialValue}
            stroke={viz.muted}
            strokeDasharray="4 3"
            strokeWidth={1}
            ifOverflow="extendDomain"
          />
          <Line
            type="monotone"
            dataKey="p50"
            stroke={median}
            strokeWidth={2.5}
            dot={false}
            activeDot={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>

      <LegendRow
        items={[
          { color: median, label: 'Медиана' },
          {
            color: `color-mix(in srgb, ${band} 35%, transparent)`,
            label: '50% сценариев (25–75%)',
          },
          {
            color: `color-mix(in srgb, ${band} 18%, transparent)`,
            label: '90% сценариев (5–95%)',
          },
          { color: viz.muted, label: 'Стартовая стоимость' },
        ]}
      />
    </>
  )
}

// -------------------------------------------------------- распределение

export function AllocationBars({
  slices,
  total,
  maxRows = 12,
}: {
  slices: AllocationSlice[]
  total: number
  maxRows?: number
}) {
  const viz = useViz()
  if (!slices.length) return <ChartEmpty />

  const head = slices.slice(0, maxRows)
  const tail = slices.slice(maxRows)
  const rows = tail.length
    ? [
        ...head,
        {
          key: '__other',
          label: `Прочее (${tail.length})`,
          value: tail.reduce((sum, s) => sum + s.value, 0),
          weight: tail.reduce((sum, s) => sum + s.weight, 0),
        },
      ]
    : head

  const max = Math.max(...rows.map((r) => r.value), 1)

  return (
    <div>
      {/* доли целого одной полосой: зазор в 2px рисуется цветом подложки */}
      <div className="mb-4 flex h-2.5 w-full overflow-hidden rounded-full">
        {rows.map((row, i) => (
          <div
            key={row.key}
            title={`${row.label}: ${pctRaw(row.weight * 100, 1)}`}
            style={{
              width: `${Math.max(row.weight * 100, 0.4)}%`,
              background: seriesColor(viz, i),
              borderRight: i < rows.length - 1 ? `2px solid ${viz.surface}` : undefined,
            }}
          />
        ))}
      </div>

      <ul className="space-y-1.5">
        {rows.map((row, i) => (
          <li key={row.key} className="grid grid-cols-[auto_1fr_auto_auto] items-center gap-2">
            <span
              aria-hidden
              className="h-2 w-2 shrink-0 rounded-sm"
              style={{ background: seriesColor(viz, i) }}
            />
            <span className="truncate text-xs text-ink-2" title={row.label}>
              {row.label}
            </span>
            <span className="tnum w-14 text-right text-xs font-medium text-ink">
              {pctRaw(row.weight * 100, 1)}
            </span>
            <span className="tnum w-24 text-right text-xs text-ink-2">{rubShort(row.value)}</span>
          </li>
        ))}
      </ul>

      <div className="mt-3 flex items-center justify-between border-t border-line pt-2 text-xs">
        <span className="text-muted">Итого</span>
        <span className="tnum font-semibold text-ink">{rub(total, 0)}</span>
      </div>
      <span className="sr-only">Максимальная доля: {rubShort(max)}</span>
    </div>
  )
}

// --------------------------------------------------------- выплаты по месяцам

export function MonthlyBars({
  data,
  color,
  height = 200,
  emptyLabel = 'Нет данных',
  forecastFrom,
}: {
  data: { month: string; amount: number; count?: number }[]
  color?: string
  height?: number
  emptyLabel?: string
  forecastFrom?: string
}) {
  const viz = useViz()
  if (!data.length) return <ChartEmpty label={emptyLabel} />

  const fill = color ?? seriesColor(viz, 0)
  const forecastFill = seriesColor(viz, 3)
  const hasForecast = Boolean(forecastFrom) && data.some((d) => d.month >= forecastFrom!)

  return (
    <>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} margin={CHART_MARGIN} barCategoryGap="18%">
          <CartesianGrid stroke={viz.grid} vertical={false} />
          <XAxis dataKey="month" tickFormatter={fmtMonth} minTickGap={12} {...axisProps(viz)} />
          <YAxis tickFormatter={axisMoney} width={46} {...axisProps(viz)} />
          <Tooltip
            cursor={{ fill: viz.grid, fillOpacity: 0.4 }}
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null
              const row = payload[0].payload as { month: string; amount: number; count?: number }
              const isForecast = Boolean(forecastFrom) && row.month >= forecastFrom!
              return (
                <TooltipShell title={fmtMonth(String(label))}>
                  <TooltipRow
                    color={isForecast ? forecastFill : fill}
                    label={isForecast ? 'Ожидается' : 'Получено'}
                    value={rub(row.amount)}
                  />
                  {row.count !== undefined && (
                    <TooltipRow label="Выплат" value={num(row.count, 0)} />
                  )}
                </TooltipShell>
              )
            }}
          />
          <Bar dataKey="amount" radius={[4, 4, 0, 0]} isAnimationActive={false}>
            {data.map((row) => (
              <Cell
                key={row.month}
                fill={forecastFrom && row.month >= forecastFrom ? forecastFill : fill}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      {hasForecast && (
        <LegendRow
          items={[
            { color: fill, label: 'Получено' },
            { color: forecastFill, label: 'Ожидается' },
          ]}
        />
      )}
    </>
  )
}

// ------------------------------------------------------------ вспомогательное

export function ChartEmpty({ label = 'Недостаточно данных для графика' }: { label?: string }) {
  return (
    <div className="flex h-[200px] items-center justify-center text-xs text-muted">{label}</div>
  )
}

export function Sparkbar({ value, max, color }: { value: number; max: number; color?: string }) {
  const viz = useViz()
  const width = max > 0 ? Math.max((Math.abs(value) / max) * 100, 1) : 0
  return (
    <span className="inline-flex h-1.5 w-full min-w-[40px] overflow-hidden rounded-sm bg-[var(--gridline)]">
      <span
        className={cx('h-full rounded-sm')}
        style={{ width: `${width}%`, background: color ?? seriesColor(viz, 0) }}
      />
    </span>
  )
}

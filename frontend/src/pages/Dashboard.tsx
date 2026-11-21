import { Link } from 'react-router-dom'

import {
  AllocationBars,
  MonthlyBars,
  PortfolioValueChart,
  ReturnComparisonChart,
} from '../components/charts'
import PassiveIncomeCard from '../components/PassiveIncome'
import { Badge, Card, ErrorBox, Money, SegmentedControl, Spinner, StatTile, toneOf } from '../components/ui'
import { useDashboard } from '../hooks/useApi'
import { PAYMENT_LABELS, daysWord, fmtDate, fmtMonth, num, pct, pctRaw, rub } from '../lib/format'
import { useSelection } from '../lib/store'

const PERIODS = [
  { value: '1M', label: '1М' },
  { value: '3M', label: '3М' },
  { value: '6M', label: '6М' },
  { value: 'YTD', label: 'С начала года' },
  { value: '1Y', label: '1Г' },
  { value: '3Y', label: '3Г' },
  { value: 'ALL', label: 'Всё время' },
]

export default function Dashboard() {
  const { period, setPeriod } = useSelection()
  const { data, isLoading, error } = useDashboard()

  if (isLoading) return <Spinner label="Считаю портфель…" />
  if (error) return <ErrorBox error={error} />
  if (!data) return null

  const {
    summary,
    allocation,
    performance,
    metrics,
    passive_income,
    income_months,
    upcoming_payments,
    macro,
  } = data
  const hasData = summary.positions_count > 0 || summary.total_value !== 0

  if (!hasData) {
    return (
      <Card title="Портфель пуст">
        <p className="text-sm text-ink-2">
          Загрузите историю сделок на странице{' '}
          <Link to="/import" className="text-[var(--series-1)] underline underline-offset-2">
            «Импорт»
          </Link>{' '}
          или добавьте сделку вручную на странице «Сделки».
        </p>
      </Card>
    )
  }

  const currentMonth = new Date().toISOString().slice(0, 7)
  const upcomingSoon = upcoming_payments.slice(0, 7)

  const gainers = data.positions
    .filter((p) => (p.day_change ?? 0) > 0)
    .sort((a, b) => (b.day_change ?? 0) - (a.day_change ?? 0))
    .slice(0, 5)
  const losers = data.positions
    .filter((p) => (p.day_change ?? 0) < 0)
    .sort((a, b) => (a.day_change ?? 0) - (b.day_change ?? 0))
    .slice(0, 5)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold text-ink">Обзор портфеля</h1>
        <SegmentedControl options={PERIODS} value={period} onChange={setPeriod} />
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          large
          label="Стоимость портфеля"
          value={rub(summary.total_value, 0)}
          delta={
            <>
              <Money value={summary.day_change} render={(v) => rub(v, 0)} /> за день
            </>
          }
          deltaTone={toneOf(summary.day_change)}
          hint={`${summary.positions_count} позиций · свободно ${rub(summary.cash, 0)}`}
        />
        <StatTile
          large
          label="Прибыль всего"
          value={<Money value={summary.total_pnl} render={(v) => rub(v, 0)} />}
          delta={pct(summary.total_pnl_pct)}
          deltaTone={toneOf(summary.total_pnl)}
          hint={`Вложено ${rub(summary.net_invested, 0)}`}
        />
        <StatTile
          large
          label="Доходность годовых (XIRR)"
          value={pct(metrics.xirr)}
          delta={`TWR ${pct(metrics.twr)} за период`}
          deltaTone={toneOf(metrics.xirr)}
          hint={
            metrics.benchmark_code
              ? `${metrics.benchmark_code}: ${pct(metrics.benchmark_return)}`
              : undefined
          }
        />
        <StatTile
          large
          label="Пассивный доход за 12 мес."
          value={rub(passive_income.total, 0)}
          delta={`${rub(passive_income.per_month, 0)} в месяц · ${pct(
            passive_income.yield_on_market,
          )} к стоимости`}
          hint={`Получено за всё время: ${rub(summary.income + summary.amortization, 0)}`}
        />
      </div>
    
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <StatTile label="Волатильность" value={pct(metrics.volatility)} hint="годовая, по торговым дням" />
        <StatTile
          label="Макс. просадка"
          value={<span className="text-[var(--critical)]">{pct(metrics.max_drawdown)}</span>}
          hint={
            metrics.max_drawdown_start
              ? `${fmtDate(metrics.max_drawdown_start)} → ${fmtDate(metrics.max_drawdown_end)}`
              : undefined
          }
        />
        <StatTile
          label="Коэф. Шарпа"
          value={metrics.sharpe !== null && metrics.sharpe !== undefined ? metrics.sharpe.toFixed(2) : '—'}
          hint={`безриск. ставка ${pctRaw((data.risk_free_rate ?? 0) * 100, 1)}`}
        />
        <StatTile
          label="Коэф. Сортино"
          value={metrics.sortino !== null && metrics.sortino !== undefined ? metrics.sortino.toFixed(2) : '—'}
          hint="учитывает только падения"
        />
        <StatTile
          label="Бета к IMOEX"
          value={metrics.beta !== null && metrics.beta !== undefined ? metrics.beta.toFixed(2) : '—'}
          hint={
            metrics.correlation !== null && metrics.correlation !== undefined
              ? `корреляция ${metrics.correlation.toFixed(2)}`
              : undefined
          }
        />
        <StatTile
          label="XIRR"
          value={pct(metrics.xirr)}
          hint="денежно-взвешенная годовая"
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card
          className="xl:col-span-2"
          title="Стоимость портфеля"
          subtitle="Рыночная оценка позиций и денег против суммы вложений"
        >
          <PortfolioValueChart points={performance.points} />
        </Card>

        <Card title="Структура портфеля" subtitle="Доли по классам активов">
          <AllocationBars slices={allocation.by_class} total={allocation.total} />
        </Card>
      </div>

      <Card
        title="Лидеры и аутсайдеры дня"
        subtitle="По изменению стоимости позиции за день, ₽"
      >
        {gainers.length === 0 && losers.length === 0 ? (
          <p className="py-6 text-center text-xs text-muted">
            Нет данных об изменении стоимости за день
          </p>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="rounded-card border border-line p-3">
              <h3 className="text-xs font-semibold text-[var(--good-text)] mb-2">
                Выросли
              </h3>
              <ul className="space-y-2">
                {gainers.map((p) => (
                  <li key={p.asset_id} className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <div className="text-xs font-medium text-ink truncate">
                        {p.name}
                      </div>
                      <div className="text-2xs text-muted truncate">
                        {p.secid}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="tnum text-xs font-semibold text-[var(--good-text)]">
                        +{rub(p.day_change, 0)}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
            <div className="rounded-card border border-line p-3">
              <h3 className="text-xs font-semibold text-[var(--critical)] mb-2">
                Упали
              </h3>
              <ul className="space-y-2">
                {losers.map((p) => (
                  <li key={p.asset_id} className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <div className="text-xs font-medium text-ink truncate">
                        {p.name}
                      </div>
                      <div className="text-2xs text-muted truncate">
                        {p.secid}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="tnum text-xs font-semibold text-[var(--critical)]">
                        {rub(p.day_change, 0)}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </Card>

      <Card
        title="Доходность против индексов Мосбиржи"
        subtitle="Накопленная доходность портфеля очищена от вводов и выводов средств (TWR)"
      >
        <ReturnComparisonChart points={performance.points} benchmarks={performance.benchmarks} />
      </Card>

      <PassiveIncomeCard data={passive_income} />

      <div className="grid gap-4 xl:grid-cols-3">
        <Card
          className="xl:col-span-2"
          title="Полученные выплаты по месяцам"
          subtitle="Дивиденды, купоны и амортизация, зачисленные на счёт"
        >
          <MonthlyBars
            data={income_months}
            forecastFrom={currentMonth}
            emptyLabel="Выплат пока не было"
          />
        </Card>

        <Card
          title="Ближайшие выплаты"
          subtitle="По текущим позициям"
          actions={
            <Link to="/calendar" className="text-2xs text-[var(--series-1)] hover:underline">
              весь календарь →
            </Link>
          }
        >
          {upcomingSoon.length === 0 ? (
            <p className="py-6 text-center text-xs text-muted">
              В ближайшие 90 дней выплат не ожидается
            </p>
          ) : (
            <ul className="space-y-2">
              {upcomingSoon.map((item) => {
                const days = Math.round(
                  (new Date(item.payment_date).getTime() - Date.now()) / 86_400_000,
                )
                return (
                  <li
                    key={`${item.asset_id}-${item.kind}-${item.payment_date}`}
                    className="flex items-center justify-between gap-3 border-b border-line/60 pb-2 last:border-0 last:pb-0"
                  >
                    <div className="min-w-0">
                      <div className="truncate text-xs font-medium text-ink">{item.name}</div>
                      <div className="text-2xs text-muted">
                        {fmtDate(item.payment_date)} · {days <= 0 ? 'сегодня' : `через ${daysWord(days)}`}
                        {item.is_forecast && ' · прогноз'}
                      </div>
                    </div>
                    <div className="shrink-0 text-right">
                      <div className="tnum text-xs font-semibold text-ink">{rub(item.amount)}</div>
                      <Badge tone={item.kind === 'DIVIDEND' ? 'info' : 'neutral'}>
                        {PAYMENT_LABELS[item.kind] ?? item.kind}
                      </Badge>
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
        </Card>
      </div>

      {macro?.available && (
        <Card
          title="Макроэкономика"
          subtitle="Ключевая ставка, инфляция и доходность ОФЗ"
        >
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {macro.key_rate && (
              <StatTile
                label="Ключевая ставка"
                value={pctRaw(macro.key_rate.value)}
                delta={
                  macro.key_rate.change === null
                    ? 'без изменений'
                    : `${macro.key_rate.change > 0 ? '+' : macro.key_rate.change < 0 ? '−' : ''}${num(
                        Math.abs(macro.key_rate.change),
                        2,
                      )} п.п.`
                }
                deltaTone="neutral"
                hint={`с ${fmtDate(macro.key_rate.date)}${
                  macro.key_rate.as_of ? ` · данные на ${fmtDate(macro.key_rate.as_of)}` : ''
                }`}
              />
            )}
            {macro.inflation && (
              <StatTile
                label="Инфляция г/г"
                value={pctRaw(macro.inflation.value)}
                delta={
                  macro.inflation.previous !== null
                    ? `${macro.inflation.value >= macro.inflation.previous ? '+' : '−'}${num(
                        Math.abs(macro.inflation.value - macro.inflation.previous),
                        2,
                      )} п.п. к прошлому месяцу`
                    : undefined
                }
                deltaTone="neutral"
                hint={`${fmtMonth(macro.inflation.date)}${
                  macro.inflation.target !== null ? ` · цель ${pctRaw(macro.inflation.target)}` : ''
                }`}
              />
            )}
            {macro.inflation_monthly && (
              <StatTile
                label="Инфляция м/м"
                value={pctRaw(macro.inflation_monthly.value)}
                hint={`${
                  macro.inflation_monthly.date
                    ? fmtMonth(macro.inflation_monthly.date)
                    : 'последний месяц'
                } · с сезонной корректировкой`}
              />
            )}
            {macro.ofz && (
              <StatTile
                label={`ОФЗ ${num(macro.ofz.tenor, 0)} лет`}
                value={pctRaw(macro.ofz.value)}
                hint={`${fmtDate(macro.ofz.date)} · кривая бескупонной доходности`}
              />
            )}
          </div>
          {macro.notes.length > 0 && (
            <p className="mt-3 text-2xs text-muted">{macro.notes.join(' ')}</p>
          )}
        </Card>
      )}

      <Card title="Крупнейшие позиции" subtitle={`Топ-10 из ${summary.positions_count}`}>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-xs">
            <thead>
              <tr className="border-b border-line text-left text-2xs uppercase text-muted">
                <th className="py-2 pr-3 font-medium">Инструмент</th>
                <th className="py-2 pr-3 text-right font-medium">Доля</th>
                <th className="py-2 pr-3 text-right font-medium">Кол-во</th>
                <th className="py-2 pr-3 text-right font-medium">Цена</th>
                <th className="py-2 pr-3 text-right font-medium">Стоимость</th>
                <th className="py-2 pr-3 text-right font-medium">Прибыль</th>
                <th className="py-2 text-right font-medium">За день</th>
              </tr>
            </thead>
            <tbody>
              {data.positions.slice(0, 10).map((p) => (
                <tr key={p.asset_id} className="border-b border-line/60">
                  <td className="py-2 pr-3">
                    <div className="font-medium text-ink">{p.name}</div>
                    <div className="text-2xs text-muted">
                      {p.secid} · {p.asset_class_label}
                    </div>
                  </td>
                  <td className="tnum py-2 pr-3 text-right text-ink-2">{pctRaw(p.weight * 100, 1)}</td>
                  <td className="tnum py-2 pr-3 text-right text-ink-2">{p.quantity}</td>
                  <td className="tnum py-2 pr-3 text-right text-ink-2">{rub(p.last_price)}</td>
                  <td className="tnum py-2 pr-3 text-right font-medium text-ink">{rub(p.market_value, 0)}</td>
                  <td className="py-2 pr-3 text-right">
                    <Money value={p.unrealized_pnl} render={(v) => rub(v, 0)} />
                    <div className="text-2xs text-muted">{pct(p.unrealized_pnl_pct)}</div>
                  </td>
                  <td className="py-2 text-right">
                    <Money value={p.day_change} render={(v) => rub(v, 0)} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Link
          to="/positions"
          className="mt-3 inline-block text-2xs text-[var(--series-1)] hover:underline"
        >
          все позиции →
        </Link>
      </Card>
    </div>
  )
}

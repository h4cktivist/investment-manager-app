import { useMemo, useState } from 'react'

import { MonthlyBars } from '../components/charts'
import {
  Badge,
  Button,
  Card,
  ErrorBox,
  SegmentedControl,
  Spinner,
  Spinner as Loading,
  StatTile,
} from '../components/ui'
import { useAutoPostPayments, useBookPayment, useCalendar, usePaymentsHistory } from '../hooks/useApi'
import type { BookPaymentResult, CalendarItem } from '../lib/api'
import { PAYMENT_LABELS, daysWord, fmtDate, fmtMonth, num, rub } from '../lib/format'
import { useSelection } from '../lib/store'

const HORIZONS = [
  { value: '3', label: '3 месяца' },
  { value: '6', label: '6 месяцев' },
  { value: '12', label: '12 месяцев' },
  { value: '24', label: '2 года' },
]

export default function CalendarPage() {
  const [horizon, setHorizon] = useState('12')
  const [tab, setTab] = useState<'plan' | 'history'>('plan')
  const { selected } = useSelection()

  const dateTo = useMemo(() => {
    const d = new Date()
    d.setMonth(d.getMonth() + Number(horizon))
    return d.toISOString().slice(0, 10)
  }, [horizon])

  const { data, isLoading, error } = useCalendar(undefined, dateTo)
  const history = usePaymentsHistory()
  const book = useBookPayment()
  const autoPost = useAutoPostPayments()
  const today = localToday()

  const handleBook = (item: CalendarItem) => {
    if (
      item.payment_date > today &&
      !confirm('Дата выплаты ещё не наступила. Зачислить её досрочно?')
    ) {
      return
    }
    if (
      item.is_forecast &&
      !confirm('Сумма выплаты прогнозная. Зачислить её по прогнозной сумме?')
    ) {
      return
    }
    book.reset()
    book.mutate({
      asset_id: item.asset_id,
      kind: item.kind,
      payment_date: item.payment_date,
      portfolio_ids: selected.length ? selected : undefined,
    })
  }

  const handleAutoPost = () => {
    if (!confirm('Зачислить все прошедшие выплаты из календаря в сделки?')) return
    autoPost.reset()
    autoPost.mutate()
  }

  const bookingKey = book.isPending && book.variables ? itemKey(book.variables) : null

  if (isLoading) return <Spinner />
  if (error) return <ErrorBox error={error} />
  if (!data) return null

  const upcoming = data.items.filter((i) => !i.is_paid)
  const byKind = upcoming.reduce<Record<string, number>>((acc, item) => {
    acc[item.kind] = (acc[item.kind] ?? 0) + item.amount
    return acc
  }, {})
  const perMonth = upcoming.length
    ? data.total_amount / Math.max(new Set(upcoming.map((i) => i.payment_date.slice(0, 7))).size, 1)
    : 0

  const grouped = upcoming.reduce<Record<string, CalendarItem[]>>((acc, item) => {
    const key = item.payment_date.slice(0, 7)
    ;(acc[key] ??= []).push(item)
    return acc
  }, {})

  const currentMonth = new Date().toISOString().slice(0, 7)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold text-ink">Календарь выплат</h1>
        <div className="flex flex-wrap gap-2">
          <SegmentedControl
            value={tab}
            onChange={setTab}
            options={[
              { value: 'plan', label: 'Ожидаются' },
              { value: 'history', label: 'Получены' },
            ]}
          />
          {tab === 'plan' && (
            <>
              <SegmentedControl size="xs" value={horizon} onChange={setHorizon} options={HORIZONS} />
              <Button
                onClick={handleAutoPost}
                disabled={autoPost.isPending}
                title="Создать операции по выплатам, дата которых уже прошла"
              >
                {autoPost.isPending ? 'Зачисляю…' : '↻ Зачислить прошедшие'}
              </Button>
            </>
          )}
        </div>
      </div>

      {tab === 'plan' ? (
        <>
          {book.data && (
            <BookingNotice result={book.data} onClose={() => book.reset()} />
          )}
          {book.isError && <ErrorBox error={book.error} />}
          {autoPost.data && (
            <div className="flex items-center justify-between gap-3 rounded-card border border-[var(--good)]/40 bg-surface px-4 py-2.5 text-xs text-ink-2">
              <span>
                {autoPost.data.posted
                  ? `Автоматически зачислено выплат: ${num(autoPost.data.posted, 0)}`
                  : 'Новых прошедших выплат для зачисления нет'}
              </span>
              <button
                onClick={() => autoPost.reset()}
                className="text-muted hover:text-ink"
                aria-label="Скрыть сообщение"
              >
                ×
              </button>
            </div>
          )}
          {autoPost.isError && <ErrorBox error={autoPost.error} />}

          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <StatTile
              large
              label={`Ожидается за ${horizon} мес.`}
              value={rub(data.total_amount, 0)}
              hint={`${upcoming.length} выплат`}
            />
            <StatTile label="В среднем в месяц" value={rub(perMonth, 0)} />
            <StatTile
              label="Купоны"
              value={rub((byKind.COUPON ?? 0) + (byKind.AMORTIZATION ?? 0) + (byKind.MATURITY ?? 0), 0)}
              hint="включая амортизацию и погашения"
            />
            <StatTile
              label="Дивиденды"
              value={rub((byKind.DIVIDEND ?? 0) + (byKind.CUSTOM_INCOME ?? 0), 0)}
              hint="по объявленным отсечкам"
            />
          </div>

          {data.next_payment && (
            <Card title="Ближайшая выплата">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="text-2xl font-semibold text-ink">{rub(data.next_payment.amount)}</span>
                <span className="text-sm text-ink-2">{data.next_payment.name}</span>
                <Badge>{PAYMENT_LABELS[data.next_payment.kind] ?? data.next_payment.kind}</Badge>
                <span className="text-xs text-muted">
                  {fmtDate(data.next_payment.payment_date)}
                  {' · '}
                  {daysUntil(data.next_payment.payment_date)}
                </span>
              </div>
            </Card>
          )}

          <Card title="Выплаты по месяцам" subtitle="Суммы рассчитаны по текущему количеству бумаг">
            <MonthlyBars
              data={data.by_month}
              forecastFrom={currentMonth}
              emptyLabel="Ожидаемых выплат нет"
              height={220}
            />
          </Card>

          <div className="space-y-3">
            {Object.entries(grouped).map(([month, items], index) => (
              <MonthGroup
                key={month}
                month={month}
                items={items}
                defaultOpen={index < 2}
                onBook={handleBook}
                bookingKey={bookingKey}
              />
            ))}
            {!upcoming.length && (
              <Card>
                <p className="py-8 text-center text-xs text-muted">
                  Ожидаемых выплат в выбранном горизонте нет. Проверьте, что по облигациям
                  загружен купонный график — кнопка синхронизации на странице «Активы».
                </p>
              </Card>
            )}
          </div>
        </>
      ) : history.isLoading ? (
        <Loading />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <StatTile large label="Получено всего" value={rub(history.data?.total ?? 0, 0)} />
            <StatTile label="Выплат" value={num(history.data?.items.length ?? 0, 0)} />
            <StatTile
              label="Последняя"
              value={history.data?.items[0] ? rub(history.data.items[0].net) : '—'}
              hint={history.data?.items[0] ? fmtDate(history.data.items[0].date) : undefined}
            />
          </div>

          <Card title="Полученные выплаты по месяцам">
            <MonthlyBars data={history.data?.by_month ?? []} emptyLabel="Выплат ещё не было" height={220} />
          </Card>

          <Card title="Все зачисления" bodyClassName="p-0">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-xs">
                <thead>
                  <tr className="border-b border-line text-left text-2xs uppercase text-muted">
                    <th className="px-3 py-2 font-medium">Дата</th>
                    <th className="px-3 py-2 font-medium">Инструмент</th>
                    <th className="px-3 py-2 font-medium">Тип</th>
                    <th className="px-3 py-2 text-right font-medium">Начислено</th>
                    <th className="px-3 py-2 text-right font-medium">Налог</th>
                    <th className="px-3 py-2 text-right font-medium">К зачислению</th>
                  </tr>
                </thead>
                <tbody>
                  {(history.data?.items ?? []).map((item) => (
                    <tr key={item.id} className="border-b border-line/60">
                      <td className="tnum whitespace-nowrap px-3 py-2 text-ink-2">{fmtDate(item.date)}</td>
                      <td className="px-3 py-2">
                        <div className="font-medium text-ink">{item.name ?? '—'}</div>
                        <div className="text-2xs text-muted">{item.secid}</div>
                      </td>
                      <td className="px-3 py-2">
                        <Badge>{PAYMENT_LABELS[item.type] ?? item.type_label}</Badge>
                      </td>
                      <td className="tnum px-3 py-2 text-right text-ink-2">{rub(item.amount)}</td>
                      <td className="tnum px-3 py-2 text-right text-muted">
                        {item.tax ? rub(item.tax) : '—'}
                      </td>
                      <td className="tnum px-3 py-2 text-right font-medium text-ink">{rub(item.net)}</td>
                    </tr>
                  ))}
                  {!history.data?.items.length && (
                    <tr>
                      <td colSpan={6} className="px-3 py-10 text-center text-muted">
                        Выплат ещё не было
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  )
}

function BookingNotice({
  result,
  onClose,
}: {
  result: BookPaymentResult
  onClose: () => void
}) {
  const created = result.items.filter((item) => item.created)
  const label = PAYMENT_LABELS[result.kind] ?? result.kind
  const date = fmtDate(result.payment_date)

  return (
    <div className="flex items-start justify-between gap-3 rounded-card border border-[var(--good)]/40 bg-surface px-4 py-2.5 text-xs text-ink-2">
      <span>
        {created.length ? (
          <>
            {`Зачислено: ${label.toLowerCase()} за ${date} — ${rub(
              created.reduce((sum, item) => sum + item.amount, 0),
            )}`}
            {` · ${[...new Set(created.map((item) => item.portfolio_name))].join(', ')}`}
          </>
        ) : (
          `Выплата «${label}» за ${date} уже зачислена ранее`
        )}
      </span>
      <button onClick={onClose} className="text-muted hover:text-ink" aria-label="Скрыть сообщение">
        ×
      </button>
    </div>
  )
}

function itemKey(item: { asset_id: number; kind: string; payment_date: string }): string {
  return `${item.asset_id}-${item.kind}-${item.payment_date}`
}

function localToday(): string {
  const now = new Date()
  return new Date(now.getTime() - now.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 10)
}

function MonthGroup({
  month,
  items,
  defaultOpen,
  onBook,
  bookingKey,
}: {
  month: string
  items: CalendarItem[]
  defaultOpen: boolean
  onBook: (item: CalendarItem) => void
  bookingKey: string | null
}) {
  const [open, setOpen] = useState(defaultOpen)
  const total = items.reduce((s, i) => s + i.amount, 0)

  return (
    <section className="overflow-hidden rounded-card border border-line bg-surface">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-raised"
      >
        <span aria-hidden className="w-3 text-xs text-muted">
          {open ? '▾' : '▸'}
        </span>
        <span className="text-sm font-semibold capitalize text-ink">{fmtMonth(month)}</span>
        <span className="text-2xs text-muted">{items.length} выплат</span>
        <span className="tnum ml-auto text-sm font-semibold text-ink">{rub(total, 0)}</span>
      </button>

      {open && (
        <div className="overflow-x-auto border-t border-line">
          <table className="w-full min-w-[860px] text-xs">
            <thead>
              <tr className="border-b border-line text-left text-2xs uppercase text-muted">
                <th className="px-3 py-2 font-medium">Дата</th>
                <th className="px-3 py-2 font-medium">Инструмент</th>
                <th className="px-3 py-2 font-medium">Тип</th>
                <th className="px-3 py-2 text-right font-medium">На бумагу</th>
                <th className="px-3 py-2 text-right font-medium">Кол-во</th>
                <th className="px-3 py-2 text-right font-medium">Сумма</th>
                <th className="px-3 py-2 font-medium">Отсечка</th>
                <th className="px-3 py-2 text-right font-medium">Действие</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr
                  key={itemKey(item)}
                  className="border-b border-line/60"
                >
                  <td className="tnum whitespace-nowrap px-3 py-2 text-ink-2">
                    {fmtDate(item.payment_date)}
                  </td>
                  <td className="px-3 py-2">
                    <div className="font-medium text-ink">{item.name}</div>
                    <div className="text-2xs text-muted">{item.secid}</div>
                  </td>
                  <td className="px-3 py-2">
                    <Badge tone={item.kind === 'DIVIDEND' ? 'info' : 'neutral'}>
                      {PAYMENT_LABELS[item.kind] ?? item.kind}
                    </Badge>
                    {item.is_forecast && (
                      <span className="ml-1 text-2xs text-muted" title={item.note ?? ''}>
                        прогноз
                      </span>
                    )}
                  </td>
                  <td className="tnum px-3 py-2 text-right text-ink-2">
                    {rub(item.value_per_unit)}
                  </td>
                  <td className="tnum px-3 py-2 text-right text-ink-2">
                    {num(item.quantity, 0)}
                  </td>
                  <td className="tnum px-3 py-2 text-right font-medium text-ink">
                    {rub(item.amount)}
                  </td>
                  <td className="tnum px-3 py-2 text-2xs text-muted">
                    {item.record_date ? fmtDate(item.record_date) : '—'}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-right">
                    <button
                      onClick={() => onBook(item)}
                      disabled={bookingKey !== null}
                      title={
                        item.is_forecast
                          ? 'Сумма выплаты прогнозная — после зачисления проверьте её'
                          : 'Создать операцию по этой выплате'
                      }
                      className="rounded-md border border-line px-2 py-1 text-2xs font-medium text-ink-2 transition hover:bg-raised hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {bookingKey === itemKey(item) ? 'Зачисляю…' : 'Зачислить'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function daysUntil(date: string): string {
  const days = Math.round((new Date(date).getTime() - Date.now()) / 86_400_000)
  if (days <= 0) return 'сегодня'
  return `через ${daysWord(days)}`
}

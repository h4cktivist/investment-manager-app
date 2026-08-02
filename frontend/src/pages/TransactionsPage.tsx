import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { Badge, Button, Card, ErrorBox, Money, Spinner, cx } from '../components/ui'
import { api, qs, type Transaction, type TransactionPageResponse } from '../lib/api'
import { TX_LABELS, fmtDate, num, rub } from '../lib/format'
import { useSelection } from '../lib/store'
import TransactionForm from './TransactionForm'

const TYPE_TONES: Record<string, 'good' | 'bad' | 'info' | 'neutral'> = {
  BUY: 'info',
  SELL: 'neutral',
  DIVIDEND: 'good',
  COUPON: 'good',
  AMORTIZATION: 'good',
  INTEREST: 'good',
  DEPOSIT: 'good',
  WITHDRAWAL: 'bad',
  TAX: 'bad',
  FEE: 'bad',
  SPLIT: 'neutral',
}

const PAGE_SIZE = 100

export default function TransactionsPage() {
  const { selected, portfolios } = useSelection()
  const queryClient = useQueryClient()
  const [offset, setOffset] = useState(0)
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState<string>('')
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Transaction | null>(null)

  const { data, isLoading, error } = useQuery({
    queryKey: ['transactions', selected, offset, search, typeFilter],
    queryFn: () =>
      api.get<TransactionPageResponse>(
        `/transactions${qs({
          portfolio_id: selected,
          limit: PAGE_SIZE,
          offset,
          search: search.trim() || undefined,
          type: typeFilter || undefined,
        })}`,
      ),
  })

  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/transactions/${id}`),
    onSuccess: () => queryClient.invalidateQueries(),
  })

  const items = data?.items ?? []
  const total = data?.total ?? 0
  const pages = Math.ceil(total / PAGE_SIZE)
  const page = Math.floor(offset / PAGE_SIZE) + 1

  const types = useMemo(() => Object.keys(TX_LABELS), [])

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-ink">Сделки и выплаты</h1>
          <p className="text-2xs text-muted">Всего записей: {num(total, 0)}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={search}
            onChange={(e) => {
              setSearch(e.target.value)
              setOffset(0)
            }}
            placeholder="Тикер или название"
            className="w-48 rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-ink placeholder:text-muted focus:border-[var(--series-1)] focus:outline-none"
          />
          <select
            value={typeFilter}
            onChange={(e) => {
              setTypeFilter(e.target.value)
              setOffset(0)
            }}
            className="rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-ink focus:border-[var(--series-1)] focus:outline-none"
          >
            <option value="">Все операции</option>
            {types.map((t) => (
              <option key={t} value={t}>
                {TX_LABELS[t]}
              </option>
            ))}
          </select>
          <Button
            variant="primary"
            onClick={() => {
              setEditing(null)
              setFormOpen(true)
            }}
            disabled={!portfolios.length}
            title={!portfolios.length ? 'Сначала создайте портфель' : undefined}
          >
            + Добавить
          </Button>
        </div>
      </div>

      {formOpen && (
        <TransactionForm
          transaction={editing}
          onClose={() => {
            setFormOpen(false)
            setEditing(null)
          }}
        />
      )}

      {error && <ErrorBox error={error} />}
      {isLoading ? (
        <Spinner />
      ) : (
        <Card bodyClassName="p-0">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-xs">
              <thead>
                <tr className="border-b border-line text-left text-2xs uppercase text-muted">
                  <th className="px-3 py-2 font-medium">Дата</th>
                  <th className="px-3 py-2 font-medium">Операция</th>
                  <th className="px-3 py-2 font-medium">Инструмент</th>
                  <th className="px-3 py-2 text-right font-medium">Цена</th>
                  <th className="px-3 py-2 text-right font-medium">Кол-во</th>
                  <th className="px-3 py-2 text-right font-medium">Сумма</th>
                  <th className="px-3 py-2 text-right font-medium">Комиссия / НКД</th>
                  <th className="px-3 py-2 font-medium">Портфель</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {items.map((tx) => (
                  <tr key={tx.id} className="border-b border-line/60">
                    <td className="tnum whitespace-nowrap px-3 py-2 text-ink-2">
                      {fmtDate(tx.executed_at)}
                    </td>
                    <td className="px-3 py-2">
                      <Badge tone={TYPE_TONES[tx.type] ?? 'neutral'}>
                        {TX_LABELS[tx.type] ?? tx.type}
                      </Badge>
                    </td>
                    <td className="px-3 py-2">
                      {tx.secid ? (
                        <>
                          <div className="font-medium text-ink">{tx.asset_name}</div>
                          <div className="text-2xs text-muted">{tx.secid}</div>
                        </>
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>
                    <td className="tnum px-3 py-2 text-right text-ink-2">
                      {tx.price ? rub(tx.price) : '—'}
                    </td>
                    <td className="tnum px-3 py-2 text-right text-ink-2">
                      {tx.quantity ? num(tx.quantity, 4) : '—'}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <Money
                        value={signOf(tx.type) * tx.amount}
                        render={(v) => rub(v)}
                      />
                    </td>
                    <td className="tnum px-3 py-2 text-right text-2xs text-muted">
                      {tx.fee ? rub(tx.fee) : '—'}
                      {tx.accrued_interest ? ` / ${rub(tx.accrued_interest)}` : ''}
                    </td>
                    <td className="px-3 py-2 text-2xs text-muted">{tx.portfolio_name}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right">
                      <button
                        onClick={() => {
                          setEditing(tx)
                          setFormOpen(true)
                        }}
                        className="text-2xs text-[var(--series-1)] hover:underline"
                      >
                        править
                      </button>
                      <button
                        onClick={() => {
                          if (confirm('Удалить операцию? Метрики пересчитаются.')) remove.mutate(tx.id)
                        }}
                        className="ml-2 text-2xs text-[var(--critical)] hover:underline"
                      >
                        удалить
                      </button>
                    </td>
                  </tr>
                ))}
                {!items.length && (
                  <tr>
                    <td colSpan={9} className="px-3 py-10 text-center text-muted">
                      Операций нет
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {pages > 1 && (
            <div className="flex items-center justify-between border-t border-line px-3 py-2">
              <span className="text-2xs text-muted">
                Страница {page} из {pages}
              </span>
              <div className="flex gap-2">
                <Button disabled={offset === 0} onClick={() => setOffset(Math.max(offset - PAGE_SIZE, 0))}>
                  ← Назад
                </Button>
                <Button
                  disabled={offset + PAGE_SIZE >= total}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                >
                  Вперёд →
                </Button>
              </div>
            </div>
          )}
        </Card>
      )}

      {items.some((t) => t.note) && (
        <p className={cx('text-2xs text-muted')}>
          Подсказка: в примечании к импортированным выплатам сохранено правило, по которому
          определена сумма.
        </p>
      )}
    </div>
  )
}

/** Знак для отображения суммы: покупка уменьшает деньги, выплата увеличивает. */
function signOf(type: string): number {
  if (['BUY', 'WITHDRAWAL', 'TAX', 'FEE'].includes(type)) return -1
  return 1
}

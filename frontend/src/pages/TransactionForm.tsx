import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'

import { Button, Card, cx } from '../components/ui'
import { api, qs, type AssetSearchResult, type Transaction } from '../lib/api'
import { TX_LABELS, rub } from '../lib/format'
import { useSelection } from '../lib/store'

const NO_ASSET = new Set(['DEPOSIT', 'WITHDRAWAL', 'TAX', 'FEE'])
const QTY_TYPES = new Set(['BUY', 'SELL', 'SPLIT'])

interface FormState {
  portfolio_id: number
  type: string
  executed_at: string
  secid: string
  price: string
  quantity: string
  amount: string
  fee: string
  tax: string
  accrued_interest: string
  note: string
}

function initialState(portfolioId: number, tx: Transaction | null): FormState {
  if (!tx) {
    return {
      portfolio_id: portfolioId,
      type: 'BUY',
      executed_at: new Date().toISOString().slice(0, 10),
      secid: '',
      price: '',
      quantity: '',
      amount: '',
      fee: '',
      tax: '',
      accrued_interest: '',
      note: '',
    }
  }
  return {
    portfolio_id: tx.portfolio_id,
    type: tx.type,
    executed_at: tx.executed_at.slice(0, 10),
    secid: tx.secid ?? '',
    price: String(tx.price || ''),
    quantity: String(tx.quantity || ''),
    amount: String(tx.amount || ''),
    fee: String(tx.fee || ''),
    tax: String(tx.tax || ''),
    accrued_interest: String(tx.accrued_interest || ''),
    note: tx.note ?? '',
  }
}

export default function TransactionForm({
  transaction,
  onClose,
}: {
  transaction: Transaction | null
  onClose: () => void
}) {
  const { portfolios, selected } = useSelection()
  const queryClient = useQueryClient()
  const defaultPortfolio = selected[0] ?? portfolios[0]?.id ?? 0
  const [form, setForm] = useState<FormState>(() => initialState(defaultPortfolio, transaction))
  const [searchTerm, setSearchTerm] = useState('')
  const [error, setError] = useState<string | null>(null)

  const isEdit = Boolean(transaction)
  const needsAsset = !NO_ASSET.has(form.type)
  const showQty = QTY_TYPES.has(form.type)

  useEffect(() => {
    setForm(initialState(defaultPortfolio, transaction))
  }, [transaction, defaultPortfolio])

  const { data: found = [] } = useQuery({
    queryKey: ['asset-search', searchTerm],
    queryFn: () => api.get<AssetSearchResult[]>(`/assets/search${qs({ q: searchTerm, limit: 8 })}`),
    enabled: searchTerm.trim().length >= 2,
    staleTime: 300_000,
  })

  const computedAmount = useMemo(() => {
    const price = Number(form.price) || 0
    const qty = Number(form.quantity) || 0
    if (form.amount) return Number(form.amount)
    return showQty ? price * qty : price
  }, [form.price, form.quantity, form.amount, showQty])

  const save = useMutation({
    mutationFn: async () => {
      const payload: Record<string, unknown> = {
        type: form.type,
        executed_at: `${form.executed_at}T00:00:00`,
        price: Number(form.price) || 0,
        quantity: Number(form.quantity) || 0,
        amount: form.amount ? Number(form.amount) : undefined,
        fee: Number(form.fee) || 0,
        tax: Number(form.tax) || 0,
        accrued_interest: Number(form.accrued_interest) || 0,
        note: form.note || null,
      }
      if (isEdit) return api.patch(`/transactions/${transaction!.id}`, payload)
      return api.post('/transactions', {
        ...payload,
        portfolio_id: form.portfolio_id,
        secid: needsAsset ? form.secid.toUpperCase() : null,
        currency: 'RUB',
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries()
      onClose()
    },
    onError: (e) => setError(e instanceof Error ? e.message : String(e)),
  })

  const set = (key: keyof FormState) => (e: { target: { value: string } }) =>
    setForm((prev) => ({ ...prev, [key]: e.target.value }))

  return (
    <Card
      title={isEdit ? `Правка операции #${transaction!.id}` : 'Новая операция'}
      subtitle={
        isEdit
          ? 'Инструмент и портфель у существующей операции не меняются'
          : 'Инструмент подтянется с Мосбиржи автоматически, вместе с историей цен и купонами'
      }
      actions={
        <Button onClick={onClose} title="Закрыть">
          ✕
        </Button>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          setError(null)
          if (needsAsset && !form.secid.trim()) {
            setError('Укажите тикер инструмента')
            return
          }
          save.mutate()
        }}
        className="grid gap-3 md:grid-cols-3 lg:grid-cols-4"
      >
        <Field label="Портфель">
          <select
            value={form.portfolio_id}
            onChange={(e) => setForm((p) => ({ ...p, portfolio_id: Number(e.target.value) }))}
            disabled={isEdit}
            className={inputClass}
          >
            {portfolios.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Операция">
          <select value={form.type} onChange={set('type')} className={inputClass}>
            {Object.entries(TX_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Дата">
          <input type="date" value={form.executed_at} onChange={set('executed_at')} className={inputClass} />
        </Field>

        {needsAsset && (
          <Field label="Тикер" hint={isEdit ? undefined : 'начните вводить название или код'}>
            <input
              value={form.secid}
              disabled={isEdit}
              onChange={(e) => {
                setForm((p) => ({ ...p, secid: e.target.value.toUpperCase() }))
                setSearchTerm(e.target.value)
              }}
              placeholder="SBER, RU000A10C5K9…"
              className={inputClass}
            />
            {!isEdit && found.length > 0 && searchTerm.length >= 2 && (
              <ul className="mt-1 max-h-40 overflow-y-auto rounded-md border border-line bg-surface">
                {found.map((item) => (
                  <li key={item.secid}>
                    <button
                      type="button"
                      onClick={() => {
                        setForm((p) => ({ ...p, secid: item.secid }))
                        setSearchTerm('')
                      }}
                      className="flex w-full items-center justify-between gap-2 px-2 py-1.5 text-left text-2xs hover:bg-raised"
                    >
                      <span className="truncate text-ink">{item.short_name ?? item.name}</span>
                      <span className="shrink-0 text-muted">{item.secid}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Field>
        )}

        <Field label={form.type === 'SPLIT' ? 'Коэффициент сплита' : 'Цена за штуку'}>
          <input
            type="number"
            step="any"
            value={form.price}
            onChange={set('price')}
            className={inputClass}
          />
        </Field>

        {showQty && form.type !== 'SPLIT' && (
          <Field label="Количество">
            <input
              type="number"
              step="any"
              value={form.quantity}
              onChange={set('quantity')}
              className={inputClass}
            />
          </Field>
        )}

        <Field
          label="Сумма"
          hint={form.amount ? undefined : `расчётная: ${rub(computedAmount)}`}
        >
          <input
            type="number"
            step="any"
            value={form.amount}
            onChange={set('amount')}
            placeholder={String(computedAmount.toFixed(2))}
            className={inputClass}
          />
        </Field>

        <Field label="Комиссия">
          <input type="number" step="any" value={form.fee} onChange={set('fee')} className={inputClass} />
        </Field>

        <Field label="Налог">
          <input type="number" step="any" value={form.tax} onChange={set('tax')} className={inputClass} />
        </Field>

        <Field label="НКД" hint="накопленный купонный доход">
          <input
            type="number"
            step="any"
            value={form.accrued_interest}
            onChange={set('accrued_interest')}
            className={inputClass}
          />
        </Field>

        <Field label="Примечание" className="md:col-span-2">
          <input value={form.note} onChange={set('note')} className={inputClass} />
        </Field>

        <div className="flex items-end gap-2 md:col-span-3 lg:col-span-4">
          <Button type="submit" variant="primary" disabled={save.isPending}>
            {save.isPending ? 'Сохраняю…' : isEdit ? 'Сохранить' : 'Добавить'}
          </Button>
          <Button onClick={onClose}>Отмена</Button>
          {error && <span className="text-xs text-[var(--critical)]">{error}</span>}
        </div>
      </form>
    </Card>
  )
}

const inputClass =
  'w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-ink placeholder:text-muted focus:border-[var(--series-1)] focus:outline-none disabled:opacity-60'

function Field({
  label,
  hint,
  children,
  className,
}: {
  label: string
  hint?: string
  children: React.ReactNode
  className?: string
}) {
  return (
    <label className={cx('block', className)}>
      <span className="mb-1 block text-2xs uppercase tracking-wide text-muted">{label}</span>
      {children}
      {hint && <span className="mt-0.5 block text-2xs text-muted">{hint}</span>}
    </label>
  )
}

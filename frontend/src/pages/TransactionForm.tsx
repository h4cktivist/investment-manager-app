import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'

import { Button, Card, cx } from '../components/ui'
import {
  api,
  qs,
  type AssetQuote,
  type AssetSearchResult,
  type Position,
  type Transaction,
} from '../lib/api'
import { TX_LABELS, num, rub } from '../lib/format'
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
  const [lookupEnabled, setLookupEnabled] = useState(false)
  // какие поля заполнены автоматически — ручной ввод важнее
  const autoPrice = useRef(false)
  const autoNkd = useRef(false)

  const isEdit = Boolean(transaction)
  const isSell = form.type === 'SELL'
  const needsAsset = !NO_ASSET.has(form.type)
  const showQty = QTY_TYPES.has(form.type)

  useEffect(() => {
    setForm(initialState(defaultPortfolio, transaction))
    autoPrice.current = false
    autoNkd.current = false
    setLookupEnabled(false)
  }, [transaction, defaultPortfolio])

  const { data: found = [] } = useQuery({
    queryKey: ['asset-search', searchTerm],
    queryFn: () => api.get<AssetSearchResult[]>(`/assets/search${qs({ q: searchTerm, limit: 8 })}`),
    enabled: searchTerm.trim().length >= 2,
    staleTime: 300_000,
  })

  const { data: held = [] } = useQuery({
    queryKey: ['positions-form', form.portfolio_id],
    queryFn: () =>
      api.get<Position[]>(`/portfolio/positions${qs({ portfolio_id: [form.portfolio_id] })}`),
    enabled: isSell && form.portfolio_id > 0,
    staleTime: 30_000,
  })

  const bondQuote = useQuery({
    queryKey: ['asset-quote', form.secid, form.executed_at],
    queryFn: () =>
      api.get<AssetQuote>(
        `/assets/quote${qs({
          secid: form.secid.trim().toUpperCase(),
          date: form.executed_at,
        })}`,
      ),
    enabled:
      lookupEnabled &&
      needsAsset &&
      !isEdit &&
      form.secid.trim().length >= 2 &&
      (form.type === 'BUY' || form.type === 'SELL'),
    staleTime: 300_000,
    retry: false,
  })

  useEffect(() => {
    const quote = bondQuote.data
    if (!quote?.is_bond) return
    setForm((prev) => {
      const next = { ...prev }
      if (quote.price !== null && (!prev.price || autoPrice.current)) {
        next.price = String(quote.price)
        autoPrice.current = true
      }
      if (
        quote.accrued_interest !== null &&
        (!prev.accrued_interest || autoNkd.current)
      ) {
        next.accrued_interest = String(quote.accrued_interest)
        autoNkd.current = true
      }
      return next
    })
  }, [bondQuote.data])

  const selectedHolding = held.find((item) => item.secid === form.secid)

  const bond = bondQuote.data
  const isBond = Boolean(bond?.is_bond)

  const computedAmount = useMemo(() => {
    const price = Number(form.price) || 0
    const qty = Number(form.quantity) || 0
    const nkd = Number(form.accrued_interest) || 0
    if (form.amount) return Number(form.amount)
    if (showQty) {
      // НКД платится за каждую бумагу — входит в общую стоимость покупки
      return price * qty + (isBond ? nkd * qty : 0)
    }
    return price
  }, [form.price, form.quantity, form.accrued_interest, form.amount, showQty, isBond])

  const chooseAsset = (secid: string) => {
    autoPrice.current = false
    autoNkd.current = false
    setForm((prev) => ({
      ...prev,
      secid,
      // цена и НКД относятся к конкретной бумаге — старые значения не переносим
      price: '',
      accrued_interest: '',
      amount: '',
    }))
    setSearchTerm('')
    setLookupEnabled(true)
  }

  const changeType = (type: string) => {
    const switchingToSell = type === 'SELL'
    setForm((prev) => ({
      ...prev,
      type,
      secid: switchingToSell ? '' : prev.secid,
      quantity: switchingToSell ? '' : prev.quantity,
      price: switchingToSell ? '' : prev.price,
      accrued_interest: switchingToSell ? '' : prev.accrued_interest,
      amount: switchingToSell ? '' : prev.amount,
    }))
    autoPrice.current = false
    autoNkd.current = false
    setLookupEnabled(!switchingToSell)
    setSearchTerm('')
    setError(null)
  }

  const changePortfolio = (portfolioId: number) => {
    if (isSell) {
      autoPrice.current = false
      autoNkd.current = false
      setLookupEnabled(false)
      setForm((prev) => ({
        ...prev,
        portfolio_id: portfolioId,
        secid: '',
        price: '',
        accrued_interest: '',
        amount: '',
      }))
      return
    }
    setForm((prev) => ({ ...prev, portfolio_id: portfolioId }))
  }

  const changeSecid = (raw: string) => {
    const value = raw.toUpperCase()
    if (value !== form.secid) {
      autoPrice.current = false
      autoNkd.current = false
      setForm((prev) => ({
        ...prev,
        secid: value,
        price: '',
        accrued_interest: '',
        amount: '',
      }))
    } else {
      setForm((prev) => ({ ...prev, secid: value }))
    }
    setSearchTerm(raw)
    setLookupEnabled(false)
  }

  const changeManual = (key: 'price' | 'accrued_interest') => (e: { target: { value: string } }) => {
    if (key === 'price') autoPrice.current = false
    else autoNkd.current = false
    setForm((prev) => ({ ...prev, [key]: e.target.value }))
  }

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
            setError(isSell ? 'Выберите бумагу для продажи' : 'Укажите тикер инструмента')
            return
          }
          if (isSell && !isEdit) {
            const quantity = Number(form.quantity) || 0
            if (quantity <= 0) {
              setError('Количество в продаже должно быть больше нуля')
              return
            }
            // текущий остаток применим только к сегодняшней или будущей дате;
            // для исторической сделки решение принимает бэкенд по остатку на дату
            if (form.executed_at >= new Date().toISOString().slice(0, 10)) {
              if (!selectedHolding) {
                setError('В портфеле нет такой бумаги')
                return
              }
              if (quantity > selectedHolding.quantity + 1e-9) {
                setError(
                  `В портфеле только ${num(selectedHolding.quantity, 4)} ${selectedHolding.secid}`,
                )
                return
              }
            }
          }
          save.mutate()
        }}
        className="grid gap-3 md:grid-cols-3 lg:grid-cols-4"
      >
        <Field label="Портфель">
          <select
            value={form.portfolio_id}
            onChange={(e) => changePortfolio(Number(e.target.value))}
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
          <select value={form.type} onChange={(e) => changeType(e.target.value)} className={inputClass}>
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

        {needsAsset && isSell && !isEdit ? (
          <Field
            label="Инструмент"
            hint={
              held.length > 0
                ? 'продать можно только бумаги из портфеля'
                : 'в портфеле нет открытых позиций'
            }
          >
            <select
              value={form.secid}
              onChange={(e) => chooseAsset(e.target.value)}
              className={inputClass}
            >
              <option value="">— выберите бумагу —</option>
              {held.map((position) => (
                <option key={position.asset_id} value={position.secid}>
                  {position.secid} — {position.name} (остаток {num(position.quantity, 4)})
                </option>
              ))}
            </select>
          </Field>
        ) : needsAsset ? (
          <Field label="Тикер" hint={isEdit ? undefined : 'начните вводить название или код'}>
            <input
              value={form.secid}
              disabled={isEdit}
              onChange={(e) => changeSecid(e.target.value)}
              onBlur={() => {
                if (!isEdit && form.secid.trim().length >= 2) setLookupEnabled(true)
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
                      onClick={() => chooseAsset(item.secid)}
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
        ) : null}

        <Field
          label={form.type === 'SPLIT' ? 'Коэффициент сплита' : 'Цена за штуку'}
          hint={
            bondQuote.data?.is_bond && (autoPrice.current || autoNkd.current)
              ? 'подтянуто с Мосбиржи на дату'
              : undefined
          }
        >
          <input
            type="number"
            step="any"
            value={form.price}
            onChange={changeManual('price')}
            className={inputClass}
          />
        </Field>

        {showQty && form.type !== 'SPLIT' && (
          <Field
            label="Количество"
            hint={
              isSell && !isEdit && selectedHolding
                ? `в портфеле: ${num(selectedHolding.quantity, 4)}`
                : undefined
            }
          >
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
          hint={form.amount
            ? undefined
            : isBond && showQty
              ? `расчётная: ${rub(computedAmount)} (цена × qty + НКД × qty)`
              : `расчётная: ${rub(computedAmount)}`}
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

        <Field
          label="НКД"
          hint={
            bondQuote.data?.is_bond && autoNkd.current
              ? 'накопленный купонный доход — подтянут автоматически'
              : isBond
                ? 'накопленный купонный доход за единицу — умножается на количество'
                : 'накопленный купонный доход'
          }
        >
          <input
            type="number"
            step="any"
            value={form.accrued_interest}
            onChange={changeManual('accrued_interest')}
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

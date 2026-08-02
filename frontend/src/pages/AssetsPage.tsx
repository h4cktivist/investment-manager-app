import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { Badge, Button, Card, ErrorBox, SegmentedControl, Spinner, cx } from '../components/ui'
import { api, type Asset } from '../lib/api'
import { CLASS_LABELS, fmtDate, pctRaw, rub } from '../lib/format'

const FREQ_LABELS: Record<string, string> = {
  MONTHLY: 'ежемесячно',
  QUARTERLY: 'ежеквартально',
  SEMIANNUAL: 'раз в полгода',
  ANNUAL: 'раз в год',
  AT_MATURITY: 'в конце срока',
  NONE: 'без выплат',
}

export default function AssetsPage() {
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState('ALL')
  const [showForm, setShowForm] = useState(false)
  const [syncing, setSyncing] = useState<number | null>(null)

  const { data = [], isLoading, error } = useQuery({
    queryKey: ['assets'],
    queryFn: () => api.get<Asset[]>('/assets'),
  })

  const syncOne = useMutation({
    mutationFn: (id: number) => api.post(`/assets/${id}/sync`),
    onSettled: () => {
      setSyncing(null)
      queryClient.invalidateQueries()
    },
  })

  const syncAll = useMutation({
    mutationFn: () =>
      api.post('/sync', { with_history: true, with_schedule: true, with_benchmarks: true }),
    onSuccess: () => queryClient.invalidateQueries(),
  })

  const classes = useMemo(() => {
    const counts = new Map<string, number>()
    for (const a of data) counts.set(a.asset_class, (counts.get(a.asset_class) ?? 0) + 1)
    return [...counts.entries()]
  }, [data])

  const rows = filter === 'ALL' ? data : data.filter((a) => a.asset_class === filter)

  if (isLoading) return <Spinner />
  if (error) return <ErrorBox error={error} />

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-ink">Активы</h1>
          <p className="text-2xs text-muted">
            Биржевые инструменты подтягиваются с Мосбиржи, кастомные ведутся вручную
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <SegmentedControl
            size="xs"
            value={filter}
            onChange={setFilter}
            options={[
              { value: 'ALL', label: `Все (${data.length})` },
              ...classes.map(([key, count]) => ({
                value: key,
                label: `${CLASS_LABELS[key] ?? key} (${count})`,
              })),
            ]}
          />
          <Button onClick={() => syncAll.mutate()} disabled={syncAll.isPending}>
            {syncAll.isPending ? 'Синхронизирую…' : '↻ Обновить всё с биржи'}
          </Button>
          <Button variant="primary" onClick={() => setShowForm((v) => !v)}>
            + Кастомный актив
          </Button>
        </div>
      </div>

      {syncAll.isPending && (
        <p className="text-2xs text-muted">
          Идёт загрузка истории цен и купонных графиков. Это может занять несколько минут — можно
          продолжать пользоваться приложением.
        </p>
      )}
      {syncAll.error && <ErrorBox error={syncAll.error} />}

      {showForm && <CustomAssetForm onClose={() => setShowForm(false)} />}

      <Card bodyClassName="p-0">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] text-xs">
            <thead>
              <tr className="border-b border-line text-left text-2xs uppercase text-muted">
                <th className="px-3 py-2 font-medium">Инструмент</th>
                <th className="px-3 py-2 font-medium">Класс</th>
                <th className="px-3 py-2 font-medium">ISIN</th>
                <th className="px-3 py-2 text-right font-medium">Номинал / цена</th>
                <th className="px-3 py-2 text-right font-medium">Купон / ставка</th>
                <th className="px-3 py-2 text-right font-medium">Погашение</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {rows.map((asset) => (
                <tr key={asset.id} className="border-b border-line/60">
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-1.5">
                      <span className="font-medium text-ink">{asset.short_name ?? asset.name}</span>
                      {asset.is_custom && <Badge tone="info">свой</Badge>}
                      {!asset.is_active && <Badge tone="warn">не торгуется</Badge>}
                    </div>
                    <div className="text-2xs text-muted">{asset.secid}</div>
                  </td>
                  <td className="px-3 py-2 text-ink-2">
                    {CLASS_LABELS[asset.asset_class] ?? asset.asset_class}
                  </td>
                  <td className="px-3 py-2 text-2xs text-muted">{asset.isin ?? '—'}</td>
                  <td className="tnum px-3 py-2 text-right text-ink-2">
                    {asset.is_custom
                      ? rub(asset.manual_price)
                      : asset.face_value
                        ? rub(asset.face_value, 0)
                        : '—'}
                  </td>
                  <td className="tnum px-3 py-2 text-right text-ink-2">
                    {asset.is_custom
                      ? asset.annual_rate
                        ? `${pctRaw(asset.annual_rate)} · ${FREQ_LABELS[asset.payout_frequency ?? ''] ?? ''}`
                        : '—'
                      : asset.coupon_rate
                        ? pctRaw(asset.coupon_rate)
                        : '—'}
                  </td>
                  <td className="tnum px-3 py-2 text-right text-ink-2">
                    {asset.maturity_date ? fmtDate(asset.maturity_date) : asset.end_date ? fmtDate(asset.end_date) : '—'}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-right">
                    {!asset.is_custom && (
                      <button
                        onClick={() => {
                          setSyncing(asset.id)
                          syncOne.mutate(asset.id)
                        }}
                        disabled={syncing === asset.id}
                        className={cx(
                          'text-2xs text-[var(--series-1)] hover:underline',
                          syncing === asset.id && 'opacity-50',
                        )}
                      >
                        {syncing === asset.id ? 'обновляю…' : 'обновить'}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {!rows.length && (
                <tr>
                  <td colSpan={7} className="px-3 py-10 text-center text-muted">
                    Активов нет
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}

function CustomAssetForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState({
    secid: '',
    name: '',
    asset_class: 'DEPOSIT',
    manual_price: '1',
    annual_rate: '',
    payout_frequency: 'MONTHLY',
    capitalization: false,
    start_date: new Date().toISOString().slice(0, 10),
    end_date: '',
  })
  const [error, setError] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: () =>
      api.post('/assets/custom', {
        secid: form.secid.trim().toUpperCase(),
        name: form.name.trim(),
        asset_class: form.asset_class,
        currency: 'RUB',
        manual_price: Number(form.manual_price) || 1,
        annual_rate: form.annual_rate ? Number(form.annual_rate) : null,
        payout_frequency: form.payout_frequency,
        capitalization: form.capitalization,
        start_date: form.start_date || null,
        end_date: form.end_date || null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries()
      onClose()
    },
    onError: (e) => setError(e instanceof Error ? e.message : String(e)),
  })

  return (
    <Card
      title="Новый кастомный актив"
      subtitle="Например, банковский вклад под 18 % с ежемесячной выплатой процентов"
      actions={<Button onClick={onClose}>✕</Button>}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          setError(null)
          if (!form.secid.trim() || !form.name.trim()) {
            setError('Заполните код и название')
            return
          }
          create.mutate()
        }}
        className="grid gap-3 md:grid-cols-3 lg:grid-cols-4"
      >
        <Field label="Код" hint="уникальный, латиницей">
          <input
            value={form.secid}
            onChange={(e) => setForm((p) => ({ ...p, secid: e.target.value.toUpperCase() }))}
            placeholder="DEPOSIT-SBER"
            className={inputClass}
          />
        </Field>
        <Field label="Название" className="md:col-span-2">
          <input
            value={form.name}
            onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
            placeholder="Вклад в Сбере до 2027"
            className={inputClass}
          />
        </Field>
        <Field label="Тип">
          <select
            value={form.asset_class}
            onChange={(e) => setForm((p) => ({ ...p, asset_class: e.target.value }))}
            className={inputClass}
          >
            <option value="DEPOSIT">Вклад / накопительный счёт</option>
            <option value="CUSTOM">Прочий актив</option>
          </select>
        </Field>
        <Field label="Цена единицы" hint="для вклада оставьте 1 — тогда количество = рубли">
          <input
            type="number"
            step="any"
            value={form.manual_price}
            onChange={(e) => setForm((p) => ({ ...p, manual_price: e.target.value }))}
            className={inputClass}
          />
        </Field>
        <Field label="Ставка, % годовых">
          <input
            type="number"
            step="any"
            value={form.annual_rate}
            onChange={(e) => setForm((p) => ({ ...p, annual_rate: e.target.value }))}
            placeholder="18"
            className={inputClass}
          />
        </Field>
        <Field label="Периодичность выплат">
          <select
            value={form.payout_frequency}
            onChange={(e) => setForm((p) => ({ ...p, payout_frequency: e.target.value }))}
            className={inputClass}
          >
            {Object.entries(FREQ_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Капитализация">
          <label className="flex h-[30px] items-center gap-2 text-xs text-ink-2">
            <input
              type="checkbox"
              checked={form.capitalization}
              onChange={(e) => setForm((p) => ({ ...p, capitalization: e.target.checked }))}
            />
            проценты остаются во вкладе
          </label>
        </Field>
        <Field label="Дата открытия">
          <input
            type="date"
            value={form.start_date}
            onChange={(e) => setForm((p) => ({ ...p, start_date: e.target.value }))}
            className={inputClass}
          />
        </Field>
        <Field label="Дата закрытия">
          <input
            type="date"
            value={form.end_date}
            onChange={(e) => setForm((p) => ({ ...p, end_date: e.target.value }))}
            className={inputClass}
          />
        </Field>

        <div className="flex items-end gap-2 md:col-span-3 lg:col-span-4">
          <Button type="submit" variant="primary" disabled={create.isPending}>
            {create.isPending ? 'Создаю…' : 'Создать'}
          </Button>
          <Button onClick={onClose}>Отмена</Button>
          {error && <span className="text-xs text-[var(--critical)]">{error}</span>}
        </div>

        <p className="text-2xs text-muted md:col-span-3 lg:col-span-4">
          После создания добавьте операцию «Покупка» этого актива на сумму вклада — количество
          равно сумме в рублях при цене 1. Календарь выплат построится автоматически по ставке
          и периодичности.
        </p>
      </form>
    </Card>
  )
}

const inputClass =
  'w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-ink placeholder:text-muted focus:border-[var(--series-1)] focus:outline-none'

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

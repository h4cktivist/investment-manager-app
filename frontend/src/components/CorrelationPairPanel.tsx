import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { useCorrelationPair } from '../hooks/useApi'
import { api, qs, type AssetSearchResult, type CorrelationPairResult } from '../lib/api'
import { correlationStrength, fmtDate, num, pct, signedNumber } from '../lib/format'
import { useSelection } from '../lib/store'
import { ErrorBox, Spinner } from './ui'

const inputClass =
  'w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-ink placeholder:text-muted focus:border-[var(--series-1)] focus:outline-none'

/**
 * Корреляция двух произвольных бумаг: не обязательно из портфеля.
 * Поиск тот же, что в форме сделки — через справочник MOEX ISS.
 */
export default function CorrelationPairPanel() {
  const { period } = useSelection()
  const [left, setLeft] = useState('')
  const [right, setRight] = useState('')

  const same = Boolean(left && right && left === right)
  const query = useCorrelationPair(left, right, period)

  return (
    <div className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-2">
        <AssetPicker label="Первая бумага" value={left} onChange={setLeft} />
        <AssetPicker label="Вторая бумага" value={right} onChange={setRight} />
      </div>

      {same ? (
        <p className="text-xs text-[var(--critical)]">
          Выберите две разные бумаги — корреляция актива с самим собой всегда 1.
        </p>
      ) : !left || !right ? (
        <p className="text-2xs text-muted">
          Начните вводить тикер или название — подсказки придут с Мосбиржи.
        </p>
      ) : query.isLoading ? (
        <Spinner label="Считаю корреляцию…" />
      ) : query.error ? (
        <ErrorBox error={query.error} />
      ) : !query.data?.available ? (
        <p className="text-xs text-ink-2">
          {query.data?.reason ?? 'Недостаточно данных для расчёта'}
        </p>
      ) : (
        <PairResult result={query.data} />
      )}
    </div>
  )
}

function AssetPicker({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  const [term, setTerm] = useState('')
  const [name, setName] = useState('')
  const [open, setOpen] = useState(false)

  const { data: found = [], isFetching } = useQuery({
    queryKey: ['asset-search', term],
    queryFn: () =>
      api.get<AssetSearchResult[]>(`/assets/search${qs({ q: term, limit: 8 })}`),
    enabled: term.trim().length >= 2,
    staleTime: 300_000,
  })

  const choose = (item: AssetSearchResult) => {
    onChange(item.secid)
    setName(item.short_name ?? item.name ?? '')
    setTerm('')
    setOpen(false)
  }

  const change = (raw: string) => {
    const next = raw.toUpperCase()
    onChange(next)
    setName('')
    setTerm(raw)
    setOpen(true)
  }

  return (
    <label className="relative block">
      <span className="mb-1 block text-2xs uppercase tracking-wide text-muted">{label}</span>
      <input
        value={value}
        onChange={(event) => change(event.target.value)}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        placeholder="SBER или Сбербанк"
        className={inputClass}
        autoComplete="off"
      />
      {name && <span className="mt-0.5 block truncate text-2xs text-muted">{name}</span>}

      {open && term.trim().length >= 2 && (
        <ul className="absolute z-20 mt-1 max-h-52 w-full overflow-y-auto rounded-md border border-line bg-surface shadow-lg">
          {found.map((item) => (
            <li key={item.secid}>
              <button
                type="button"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => choose(item)}
                className="flex w-full items-center justify-between gap-2 px-2 py-1.5 text-left text-2xs hover:bg-raised"
              >
                <span className="min-w-0 truncate text-ink">
                  {item.short_name ?? item.name ?? item.secid}
                </span>
                <span className="shrink-0 text-muted">{item.secid}</span>
              </button>
            </li>
          ))}
          {!found.length && (
            <li className="px-2 py-1.5 text-2xs text-muted">
              {isFetching ? 'Ищу на Мосбирже…' : 'Ничего не найдено'}
            </li>
          )}
        </ul>
      )}
    </label>
  )
}

function PairResult({ result }: { result: CorrelationPairResult }) {
  const correlation = result.correlation ?? 0
  const positive = correlation >= 0
  const left = result.left
  const right = result.right

  return (
    <div className="space-y-3">
      <div className="rounded-card border border-line bg-raised px-4 py-3">
        <div className="text-2xs uppercase tracking-wide text-muted">Корреляция</div>
        <div className="mt-1 flex flex-wrap items-baseline gap-2">
          <span
            className="tnum text-3xl font-semibold"
            style={{ color: positive ? 'var(--critical)' : 'var(--good-text)' }}
          >
            {signedNumber(correlation)}
          </span>
          <span className="text-xs text-ink-2">{correlationStrength(correlation)}</span>
        </div>
        <div className="mt-1 text-2xs text-muted">
          {fmtDate(result.start)} — {fmtDate(result.end)} · {result.observations ?? 0} общих
          наблюдений
          {result.min_observations ? ` (минимум ${result.min_observations})` : ''}
        </div>
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        <div className="rounded border border-line px-3 py-2">
          <div className="text-2xs text-muted">Ковариация за день</div>
          <div className="mt-0.5 tnum font-medium text-ink">
            {num(result.covariance_pct2, 3)} %²
          </div>
        </div>
        <div className="rounded border border-line px-3 py-2">
          <div className="text-2xs text-muted">Ковариация за год (×252)</div>
          <div className="mt-0.5 tnum font-medium text-ink">
            {num(result.covariance_annual_pct2, 1)} %²
          </div>
        </div>
      </div>

      <div className="space-y-2">
        {[left, right].map((asset) =>
          asset ? (
            <div key={asset.secid} className="rounded-card border border-line px-3 py-2">
              <div className="flex items-baseline justify-between gap-2">
                <div className="min-w-0 truncate text-xs font-semibold text-ink">
                  {asset.name}
                </div>
                <div className="shrink-0 text-2xs text-muted">{asset.secid}</div>
              </div>
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-2xs text-ink-2">
                <span>{asset.asset_class_label}</span>
                <span>волатильность {pct(asset.volatility, 1)}</span>
                <span>{asset.observations} дн.</span>
              </div>
            </div>
          ) : null,
        )}
      </div>

      <p className="text-2xs text-muted">
        {result.method ?? 'Дневные доходности чистых цен'}. Расчёт не зависит от того,
        есть ли бумаги в портфеле: важна только история цен за выбранный период.
      </p>
    </div>
  )
}

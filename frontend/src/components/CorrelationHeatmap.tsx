import { Fragment, useEffect, useState } from 'react'

import type { CorrelationAsset, CorrelationMatrix, CorrelationPair } from '../lib/api'
import { correlationStrength, fmtDate, pct, signedNumber } from '../lib/format'
import { cx } from './ui'

//: те же смысловые цвета, что в остальном интерфейсе:
//: красный — риск концентрации (активы движутся вместе),
//: зелёный — диверсификация (движутся в разные стороны).
const POS_RGB = '208, 59, 59'
const NEG_RGB = '27, 175, 122'

const twoDigits = new Intl.NumberFormat('ru-RU', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})
const threeDigits = new Intl.NumberFormat('ru-RU', {
  minimumFractionDigits: 3,
  maximumFractionDigits: 3,
})
const oneDigit = new Intl.NumberFormat('ru-RU', {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
})

function heatColor(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return 'transparent'
  const strength = Math.min(Math.abs(value), 1)
  if (strength < 0.02) return 'rgba(137, 135, 129, 0.16)'
  const rgb = value > 0 ? POS_RGB : NEG_RGB
  return `rgba(${rgb}, ${0.14 + strength * 0.62})`
}

function heatText(value: number | null): string {
  // цвет текста берём из темы: на насыщенной заливке чёрный читается в светлой
  // схеме, белый — в тёмной; числа в ячейках всё равно остаются главным сигналом
  return value === null || !Number.isFinite(value)
    ? 'var(--text-muted)'
    : 'var(--text-primary)'
}

function pairKey(i: number, j: number): { i: number; j: number } {
  return { i: Math.min(i, j), j: Math.max(i, j) }
}

export default function CorrelationHeatmap({ data }: { data: CorrelationMatrix }) {
  const assets = data.assets ?? []
  const matrix = data.correlation ?? []
  const pairs = data.pairs ?? []
  const [selected, setSelected] = useState<{ i: number; j: number } | null>(null)

  // при смене периода индексы строк и столбцов уже другие
  useEffect(() => {
    setSelected(null)
  }, [data])

  useEffect(() => {
    if (!selected) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSelected(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selected])

  const pair = selected
    ? pairs.find((item) => item.i === selected.i && item.j === selected.j)
    : undefined

  const openPair = (i: number, j: number) => {
    if (i === j) return
    const value = matrix[i]?.[j]
    if (value === null || value === undefined) return
    setSelected(pairKey(i, j))
  }

  return (
    <>
      <div className="overflow-x-auto">
        <div
          className="grid gap-px"
          style={{
            gridTemplateColumns: `128px repeat(${assets.length}, 56px)`,
          }}
        >
          <div />
          {assets.map((asset) => (
            <div
              key={`head-${asset.asset_id}`}
              className="min-w-0 truncate px-1 pb-1 text-center text-2xs font-medium text-muted"
              title={asset.name}
            >
              {asset.name}
            </div>
          ))}

          {assets.map((row, i) => (
            <Fragment key={row.asset_id}>
              <div
                className="flex min-w-0 items-center gap-1 pr-2 text-2xs text-ink-2"
                title={row.name}
              >
                <span className="truncate font-medium">{row.name}</span>
                <span className="shrink-0 text-muted">{pct(row.weight, 0)}</span>
              </div>

              {assets.map((column, j) => {
                const value = matrix[i]?.[j] ?? null
                if (i === j) {
                  return (
                    <div
                      key={column.asset_id}
                      className="flex h-10 items-center justify-center rounded-sm bg-raised text-2xs text-muted"
                      title={`${row.name} — сам с собой`}
                    >
                      1,00
                    </div>
                  )
                }
                return (
                  <button
                    key={column.asset_id}
                    type="button"
                    disabled={value === null}
                    onClick={() => openPair(i, j)}
                    title={
                      value === null
                        ? `${row.name} × ${column.name}: недостаточно общих наблюдений`
                        : `${row.name} × ${column.name}: ${signedNumber(value)}`
                    }
                    className={cx(
                      'flex h-10 items-center justify-center rounded-sm text-2xs font-medium tnum',
                      value === null
                        ? 'cursor-default opacity-40'
                        : 'cursor-pointer transition-opacity hover:opacity-80 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--series-1)]',
                    )}
                    style={{ backgroundColor: heatColor(value), color: heatText(value) }}
                  >
                    {value === null ? '—' : twoDigits.format(value)}
                  </button>
                )
              })}
            </Fragment>
          ))}
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-muted">
        <span>−1</span>
        <span
          className="h-2 w-28 rounded-full"
          style={{
            background: `linear-gradient(to right, rgba(${NEG_RGB}, 0.78), rgba(137, 135, 129, 0.18), rgba(${POS_RGB}, 0.78))`,
          }}
        />
        <span>+1</span>
        <span>зелёный — движутся в разные стороны, красный — вместе</span>
      </div>

      <p className="mt-2 text-2xs text-muted">
        {data.method ?? 'Дневные доходности за выбранный период'}. Нажмите на ячейку пары,
        чтобы увидеть корреляцию и ковариацию. Диагональ — актив сам с собой.
      </p>

      {((data.truncated && data.total_assets) || data.excluded_no_history) && (
        <p className="mt-1 text-2xs text-muted">
          {data.truncated && data.total_assets
            ? `В матрицу вошли ${assets.length} из ${data.total_assets} позиций. `
            : ''}
          {data.excluded_no_history
            ? `Без достаточной истории осталось: ${data.excluded_no_history}.`
            : ''}
        </p>
      )}

      {selected && pair && (
        <PairDialog
          left={assets[selected.i]}
          right={assets[selected.j]}
          pair={pair}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  )
}

function PairDialog({
  left,
  right,
  pair,
  onClose,
}: {
  left: CorrelationAsset | undefined
  right: CorrelationAsset | undefined
  pair: CorrelationPair
  onClose: () => void
}) {
  if (!left || !right) return null
  const positive = pair.correlation >= 0

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4"
      role="presentation"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="correlation-dialog-title"
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-card border border-line bg-surface p-5 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h3
              id="correlation-dialog-title"
              className="text-sm font-semibold text-ink"
            >
              {left.name} × {right.name}
            </h3>
            <p className="mt-0.5 truncate text-2xs text-muted">
              {left.secid} · {right.secid}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Закрыть"
            className="shrink-0 rounded border border-line px-2 py-0.5 text-xs text-muted hover:bg-raised hover:text-ink"
          >
            ✕
          </button>
        </div>

        <div className="mt-4 rounded-card border border-line bg-raised px-4 py-3">
          <div className="text-2xs uppercase tracking-wide text-muted">Корреляция</div>
          <div className="mt-1 flex flex-wrap items-baseline gap-2">
            <span
              className="tnum text-3xl font-semibold"
              style={{ color: positive ? 'var(--critical)' : 'var(--good-text)' }}
            >
              {signedNumber(pair.correlation)}
            </span>
            <span className="text-xs text-ink-2">{correlationStrength(pair.correlation)}</span>
          </div>
        </div>

        <dl className="mt-4 grid gap-2 text-xs sm:grid-cols-2">
          <div className="rounded border border-line px-3 py-2">
            <dt className="text-2xs text-muted">Ковариация за день</dt>
            <dd className="mt-0.5 tnum font-medium text-ink">
              {threeDigits.format(pair.covariance_pct2)} %²
            </dd>
          </div>
          <div className="rounded border border-line px-3 py-2">
            <dt className="text-2xs text-muted">Ковариация за год (×252)</dt>
            <dd className="mt-0.5 tnum font-medium text-ink">
              {oneDigit.format(pair.covariance_annual_pct2)} %²
            </dd>
          </div>
          <div className="rounded border border-line px-3 py-2">
            <dt className="text-2xs text-muted">Общих наблюдений</dt>
            <dd className="mt-0.5 tnum font-medium text-ink">{pair.observations}</dd>
          </div>
          <div className="rounded border border-line px-3 py-2">
            <dt className="text-2xs text-muted">Период пары</dt>
            <dd className="mt-0.5 font-medium text-ink">
              {fmtDate(pair.start)} — {fmtDate(pair.end)}
            </dd>
          </div>
        </dl>

        <div className="mt-4 grid gap-2 sm:grid-cols-2">
          {[left, right].map((asset) => (
            <div key={asset.asset_id} className="rounded-card border border-line px-3 py-2">
              <div className="text-xs font-semibold text-ink">{asset.name}</div>
              <div className="truncate text-2xs text-muted" title={asset.secid}>
                {asset.secid}
              </div>
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-2xs text-ink-2">
                <span>доля {pct(asset.weight, 1)}</span>
                <span>волатильность {pct(asset.volatility, 1)}</span>
                <span>{asset.asset_class_label}</span>
              </div>
            </div>
          ))}
        </div>

        <p className="mt-4 text-2xs text-muted">
          Корреляция показывает силу связи от −1 до +1 и не зависит от масштаба.
          Ковариация — та же связь в «процентах в квадрате»: её значение зависит от
          волатильности самих бумаг, поэтому сравнивать её между разными парами сложнее.
        </p>
      </div>
    </div>
  )
}

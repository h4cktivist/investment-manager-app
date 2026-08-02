import { useMemo, useState } from 'react'

import { Sparkbar } from '../components/charts'
import {
  Badge,
  Button,
  Card,
  ErrorBox,
  Money,
  SegmentedControl,
  Spinner,
  cx,
} from '../components/ui'
import { usePositions } from '../hooks/useApi'
import { useQuotes } from '../hooks/useQuotes'
import type { Position } from '../lib/api'
import { CLASS_LABELS, fmtDate, num, pct, pctRaw, relativeTime, rub } from '../lib/format'

type SortKey = 'market_value' | 'unrealized_pnl' | 'weight' | 'day_change' | 'secid' | 'total_pnl'

const COLUMNS: { key: SortKey; label: string; align?: 'right' }[] = [
  { key: 'secid', label: 'Инструмент' },
  { key: 'weight', label: 'Доля', align: 'right' },
  { key: 'market_value', label: 'Стоимость', align: 'right' },
  { key: 'unrealized_pnl', label: 'Бумажная прибыль', align: 'right' },
  { key: 'total_pnl', label: 'Итого с выплатами', align: 'right' },
  { key: 'day_change', label: 'За день', align: 'right' },
]

export default function PositionsPage() {
  const [includeClosed, setIncludeClosed] = useState(false)
  const [filter, setFilter] = useState<string>('ALL')
  const [sort, setSort] = useState<SortKey>('market_value')
  const [search, setSearch] = useState('')
  const { data = [], isLoading, error } = usePositions(includeClosed)
  const { quotes } = useQuotes()

  const classes = useMemo(() => {
    const set = new Map<string, number>()
    for (const p of data) set.set(p.asset_class, (set.get(p.asset_class) ?? 0) + 1)
    return [...set.entries()]
  }, [data])

  const rows = useMemo(() => {
    const term = search.trim().toLowerCase()
    let out = data.filter((p) => {
      if (filter !== 'ALL' && p.asset_class !== filter) return false
      if (!term) return true
      return (
        p.secid.toLowerCase().includes(term) ||
        p.name.toLowerCase().includes(term) ||
        p.full_name.toLowerCase().includes(term)
      )
    })
    out = [...out].sort((a, b) => {
      if (sort === 'secid') return a.secid.localeCompare(b.secid)
      return (b[sort] as number) - (a[sort] as number)
    })
    return out
  }, [data, filter, search, sort])

  const totals = useMemo(
    () => ({
      value: rows.reduce((s, p) => s + p.market_value, 0),
      unrealized: rows.reduce((s, p) => s + p.unrealized_pnl, 0),
      total: rows.reduce((s, p) => s + p.total_pnl, 0),
      day: rows.reduce((s, p) => s + p.day_change, 0),
    }),
    [rows],
  )
  const maxValue = Math.max(...rows.map((p) => p.market_value), 1)

  if (isLoading) return <Spinner />
  if (error) return <ErrorBox error={error} />

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold text-ink">Позиции</h1>
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Поиск по тикеру или названию"
            className="w-56 rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-ink placeholder:text-muted focus:border-[var(--series-1)] focus:outline-none"
          />
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
          <Button
            variant={includeClosed ? 'primary' : 'ghost'}
            onClick={() => setIncludeClosed((v) => !v)}
          >
            Закрытые
          </Button>
        </div>
      </div>

      <Card bodyClassName="p-0">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1000px] text-xs">
            <thead className="sticky top-0 bg-surface">
              <tr className="border-b border-line text-left text-2xs uppercase text-muted">
                {COLUMNS.map((col) => (
                  <th
                    key={col.key}
                    onClick={() => setSort(col.key)}
                    className={cx(
                      'cursor-pointer select-none px-3 py-2 font-medium hover:text-ink',
                      col.align === 'right' && 'text-right',
                      sort === col.key && 'text-ink',
                    )}
                  >
                    {col.label}
                    {sort === col.key && <span aria-hidden> ↓</span>}
                  </th>
                ))}
                <th className="px-3 py-2 text-right font-medium">Средняя / текущая</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <PositionRow key={p.asset_id} position={p} maxValue={maxValue} live={quotes[p.secid]} />
              ))}
              {!rows.length && (
                <tr>
                  <td colSpan={7} className="px-3 py-10 text-center text-muted">
                    Ничего не найдено
                  </td>
                </tr>
              )}
            </tbody>
            {rows.length > 0 && (
              <tfoot>
                <tr className="border-t border-line font-medium">
                  <td className="px-3 py-2 text-ink">Итого · {rows.length}</td>
                  <td />
                  <td className="tnum px-3 py-2 text-right text-ink">{rub(totals.value, 0)}</td>
                  <td className="px-3 py-2 text-right">
                    <Money value={totals.unrealized} render={(v) => rub(v, 0)} />
                  </td>
                  <td className="px-3 py-2 text-right">
                    <Money value={totals.total} render={(v) => rub(v, 0)} />
                  </td>
                  <td className="px-3 py-2 text-right">
                    <Money value={totals.day} render={(v) => rub(v, 0)} />
                  </td>
                  <td />
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      </Card>
    </div>
  )
}

function PositionRow({
  position: p,
  maxValue,
  live,
}: {
  position: Position
  maxValue: number
  live?: { price_norm: number; change_pct: number | null; updated_at: string; is_delayed: boolean }
}) {
  // живая котировка из WebSocket перекрывает значение из последнего ответа API
  const price = live?.price_norm ?? p.last_price
  const drift = p.quantity * (price - p.last_price)
  const marketValue = p.market_value + drift
  const unrealized = p.unrealized_pnl + drift

  return (
    <tr className={cx('border-b border-line/60', !p.is_open && 'opacity-60')}>
      <td className="px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="font-medium text-ink">{p.name}</span>
          {!p.is_open && <Badge>закрыта</Badge>}
          {p.is_custom && <Badge tone="info">свой актив</Badge>}
          {!p.has_market_price && <Badge tone="warn">нет котировки</Badge>}
        </div>
        <div className="text-2xs text-muted">
          {p.secid} · {p.asset_class_label}
          {p.maturity_date && ` · погашение ${fmtDate(p.maturity_date)}`}
        </div>
      </td>
      <td className="px-3 py-2 text-right">
        <div className="tnum text-ink-2">{pctRaw(p.weight * 100, 1)}</div>
        <div className="mt-1 flex justify-end">
          <span className="w-16">
            <Sparkbar value={p.market_value} max={maxValue} />
          </span>
        </div>
      </td>
      <td className="px-3 py-2 text-right">
        <div className="tnum font-medium text-ink">{rub(marketValue, 0)}</div>
        <div className="tnum text-2xs text-muted">{num(p.quantity, 4)} шт.</div>
      </td>
      <td className="px-3 py-2 text-right">
        <Money value={unrealized} render={(v) => rub(v, 0)} />
        <div className="text-2xs text-muted">
          {pct(p.cost_basis > 0 ? unrealized / p.cost_basis : null)}
        </div>
      </td>
      <td className="px-3 py-2 text-right">
        <Money value={p.total_pnl + drift} render={(v) => rub(v, 0)} />
        {p.income > 0 && (
          <div className="text-2xs text-muted">выплаты {rub(p.income, 0)}</div>
        )}
      </td>
      <td className="px-3 py-2 text-right">
        <Money value={p.day_change} render={(v) => rub(v, 0)} />
        {live?.change_pct != null && (
          <div className="tnum text-2xs text-muted">{pctRaw(live.change_pct, 2)}</div>
        )}
      </td>
      <td className="px-3 py-2 text-right">
        <div className="tnum text-ink-2">
          {rub(p.avg_price)} → <span className="font-medium text-ink">{rub(price)}</span>
        </div>
        <div className="text-2xs text-muted">
          {live ? `обновлено ${relativeTime(live.updated_at)}` : relativeTime(p.quote_updated_at)}
          {(live?.is_delayed ?? p.quote_delayed) && ' · задержка ~15 мин'}
        </div>
      </td>
    </tr>
  )
}

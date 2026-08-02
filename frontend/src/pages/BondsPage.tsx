import { useMemo, useState } from 'react'

import { Badge, Card, ErrorBox, Money, SegmentedControl, Spinner, StatTile, cx } from '../components/ui'
import { useBonds } from '../hooks/useApi'
import type { BondItem } from '../lib/api'
import { daysWord, fmtDate, num, pct, pctRaw, rub } from '../lib/format'

type SortKey = 'market_value' | 'effective_yield' | 'duration_days' | 'days_to_maturity'

const SORTS: { value: SortKey; label: string }[] = [
  { value: 'market_value', label: 'По стоимости' },
  { value: 'effective_yield', label: 'По доходности' },
  { value: 'duration_days', label: 'По дюрации' },
  { value: 'days_to_maturity', label: 'По сроку' },
]

export default function BondsPage() {
  const { data, isLoading, error } = useBonds()
  const [sort, setSort] = useState<SortKey>('market_value')

  const items = useMemo(() => {
    if (!data?.items) return []
    return [...data.items].sort((a, b) => (Number(b[sort]) || 0) - (Number(a[sort]) || 0))
  }, [data, sort])

  if (isLoading) return <Spinner />
  if (error) return <ErrorBox error={error} />
  if (!data) return null

  const s = data.summary
  const hasEstimates = items.some((i) => i.is_estimate)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-ink">Облигации</h1>
          <p className="text-2xs text-muted">
            Доходности считаются от «грязной» цены (чистая + НКД) по полному графику купонов,
            амортизаций и погашения
          </p>
        </div>
        <SegmentedControl size="xs" value={sort} onChange={setSort} options={SORTS} />
      </div>

      {!items.length ? (
        <Card>
          <p className="py-10 text-center text-xs text-muted">Облигаций в портфеле нет</p>
        </Card>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <StatTile large label="Стоимость облигаций" value={rub(s.market_value, 0)} hint={`${s.positions} выпусков`} />
            <StatTile
              large
              label="Эффективная доходность"
              value={pct(s.weighted_effective_yield)}
              hint="средневзвешенная по рыночной стоимости"
            />
            <StatTile label="Текущая доходность" value={pct(s.weighted_current_yield)} hint="купоны за год к цене" />
            <StatTile
              label="Дюрация портфеля"
              value={s.weighted_duration_days ? daysWord(s.weighted_duration_days) : '—'}
              hint={
                s.weighted_duration_years ? `${num(s.weighted_duration_years, 2)} года` : undefined
              }
            />
          </div>

          <Card bodyClassName="p-0">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1180px] text-xs">
                <thead>
                  <tr className="border-b border-line text-left text-2xs uppercase text-muted">
                    <th className="px-3 py-2 font-medium">Выпуск</th>
                    <th className="px-3 py-2 text-right font-medium">Кол-во</th>
                    <th className="px-3 py-2 text-right font-medium">Цена, % / НКД</th>
                    <th className="px-3 py-2 text-right font-medium">Стоимость</th>
                    <th className="px-3 py-2 text-right font-medium" title="Годовой купон к чистой цене">
                      Текущая
                    </th>
                    <th className="px-3 py-2 text-right font-medium" title="Простая доходность к погашению">
                      К погашению
                    </th>
                    <th className="px-3 py-2 text-right font-medium" title="С реинвестированием купонов">
                      Эффективная
                    </th>
                    <th className="px-3 py-2 text-right font-medium">Дюрация</th>
                    <th className="px-3 py-2 text-right font-medium">Погашение</th>
                    <th className="px-3 py-2 text-right font-medium">Прибыль</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((bond) => (
                    <BondRow key={bond.asset_id} bond={bond} />
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title="Как читать эти доходности" subtitle="Все ставки — годовые">
            <dl className="grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
              <Definition term="Текущая доходность">
                Купонные выплаты за ближайшие 12 месяцев, делённые на чистую цену. Показывает
                денежный поток, но игнорирует возврат номинала.
              </Definition>
              <Definition term="Доходность к погашению (простая)">
                Весь остаток денежного потока минус грязная цена, приведённый к году. Без
                реинвестирования купонов.
              </Definition>
              <Definition term="Эффективная доходность">
                Ставка, при которой дисконтированный поток купонов, амортизаций и погашения равен
                грязной цене. Это то, что Мосбиржа показывает как «доходность».
              </Definition>
              <Definition term="Дюрация Маколея">
                Средневзвешенный срок возврата вложенных денег. Чем больше, тем сильнее цена
                отреагирует на изменение ставок.
              </Definition>
            </dl>
            {hasEstimates && (
              <p className="mt-3 text-2xs text-muted">
                Отметка «оценка» — у флоатера будущие купоны ещё не объявлены, они спрогнозированы
                по последнему известному значению.
              </p>
            )}
          </Card>
        </>
      )}
    </div>
  )
}

function BondRow({ bond }: { bond: BondItem }) {
  const soon = bond.days_to_maturity !== null && bond.days_to_maturity < 180
  return (
    <tr className="border-b border-line/60">
      <td className="px-3 py-2">
        <div className="flex items-center gap-1.5">
          <span className="font-medium text-ink">{bond.name}</span>
          {bond.is_estimate && <Badge tone="warn">оценка</Badge>}
          {bond.offer_date && <Badge tone="info">оферта {fmtDate(bond.offer_date)}</Badge>}
        </div>
        <div className="text-2xs text-muted">
          {bond.secid} · номинал {rub(bond.face_value, 0)}
          {bond.coupon_rate ? ` · купон ${pctRaw(bond.coupon_rate, 2)}` : ''}
          {bond.coupons_per_year ? ` · ${bond.coupons_per_year} раз в год` : ''}
        </div>
      </td>
      <td className="tnum px-3 py-2 text-right text-ink-2">{num(bond.quantity, 0)}</td>
      <td className="px-3 py-2 text-right">
        <div className="tnum text-ink-2">{num(bond.clean_price_pct, 2)}%</div>
        <div className="tnum text-2xs text-muted">+{rub(bond.accrued_interest)}</div>
      </td>
      <td className="tnum px-3 py-2 text-right font-medium text-ink">{rub(bond.market_value, 0)}</td>
      <td className="tnum px-3 py-2 text-right text-ink-2">{pct(bond.current_yield)}</td>
      <td className="tnum px-3 py-2 text-right text-ink-2">{pct(bond.simple_yield)}</td>
      <td className="tnum px-3 py-2 text-right font-semibold text-ink">{pct(bond.effective_yield)}</td>
      <td className="px-3 py-2 text-right">
        <div className="tnum text-ink-2">{bond.duration_days ? `${bond.duration_days} дн.` : '—'}</div>
        {bond.modified_duration !== null && (
          <div className="tnum text-2xs text-muted" title="Модифицированная дюрация">
            мод. {num(bond.modified_duration, 2)}
          </div>
        )}
      </td>
      <td className="px-3 py-2 text-right">
        <div className={cx('tnum', soon ? 'text-[var(--warning)]' : 'text-ink-2')}>
          {fmtDate(bond.maturity_date)}
        </div>
        <div className="text-2xs text-muted">
          {bond.days_to_maturity !== null ? daysWord(bond.days_to_maturity) : '—'}
        </div>
      </td>
      <td className="px-3 py-2 text-right">
        <Money value={bond.unrealized_pnl} render={(v) => rub(v, 0)} />
        {bond.next_payment_date && (
          <div className="text-2xs text-muted">
            купон {fmtDate(bond.next_payment_date)}
          </div>
        )}
      </td>
    </tr>
  )
}

function Definition({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="font-medium text-ink">{term}</dt>
      <dd className="mt-0.5 text-ink-2">{children}</dd>
    </div>
  )
}

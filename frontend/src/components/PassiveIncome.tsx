import type { PassiveIncome } from '../lib/api'
import { pct, rub, rubShort } from '../lib/format'
import { seriesColor, useViz } from '../lib/viz'
import { MonthlyBars, Sparkbar } from './charts'
import { Badge, Card } from './ui'

/**
 * Сколько портфель принесёт деньгами за ближайший год.
 * Возврат тела долга (амортизации и погашения) сюда не входит — он показан
 * отдельной строкой, иначе доходность выглядела бы кратно выше реальной.
 */
export default function PassiveIncomeCard({ data }: { data: PassiveIncome }) {
  const viz = useViz()
  const hasIncome = data.total > 0

  return (
    <Card
      title="Пассивный доход"
      subtitle={`Ожидаемые купоны и дивиденды за ${data.horizon_months} месяцев по текущим позициям`}
      actions={
        data.forecast_share > 0.01 ? (
          <Badge tone="warn">
            {pct(data.forecast_share, 0)} прогноз по флоатерам
          </Badge>
        ) : undefined
      }
    >
      {!hasIncome ? (
        <p className="py-8 text-center text-xs text-muted">
          По текущим позициям выплат в ближайший год не ожидается. Если в портфеле есть
          облигации, проверьте, что загружен купонный график — кнопка «Обновить всё с биржи»
          на странице «Активы».
        </p>
      ) : (
        <>
          <div className="grid gap-5 lg:grid-cols-[minmax(220px,1fr)_2fr]">
            <div>
              <div className="text-2xs uppercase tracking-wide text-muted">
                За 12 месяцев
              </div>
              <div className="mt-1 text-3xl font-semibold text-ink">{rub(data.total, 0)}</div>
              <div className="mt-1 text-xs text-ink-2">
                <span className="tnum font-medium">{rub(data.per_month, 0)}</span> в месяц в среднем
              </div>

              <dl className="mt-4 space-y-2">
                <Metric
                  term="К рыночной стоимости"
                  value={pct(data.yield_on_market)}
                  hint="сколько процентов от текущей цены портфеля вернётся деньгами"
                />
                <Metric
                  term="К вложенной сумме"
                  value={pct(data.yield_on_cost)}
                  hint="то же к себестоимости позиций"
                />
                <Metric
                  term="Возврат тела долга"
                  value={rub(data.principal_repayments, 0)}
                  hint="амортизации и погашения — это не доход"
                />
              </dl>

              <div className="mt-4 space-y-1.5 border-t border-line pt-3">
                <SplitRow
                  color={seriesColor(viz, 0)}
                  label="Купоны"
                  amount={data.coupons}
                  total={data.total}
                />
                <SplitRow
                  color={seriesColor(viz, 1)}
                  label="Дивиденды"
                  amount={data.dividends}
                  total={data.total}
                />
                {data.other_income > 0 && (
                  <SplitRow
                    color={seriesColor(viz, 2)}
                    label="Проценты по вкладам"
                    amount={data.other_income}
                    total={data.total}
                  />
                )}
              </div>
            </div>

            <div>
              <div className="mb-1 text-2xs uppercase tracking-wide text-muted">
                Ожидаемые выплаты по месяцам
              </div>
              <MonthlyBars
                data={data.by_month}
                color={seriesColor(viz, 3)}
                height={230}
                emptyLabel="Нет ожидаемых выплат"
              />
            </div>
          </div>

          {data.top_assets.length > 0 && (
            <div className="mt-5 border-t border-line pt-4">
              <div className="mb-2 text-2xs uppercase tracking-wide text-muted">
                Кто приносит доход
              </div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[620px] text-xs">
                  <thead>
                    <tr className="border-b border-line text-left text-2xs uppercase text-muted">
                      <th className="py-1.5 pr-3 font-medium">Инструмент</th>
                      <th className="py-1.5 pr-3 text-right font-medium">За 12 мес.</th>
                      <th className="py-1.5 pr-3 text-right font-medium">Доля</th>
                      <th className="py-1.5 pr-3 text-right font-medium">К позиции</th>
                      <th className="py-1.5 font-medium">Вклад</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.top_assets.slice(0, 8).map((asset) => (
                      <tr key={asset.asset_id} className="border-b border-line/60">
                        <td className="py-1.5 pr-3">
                          <span className="font-medium text-ink">{asset.name}</span>
                          {asset.is_forecast && (
                            <span className="ml-1.5 text-2xs text-muted">оценка</span>
                          )}
                          <div className="text-2xs text-muted">
                            {asset.secid} · {asset.payments} выплат
                          </div>
                        </td>
                        <td className="tnum py-1.5 pr-3 text-right font-medium text-ink">
                          {rub(asset.amount, 0)}
                        </td>
                        <td className="tnum py-1.5 pr-3 text-right text-ink-2">
                          {pct(asset.share, 1)}
                        </td>
                        <td className="tnum py-1.5 pr-3 text-right text-ink-2">
                          {pct(asset.yield_on_position)}
                        </td>
                        <td className="py-1.5">
                          <Sparkbar
                            value={asset.amount}
                            max={data.top_assets[0].amount}
                            color={seriesColor(viz, 3)}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {data.top_assets.length > 8 && (
                <p className="mt-2 text-2xs text-muted">
                  Ещё {data.top_assets.length - 8} инструментов на{' '}
                  {rubShort(
                    data.top_assets.slice(8).reduce((sum, a) => sum + a.amount, 0),
                  )}
                </p>
              )}
            </div>
          )}

          <p className="mt-4 text-2xs text-muted">
            По облигациям учтён весь известный купонный график, по акциям — только уже
            объявленные дивиденды, поэтому дивидендная часть занижена. Суммы посчитаны по
            текущему количеству бумаг и не учитывают налог.
          </p>
        </>
      )}
    </Card>
  )
}

function Metric({ term, value, hint }: { term: string; value: string; hint: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <div className="min-w-0">
        <dt className="text-xs text-ink-2">{term}</dt>
        <dd className="text-2xs text-muted">{hint}</dd>
      </div>
      <span className="tnum shrink-0 text-sm font-semibold text-ink">{value}</span>
    </div>
  )
}

function SplitRow({
  color,
  label,
  amount,
  total,
}: {
  color: string
  label: string
  amount: number
  total: number
}) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span aria-hidden className="h-2 w-2 shrink-0 rounded-sm" style={{ background: color }} />
      <span className="text-ink-2">{label}</span>
      <span className="tnum ml-auto font-medium text-ink">{rub(amount, 0)}</span>
      <span className="tnum w-12 shrink-0 text-right text-muted">
        {total > 0 ? pct(amount / total, 0) : '—'}
      </span>
    </div>
  )
}

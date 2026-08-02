import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'

import { Badge, Button, Card, ErrorBox, Spinner, StatTile, cx } from '../components/ui'
import { api, type ImportPreview } from '../lib/api'
import { TX_LABELS, fmtDate, fmtDateTime, num, rub } from '../lib/format'
import { useSelection } from '../lib/store'

interface CommitResult {
  batch_id: number
  imported: number
  skipped: number
  failed: number
  created_assets: string[]
  unresolved_assets: string[]
  errors: { line: number; error: string }[]
}

interface Batch {
  id: number
  portfolio_id: number | null
  filename: string
  status: string
  total_rows: number
  imported: number
  skipped: number
  failed: number
  created_at: string
}

export default function ImportPage() {
  const { portfolios } = useSelection()
  const queryClient = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [result, setResult] = useState<CommitResult | null>(null)
  const [portfolioId, setPortfolioId] = useState<number | ''>(portfolios[0]?.id ?? '')
  const [skipDuplicates, setSkipDuplicates] = useState(true)
  const [showAllRows, setShowAllRows] = useState(false)
  const [newPortfolio, setNewPortfolio] = useState('')

  const batches = useQuery({
    queryKey: ['import-batches'],
    queryFn: () => api.get<Batch[]>('/imports/batches'),
  })

  const doPreview = useMutation({
    mutationFn: async (f: File) => {
      const form = new FormData()
      form.append('file', f)
      return api.postForm<ImportPreview>('/imports/preview', form)
    },
    onSuccess: (data) => {
      setPreview(data)
      setResult(null)
    },
  })

  const createPortfolio = useMutation({
    mutationFn: (name: string) => api.post<{ id: number }>('/portfolios', { name, kind: 'BROKER' }),
    onSuccess: (created) => {
      setPortfolioId(created.id)
      setNewPortfolio('')
      queryClient.invalidateQueries({ queryKey: ['portfolios'] })
    },
  })

  const commit = useMutation({
    mutationFn: async () => {
      if (!file || portfolioId === '') throw new Error('Выберите файл и портфель')
      const form = new FormData()
      form.append('file', file)
      form.append('portfolio_id', String(portfolioId))
      form.append('skip_duplicates', String(skipDuplicates))
      form.append('load_market_data', 'true')
      return api.postForm<CommitResult>('/imports', form)
    },
    onSuccess: (data) => {
      setResult(data)
      setPreview(null)
      setFile(null)
      if (fileRef.current) fileRef.current.value = ''
      queryClient.invalidateQueries()
    },
  })

  const rollback = useMutation({
    mutationFn: (id: number) => api.del(`/imports/batches/${id}`),
    onSuccess: () => queryClient.invalidateQueries(),
  })

  const onPick = (f: File | null) => {
    setFile(f)
    setResult(null)
    setPreview(null)
    if (f) doPreview.mutate(f)
  }

  const rowsToShow = preview ? (showAllRows ? preview.rows : preview.rows.slice(0, 40)) : []
  const warnings = preview?.rows.filter((r) => r.warnings.length) ?? []

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-ink">Импорт сделок</h1>

      <Card
        title="Файл выгрузки"
        subtitle="Ожидаемые колонки: Event, Date, Symbol, Price, Quantity, Currency, FeeTax, Exchange, NKD, FeeCurrency, DoNotAdjustCash, Note"
      >
        <div className="flex flex-wrap items-end gap-3">
          <label className="block">
            <span className="mb-1 block text-2xs uppercase tracking-wide text-muted">CSV-файл</span>
            <input
              ref={fileRef}
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => onPick(e.target.files?.[0] ?? null)}
              className="block w-72 text-xs text-ink-2 file:mr-3 file:rounded-md file:border file:border-line file:bg-surface file:px-3 file:py-1.5 file:text-xs file:text-ink hover:file:bg-raised"
            />
          </label>

          <label className="block">
            <span className="mb-1 block text-2xs uppercase tracking-wide text-muted">Портфель</span>
            <select
              value={portfolioId}
              onChange={(e) => setPortfolioId(Number(e.target.value))}
              className="w-56 rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-ink focus:border-[var(--series-1)] focus:outline-none"
            >
              <option value="">— выберите —</option>
              {portfolios.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>

          <label className="flex items-center gap-2 pb-1.5 text-xs text-ink-2">
            <input
              type="checkbox"
              checked={skipDuplicates}
              onChange={(e) => setSkipDuplicates(e.target.checked)}
            />
            пропускать дубли
          </label>
        </div>

        {!portfolios.length && (
          <div className="mt-3 flex flex-wrap items-end gap-2 border-t border-line pt-3">
            <label className="block">
              <span className="mb-1 block text-2xs uppercase tracking-wide text-muted">
                Портфелей ещё нет — создайте первый
              </span>
              <input
                value={newPortfolio}
                onChange={(e) => setNewPortfolio(e.target.value)}
                placeholder="Брокерский счёт"
                className="w-56 rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-ink placeholder:text-muted focus:border-[var(--series-1)] focus:outline-none"
              />
            </label>
            <Button
              variant="primary"
              disabled={!newPortfolio.trim() || createPortfolio.isPending}
              onClick={() => createPortfolio.mutate(newPortfolio.trim())}
            >
              Создать портфель
            </Button>
          </div>
        )}
      </Card>

      {doPreview.isPending && <Spinner label="Разбираю файл…" />}
      {doPreview.error && <ErrorBox error={doPreview.error} />}
      {commit.error && <ErrorBox error={commit.error} />}

      {preview && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <StatTile large label="Строк разобрано" value={num(preview.parsed, 0)} hint={`из ${preview.total_lines}`} />
            <StatTile
              label="Ошибки разбора"
              value={num(preview.errors.length, 0)}
              hint={preview.errors.length ? 'эти строки будут пропущены' : 'нет'}
            />
            <StatTile
              label="Уже в базе"
              value={num(preview.duplicates, 0)}
              hint={skipDuplicates ? 'будут пропущены' : 'будут добавлены повторно'}
            />
            <StatTile
              label="Новых инструментов"
              value={num(preview.new_symbols.length, 0)}
              hint="подтянутся с Мосбиржи"
            />
          </div>

          <Card title="Состав файла">
            <div className="flex flex-wrap gap-2">
              {Object.entries(preview.summary).map(([type, count]) => (
                <span
                  key={type}
                  className="rounded-md border border-line px-2.5 py-1 text-xs text-ink-2"
                >
                  {TX_LABELS[type] ?? type}: <span className="tnum font-medium text-ink">{count}</span>
                </span>
              ))}
            </div>
            {preview.new_symbols.length > 0 && (
              <p className="mt-3 text-2xs text-muted">
                Новые тикеры: {preview.new_symbols.join(', ')}
              </p>
            )}
          </Card>

          {warnings.length > 0 && (
            <Card
              title={`Строки, требующие внимания (${warnings.length})`}
              subtitle="Импорт пройдёт, но эти суммы стоит перепроверить"
            >
              <ul className="space-y-1.5 text-xs">
                {warnings.slice(0, 20).map((row) => (
                  <li key={row.line} className="flex flex-wrap items-baseline gap-2">
                    <span className="tnum text-muted">стр. {row.line}</span>
                    <span className="font-medium text-ink">{row.symbol}</span>
                    <span className="text-ink-2">{row.warnings.join('; ')}</span>
                    <span className="tnum text-muted">→ {rub(row.amount)}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {preview.errors.length > 0 && (
            <Card title="Ошибки разбора">
              <ul className="space-y-1 text-xs text-[var(--critical)]">
                {preview.errors.slice(0, 20).map((e, i) => (
                  <li key={i}>
                    стр. {e.line}: {e.error}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <Card
            title="Предпросмотр операций"
            subtitle="Суммы выплат определены автоматически — в колонке «правило» видно, как именно"
            bodyClassName="p-0"
            actions={
              <Button
                variant="primary"
                disabled={portfolioId === '' || commit.isPending}
                onClick={() => commit.mutate()}
              >
                {commit.isPending ? 'Импортирую…' : `Импортировать ${preview.parsed} операций`}
              </Button>
            }
          >
            <div className="max-h-[520px] overflow-auto">
              <table className="w-full min-w-[900px] text-xs">
                <thead className="sticky top-0 bg-surface">
                  <tr className="border-b border-line text-left text-2xs uppercase text-muted">
                    <th className="px-3 py-2 font-medium">Стр.</th>
                    <th className="px-3 py-2 font-medium">Дата</th>
                    <th className="px-3 py-2 font-medium">Операция</th>
                    <th className="px-3 py-2 font-medium">Тикер</th>
                    <th className="px-3 py-2 text-right font-medium">Цена</th>
                    <th className="px-3 py-2 text-right font-medium">Кол-во</th>
                    <th className="px-3 py-2 text-right font-medium">Сумма</th>
                    <th className="px-3 py-2 font-medium">Правило / примечание</th>
                  </tr>
                </thead>
                <tbody>
                  {rowsToShow.map((row) => (
                    <tr
                      key={`${row.line}-${row.external_id}`}
                      className={cx('border-b border-line/60', row.is_duplicate && 'opacity-50')}
                    >
                      <td className="tnum px-3 py-1.5 text-muted">{row.line}</td>
                      <td className="tnum whitespace-nowrap px-3 py-1.5 text-ink-2">
                        {fmtDate(row.executed_at)}
                      </td>
                      <td className="px-3 py-1.5">
                        <Badge>{TX_LABELS[row.type] ?? row.type}</Badge>
                        {row.is_duplicate && <span className="ml-1 text-2xs text-muted">дубль</span>}
                      </td>
                      <td className="px-3 py-1.5 font-medium text-ink">{row.symbol ?? '—'}</td>
                      <td className="tnum px-3 py-1.5 text-right text-ink-2">{num(row.price, 4)}</td>
                      <td className="tnum px-3 py-1.5 text-right text-ink-2">{num(row.quantity, 4)}</td>
                      <td className="tnum px-3 py-1.5 text-right font-medium text-ink">
                        {rub(row.amount)}
                      </td>
                      <td className="px-3 py-1.5 text-2xs text-muted">
                        {row.warnings.length > 0 && (
                          <span className="text-[var(--warning)]">{row.warnings.join('; ')} </span>
                        )}
                        {row.amount_rule}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!showAllRows && preview.rows.length > 40 && (
              <button
                onClick={() => setShowAllRows(true)}
                className="w-full border-t border-line py-2 text-2xs text-[var(--series-1)] hover:bg-raised"
              >
                показать все {preview.rows.length} строк
              </button>
            )}
          </Card>
        </>
      )}

      {result && (
        <Card title="Импорт завершён">
          <div className="grid gap-3 sm:grid-cols-4">
            <StatTile large label="Добавлено" value={num(result.imported, 0)} />
            <StatTile label="Пропущено дублей" value={num(result.skipped, 0)} />
            <StatTile label="Ошибок" value={num(result.failed, 0)} />
            <StatTile label="Новых инструментов" value={num(result.created_assets.length, 0)} />
          </div>
          {result.unresolved_assets.length > 0 && (
            <p className="mt-3 text-xs text-[var(--warning)]">
              Не найдены на бирже: {result.unresolved_assets.join(', ')}. Они заведены как кастомные
              активы — задайте им цену вручную на странице «Активы».
            </p>
          )}
          <p className="mt-3 text-2xs text-muted">
            История цен и купонные графики догружаются в фоне — метрики и календарь наполнятся в
            течение пары минут.
          </p>
        </Card>
      )}

      <Card title="История импортов" bodyClassName="p-0">
        {batches.isLoading ? (
          <Spinner />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-xs">
              <thead>
                <tr className="border-b border-line text-left text-2xs uppercase text-muted">
                  <th className="px-3 py-2 font-medium">Когда</th>
                  <th className="px-3 py-2 font-medium">Файл</th>
                  <th className="px-3 py-2 text-right font-medium">Строк</th>
                  <th className="px-3 py-2 text-right font-medium">Добавлено</th>
                  <th className="px-3 py-2 text-right font-medium">Пропущено</th>
                  <th className="px-3 py-2 font-medium">Статус</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {(batches.data ?? []).map((batch) => (
                  <tr key={batch.id} className="border-b border-line/60">
                    <td className="tnum whitespace-nowrap px-3 py-2 text-ink-2">
                      {fmtDateTime(batch.created_at)}
                    </td>
                    <td className="max-w-[280px] truncate px-3 py-2 text-ink" title={batch.filename}>
                      {batch.filename}
                    </td>
                    <td className="tnum px-3 py-2 text-right text-ink-2">{batch.total_rows}</td>
                    <td className="tnum px-3 py-2 text-right text-ink">{batch.imported}</td>
                    <td className="tnum px-3 py-2 text-right text-muted">{batch.skipped}</td>
                    <td className="px-3 py-2">
                      <Badge tone={batch.status === 'DONE' ? 'good' : 'bad'}>
                        {batch.status === 'DONE' ? 'успешно' : 'с ошибками'}
                      </Badge>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <button
                        onClick={() => {
                          if (confirm(`Откатить импорт и удалить ${batch.imported} операций?`))
                            rollback.mutate(batch.id)
                        }}
                        className="text-2xs text-[var(--critical)] hover:underline"
                      >
                        откатить
                      </button>
                    </td>
                  </tr>
                ))}
                {!batches.data?.length && (
                  <tr>
                    <td colSpan={7} className="px-3 py-8 text-center text-muted">
                      Импортов ещё не было
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Как определяется сумма выплаты" subtitle="Про особенность формата выгрузок">
        <p className="text-xs text-ink-2">
          В выгрузке колонка <code className="text-ink">Quantity</code> у дивидендов и купонов
          заполнена непоследовательно: иногда это количество бумаг, иногда сама сумма выплаты, а
          иногда сумма продублирована в <code className="text-ink">Price</code>. Импортёр выбирает
          трактовку, сверяясь с количеством бумаг в позиции на дату выплаты (на начало дня — купон
          часто приходит в день продажи). Применённое правило видно в предпросмотре и сохраняется в
          примечании к операции.
        </p>
        <p className="mt-2 text-xs text-ink-2">
          Событие <code className="text-ink">DIVIDEND</code> по облигации автоматически становится
          купоном, <code className="text-ink">AMORTISATION</code> уменьшает себестоимость позиции, а
          <code className="text-ink"> SPLIT</code> пересчитывает количество и среднюю цену.
        </p>
        <p className="mt-2 text-xs text-ink-2">
          Пополнения счёта в выгрузке отсутствуют, поэтому недостающие для покупок деньги
          учитываются как внешние вводы в дату сделки — без этого невозможно посчитать TWR и XIRR.
        </p>
      </Card>
    </div>
  )
}

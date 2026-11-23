const BASE = '/api'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') detail = body.detail
      else if (Array.isArray(body.detail)) detail = body.detail.map((d: any) => d.msg).join('; ')
    } catch {
      /* тело не JSON — оставляем статус */
    }
    throw new ApiError(detail, res.status)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  postForm: <T>(path: string, form: FormData) =>
    request<T>(path, { method: 'POST', body: form }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}

export function qs(params: Record<string, unknown>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) value.forEach((v) => search.append(key, String(v)))
    else search.append(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

// ----------------------------------------------------------------- типы

export interface Portfolio {
  id: number
  name: string
  kind: string
  base_currency: string
  broker: string | null
  opened_at: string | null
  note: string | null
  is_archived: boolean
  total_value: number
  total_pnl: number
  total_pnl_pct: number | null
  day_change: number
  positions_count: number
}

export interface Summary {
  market_value: number
  cash: number
  total_value: number
  cost_basis: number
  net_invested: number
  unrealized_pnl: number
  unrealized_pnl_pct: number | null
  realized_pnl: number
  dividends: number
  coupons: number
  interest: number
  amortization: number
  income: number
  fees: number
  taxes: number
  total_pnl: number
  total_pnl_pct: number | null
  day_change: number
  positions_count: number
  first_transaction: string | null
}

export interface AssetQuote {
  secid: string
  name: string | null
  asset_class: string
  is_bond: boolean
  /** чистая цена за единицу, рубли */
  price: number | null
  /** НКД за единицу, рубли */
  accrued_interest: number | null
  as_of: string | null
  source: string | null
}

export interface Position {
  asset_id: number
  secid: string
  name: string
  full_name: string
  asset_class: string
  asset_class_label: string
  currency: string
  is_open: boolean
  quantity: number
  lot_size: number
  avg_price: number
  avg_clean_price: number
  last_price: number
  clean_price: number
  accrued_interest: number
  cost_basis: number
  market_value: number
  unrealized_pnl: number
  unrealized_pnl_pct: number | null
  realized_pnl: number
  dividends: number
  coupons: number
  amortization: number
  income: number
  fees: number
  taxes: number
  total_pnl: number
  day_change: number
  day_change_pct: number | null
  quote_updated_at: string | null
  quote_delayed: boolean | null
  has_market_price: boolean
  maturity_date: string | null
  first_trade: string | null
  is_custom: boolean
  weight: number
}

export interface AllocationSlice {
  key: string
  label: string
  value: number
  weight: number
  asset_class?: string
}

export interface Allocation {
  by_class: AllocationSlice[]
  by_asset: AllocationSlice[]
  total: number
}

export interface PerfPoint {
  date: string
  value: number
  cash: number
  total: number
  invested: number
  return_pct: number
  flow: number
}

export interface BenchmarkSeries {
  code: string
  name: string
  kind: string
  total_return_pct: number | null
  points: { date: string; return_pct: number }[]
}

export interface Performance {
  period: string
  start?: string
  end?: string
  points: PerfPoint[]
  benchmarks: BenchmarkSeries[]
  portfolio_return_pct?: number
}

export interface Metrics {
  available: boolean
  reason?: string
  period?: string
  start?: string | null
  end?: string | null
  days?: number
  observations?: number
  twr?: number
  /** null на окне короче месяца — приводить к году бессмысленно */
  twr_annual?: number | null
  xirr?: number | null
  volatility?: number
  max_drawdown?: number
  max_drawdown_start?: string | null
  max_drawdown_end?: string | null
  max_drawdown_days?: number | null
  /** исторический VaR/ES; отрицательные значения — убыток */
  var_95_daily?: number | null
  es_95_daily?: number | null
  var_99_daily?: number | null
  es_99_daily?: number | null
  var_95_annual?: number | null
  es_95_annual?: number | null
  var_99_annual?: number | null
  es_99_annual?: number | null
  worst_day?: number | null
  worst_day_date?: string | null
  best_day?: number | null
  best_day_date?: string | null
  sharpe?: number | null
  sortino?: number | null
  calmar?: number | null
  beta?: number | null
  alpha?: number | null
  correlation?: number | null
  benchmark_code?: string | null
  benchmark_name?: string | null
  benchmark_return?: number | null
  excess_return?: number | null
  risk_free_rate?: number
}

export interface MonteCarloPoint {
  day: number
  date: string
  /** юбилейные квантили стоимости портфеля по всем сценариям */
  p5: number
  p25: number
  p50: number
  p75: number
  p95: number
  mean: number
}

export interface MonteCarloFinal {
  p5: number
  p25: number
  p50: number
  p75: number
  p95: number
  mean: number
  min: number
  max: number
}

export interface MonteCarlo {
  available: boolean
  reason?: string
  period?: string
  horizon?: string
  horizon_days?: number
  simulations?: number
  observations?: number
  method?: string
  start?: string
  end?: string
  initial_value?: number
  /** годовая волатильность, доля */
  annual_volatility?: number
  probability_of_loss?: number
  probability_of_loss_10?: number
  /** убыток на горизонте, рубли: 95% сценариев не хуже этой границы */
  var_95?: number
  var_95_pct?: number
  es_95?: number
  es_95_pct?: number
  expected_return?: number
  median_return?: number
  final?: MonteCarloFinal
  returns?: MonteCarloFinal
  points?: MonteCarloPoint[]
}

export interface CorrelationAsset {
  asset_id: number
  secid: string
  name: string
  asset_class: string
  asset_class_label: string
  /** доля в рыночной стоимости открытых позиций */
  weight: number
  market_value: number
  observations: number
  /** годовая волатильность, доля */
  volatility: number
}

export interface CorrelationPair {
  i: number
  j: number
  correlation: number
  /** дневная ковариация доходностей, доли² */
  covariance: number
  covariance_annual: number
  /** то же в процентах в квадрате — так понятнее на экране */
  covariance_pct2: number
  covariance_annual_pct2: number
  observations: number
  start: string
  end: string
}

export interface CorrelationMatrix {
  available: boolean
  reason?: string
  period?: string
  start?: string
  end?: string
  method?: string
  assets?: CorrelationAsset[]
  /** null — по паре слишком мало общих наблюдений */
  correlation?: (number | null)[][]
  pairs?: CorrelationPair[]
  total_assets?: number
  truncated?: boolean
  excluded_no_history?: number
  min_observations?: number
}

export interface CorrelationPairAsset {
  secid: string
  name: string
  asset_class: string
  asset_class_label: string
  /** число дневных доходностей за период */
  observations: number
  /** годовая волатильность, доля */
  volatility: number
  first: string | null
  last: string | null
}

export interface CorrelationPairResult {
  available: boolean
  reason?: string
  period?: string
  /** начало и конец общей истории пары */
  start?: string
  end?: string
  requested_start?: string | null
  method?: string
  left?: CorrelationPairAsset
  right?: CorrelationPairAsset
  correlation?: number
  covariance?: number
  covariance_annual?: number
  covariance_pct2?: number
  covariance_annual_pct2?: number
  observations?: number
  min_observations?: number
}

export interface BondItem {
  asset_id: number
  secid: string
  name: string
  quantity: number
  face_value: number
  clean_price: number
  clean_price_pct: number | null
  accrued_interest: number
  dirty_price: number
  market_value: number
  avg_price: number
  current_yield: number | null
  simple_yield: number | null
  effective_yield: number | null
  macaulay_duration_years: number | null
  duration_days: number | null
  modified_duration: number | null
  days_to_maturity: number | null
  maturity_date: string | null
  offer_date: string | null
  coupon_rate: number | null
  annual_coupon: number | null
  coupons_per_year: number | null
  coupon_period_days: number | null
  is_estimate: boolean
  next_payment_date: string | null
  next_payment_value: number | null
  unrealized_pnl: number
}

export interface BondsResponse {
  items: BondItem[]
  summary: {
    market_value: number
    weighted_effective_yield: number | null
    weighted_current_yield: number | null
    weighted_duration_years: number | null
    weighted_duration_days: number | null
    positions: number
  }
}

export interface CalendarItem {
  asset_id: number
  secid: string
  name: string
  asset_class: string
  kind: string
  payment_date: string
  record_date: string | null
  value_per_unit: number
  quantity: number
  amount: number
  currency: string
  is_forecast: boolean
  is_paid: boolean
  note: string | null
}

export interface CalendarResponse {
  date_from: string
  date_to: string
  items: CalendarItem[]
  by_month: { month: string; amount: number; count: number }[]
  total_amount: number
  next_payment: CalendarItem | null
}

export interface BookedPayment {
  created: boolean
  transaction_id: number | null
  portfolio_id: number
  portfolio_name: string
  quantity: number
  amount: number
  tax: number
}

export interface BookPaymentResult {
  asset_id: number
  secid: string
  kind: string
  payment_date: string
  items: BookedPayment[]
}

export interface Transaction {
  id: number
  portfolio_id: number
  portfolio_name: string | null
  asset_id: number | null
  secid: string | null
  asset_name: string | null
  asset_class: string | null
  type: string
  executed_at: string
  price: number
  quantity: number
  amount: number
  fee: number
  tax: number
  accrued_interest: number
  currency: string
  note: string | null
  source: string
  do_not_adjust_cash: boolean
}

export interface PassiveIncomeAsset {
  asset_id: number
  secid: string
  name: string
  asset_class: string
  amount: number
  payments: number
  is_forecast: boolean
  market_value: number
  yield_on_position: number | null
  share: number
}

export interface PassiveIncome {
  horizon_months: number
  date_from: string
  date_to: string
  /** купоны + дивиденды + доход по кастомным активам, без возврата тела долга */
  total: number
  coupons: number
  dividends: number
  other_income: number
  /** амортизации и погашения — возврат вложенного, не доход */
  principal_repayments: number
  per_month: number
  payments_count: number
  market_value: number
  cost_basis: number
  yield_on_market: number | null
  yield_on_cost: number | null
  forecast_amount: number
  forecast_share: number
  by_month: { month: string; amount: number; count: number }[]
  top_assets: PassiveIncomeAsset[]
}

export interface TransactionPageResponse {
  items: Transaction[]
  total: number
  limit: number
  offset: number
}

export interface MacroKeyRate {
  value: number
  /** дата, с которой действует ставка */
  date: string
  /** дата последних данных ЦБ */
  as_of: string | null
  previous: number | null
  change: number | null
}

export interface MacroInflation {
  value: number
  /** YYYY-MM */
  date: string
  target: number | null
  previous: number | null
}

export interface MacroMonthlyInflation {
  value: number
  /** YYYY-MM; null, если дату не удалось разобрать */
  date: string | null
  seasonally_adjusted: boolean
}

export interface MacroOfz {
  tenor: number
  value: number
  date: string
  curve: { tenor: number; value: number }[]
}

export interface Macro {
  available: boolean
  disabled?: boolean
  updated_at?: string
  key_rate: MacroKeyRate | null
  inflation: MacroInflation | null
  inflation_monthly: MacroMonthlyInflation | null
  ofz: MacroOfz | null
  sources: string[]
  notes: string[]
}

export interface Dashboard {
  summary: Summary
  allocation: Allocation
  positions: Position[]
  performance: Performance
  metrics: Metrics
  passive_income: PassiveIncome
  income_months: { month: string; amount: number }[]
  upcoming_payments: CalendarItem[]
  upcoming_total: number
  risk_free_rate: number
  macro: Macro
}

export interface Asset {
  id: number
  secid: string
  name: string
  short_name: string | null
  asset_class: string
  currency: string
  isin: string | null
  lot_size: number
  face_value: number | null
  coupon_rate: number | null
  maturity_date: string | null
  offer_date: string | null
  is_custom: boolean
  annual_rate: number | null
  payout_frequency: string | null
  manual_price: number | null
  capitalization: boolean
  start_date: string | null
  end_date: string | null
  is_active: boolean
}

export interface AssetSearchResult {
  secid: string
  name: string | null
  short_name: string | null
  isin: string | null
  asset_class: string
  board: string | null
  in_portfolio: boolean
}

export interface ImportPreviewRow {
  line: number
  event: string
  type: string
  executed_at: string
  symbol: string | null
  price: number
  quantity: number
  amount: number
  fee: number
  accrued_interest: number
  currency: string
  note: string | null
  warnings: string[]
  amount_rule: string | null
  external_id: string
  is_duplicate: boolean
}

export interface ImportPreview {
  filename: string
  total_lines: number
  parsed: number
  errors: { line: number; error: string; raw?: string }[]
  duplicates: number
  new_symbols: string[]
  known_symbols: string[]
  rows: ImportPreviewRow[]
  summary: Record<string, number>
}

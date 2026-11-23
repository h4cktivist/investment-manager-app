const money0 = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 })
const money2 = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const num4 = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 4 })

export function rub(value: number | null | undefined, digits: 0 | 2 = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${(digits === 0 ? money0 : money2).format(value)} ₽`
}

export function rubShort(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const abs = Math.abs(value)
  if (abs >= 1_000_000) return `${money0.format(value / 1_000_000)} млн ₽`
  if (abs >= 10_000) return `${money0.format(value / 1000)} тыс ₽`
  return `${money0.format(value)} ₽`
}

export function axisMoney(value: number): string {
  const abs = Math.abs(value)
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 1)}м`
  if (abs >= 1000) return `${Math.round(value / 1000)}т`
  return String(Math.round(value))
}

/** Доля (0.1234) → «12,34 %». */
export function pct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${num4.format(Number((value * 100).toFixed(digits)))} %`
}

/** Уже в процентах (12.34) → «12,34 %». */
export function pctRaw(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${num4.format(Number(value.toFixed(digits)))} %`
}

const twoDigits = new Intl.NumberFormat('ru-RU', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

/** Корреляция (0.42) → «+0,42», (−0.42) → «−0,42». */
export function signedNumber(value: number): string {
  const sign = value > 0 ? '+' : value < 0 ? '−' : ''
  return `${sign}${twoDigits.format(Math.abs(value))}`
}

/** Словесная сила связи по модулю корреляции. */
export function correlationStrength(value: number): string {
  const absolute = Math.abs(value)
  if (absolute >= 0.7) return value > 0 ? 'сильная положительная' : 'сильная отрицательная'
  if (absolute >= 0.4) return value > 0 ? 'умеренная положительная' : 'умеренная отрицательная'
  if (absolute >= 0.15) return value > 0 ? 'слабая положительная' : 'слабая отрицательная'
  return 'связи почти нет'
}

export function signed(value: number | null | undefined, fn = rub): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const text = fn(Math.abs(value))
  if (value > 0) return `+${text}`
  if (value < 0) return `−${text}`
  return text
}

export function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: digits }).format(value)
}

const dateFmt = new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })
const dayMonthFmt = new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: 'short' })
const monthFmt = new Intl.DateTimeFormat('ru-RU', { month: 'short', year: '2-digit' })

export function fmtDate(value: string | null | undefined): string {
  if (!value) return '—'
  return dateFmt.format(new Date(value))
}

export function fmtDayMonth(value: string): string {
  return dayMonthFmt.format(new Date(value))
}

/** «2026-07» → «июль 26». */
export function fmtMonth(value: string): string {
  const [year, month] = value.split('-')
  return monthFmt.format(new Date(Number(year), Number(month) - 1, 1))
}

export function fmtDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  return `${dateFmt.format(d)} ${d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}`
}

export function daysWord(days: number | null | undefined): string {
  if (days === null || days === undefined) return '—'
  const abs = Math.abs(days) % 100
  const last = abs % 10
  if (abs > 10 && abs < 20) return `${days} дней`
  if (last === 1) return `${days} день`
  if (last >= 2 && last <= 4) return `${days} дня`
  return `${days} дней`
}

export function relativeTime(value: string | null | undefined): string {
  if (!value) return '—'
  const diff = (Date.now() - new Date(value).getTime()) / 1000
  if (diff < 60) return 'только что'
  if (diff < 3600) return `${Math.floor(diff / 60)} мин назад`
  if (diff < 86400) return `${Math.floor(diff / 3600)} ч назад`
  return fmtDate(value)
}

export const TX_LABELS: Record<string, string> = {
  BUY: 'Покупка',
  SELL: 'Продажа',
  DIVIDEND: 'Дивиденд',
  COUPON: 'Купон',
  AMORTIZATION: 'Амортизация',
  SPLIT: 'Сплит',
  DEPOSIT: 'Пополнение',
  WITHDRAWAL: 'Вывод',
  TAX: 'Налог',
  FEE: 'Комиссия',
  INTEREST: 'Проценты',
}

export const PAYMENT_LABELS: Record<string, string> = {
  COUPON: 'Купон',
  AMORTIZATION: 'Амортизация',
  MATURITY: 'Погашение',
  DIVIDEND: 'Дивиденд',
  CUSTOM_INCOME: 'Доход',
  OFFER: 'Оферта',
}

export const CLASS_LABELS: Record<string, string> = {
  SHARE: 'Акции',
  BOND: 'Облигации',
  ETF: 'Фонды',
  CURRENCY: 'Валюта',
  DEPOSIT: 'Вклады',
  CUSTOM: 'Прочее',
  CASH: 'Деньги',
}

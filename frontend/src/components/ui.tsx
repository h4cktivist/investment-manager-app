import type { ReactNode } from 'react'

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ')
}

export function Card({
  title,
  subtitle,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
}) {
  return (
    <section
      className={cx(
        'rounded-card border border-line bg-surface',
        className,
      )}
    >
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3">
          <div className="min-w-0">
            {title && <h2 className="text-sm font-semibold text-ink">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-2xs text-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cx('p-4', bodyClassName)}>{children}</div>
    </section>
  )
}

export function StatTile({
  label,
  value,
  delta,
  deltaTone,
  hint,
  large,
}: {
  label: string
  value: ReactNode
  delta?: ReactNode
  deltaTone?: 'good' | 'bad' | 'neutral'
  hint?: ReactNode
  large?: boolean
}) {
  return (
    <div className="rounded-card border border-line bg-surface px-4 py-3">
      <div className="text-2xs uppercase tracking-wide text-muted">{label}</div>
      <div
        className={cx(
          'mt-1 font-semibold text-ink',
          large ? 'text-2xl sm:text-3xl' : 'text-xl',
        )}
      >
        {value}
      </div>
      {delta !== undefined && (
        <div className={cx('mt-1 text-xs font-medium', toneClass(deltaTone))}>{delta}</div>
      )}
      {hint && <div className="mt-1 text-2xs text-muted">{hint}</div>}
    </div>
  )
}

export function toneClass(tone?: 'good' | 'bad' | 'neutral'): string {
  if (tone === 'good') return 'text-[var(--good-text)]'
  if (tone === 'bad') return 'text-[var(--critical)]'
  return 'text-ink-2'
}

export function toneOf(value: number | null | undefined): 'good' | 'bad' | 'neutral' {
  if (value === null || value === undefined || !Number.isFinite(value) || value === 0) return 'neutral'
  return value > 0 ? 'good' : 'bad'
}

/** Число с окраской по знаку. Знак дублируется символом — не только цветом. */
export function Money({
  value,
  render,
  className,
}: {
  value: number | null | undefined
  render: (v: number) => string
  className?: string
}) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return <span className={cx('text-muted', className)}>—</span>
  }
  return (
    <span className={cx('tnum', toneClass(toneOf(value)), className)}>
      {value > 0 ? '+' : value < 0 ? '−' : ''}
      {render(Math.abs(value))}
    </span>
  )
}

export function Badge({
  children,
  tone = 'neutral',
}: {
  children: ReactNode
  tone?: 'neutral' | 'good' | 'bad' | 'warn' | 'info'
}) {
  const tones: Record<string, string> = {
    neutral: 'border-line text-ink-2',
    good: 'border-[var(--good)]/40 text-[var(--good-text)]',
    bad: 'border-[var(--critical)]/40 text-[var(--critical)]',
    warn: 'border-[var(--warning)]/50 text-ink-2',
    info: 'border-[var(--series-1)]/40 text-[var(--series-1)]',
  }
  return (
    <span
      className={cx(
        'inline-flex items-center rounded border px-1.5 py-0.5 text-2xs font-medium',
        tones[tone],
      )}
    >
      {children}
    </span>
  )
}

export function Button({
  children,
  onClick,
  variant = 'ghost',
  type = 'button',
  disabled,
  className,
  title,
}: {
  children: ReactNode
  onClick?: () => void
  variant?: 'primary' | 'ghost' | 'danger'
  type?: 'button' | 'submit'
  disabled?: boolean
  className?: string
  title?: string
}) {
  const variants: Record<string, string> = {
    primary:
      'bg-[var(--series-1)] text-white border-transparent hover:brightness-110 disabled:opacity-50',
    ghost: 'border-line bg-surface text-ink hover:bg-raised disabled:opacity-50',
    danger: 'border-[var(--critical)]/50 bg-surface text-[var(--critical)] hover:bg-raised',
  }
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={cx(
        'inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-medium transition',
        'disabled:cursor-not-allowed',
        variants[variant],
        className,
      )}
    >
      {children}
    </button>
  )
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  size = 'sm',
}: {
  options: { value: T; label: string; title?: string }[]
  value: T
  onChange: (value: T) => void
  size?: 'sm' | 'xs'
}) {
  return (
    <div className="inline-flex rounded-md border border-line bg-surface p-0.5" role="tablist">
      {options.map((option) => (
        <button
          key={option.value}
          role="tab"
          aria-selected={option.value === value}
          title={option.title}
          onClick={() => onChange(option.value)}
          className={cx(
            'rounded px-2.5 font-medium transition',
            size === 'xs' ? 'py-0.5 text-2xs' : 'py-1 text-xs',
            option.value === value
              ? 'bg-[var(--series-1)] text-white'
              : 'text-ink-2 hover:text-ink',
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string
  hint?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-4 py-12 text-center">
      <p className="text-sm font-medium text-ink">{title}</p>
      {hint && <p className="max-w-md text-xs text-muted">{hint}</p>}
      {action}
    </div>
  )
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-xs text-muted">
      <span className="h-3 w-3 animate-spin rounded-full border-2 border-line border-t-[var(--series-1)]" />
      {label ?? 'Загрузка…'}
    </div>
  )
}

export function ErrorBox({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error)
  return (
    <div className="rounded-card border border-[var(--critical)]/40 bg-surface px-4 py-3 text-xs text-[var(--critical)]">
      Не удалось загрузить данные: {message}
    </div>
  )
}

/** Подпись под графиком — обязательная «страховка» вместо цвета. */
export function LegendRow({
  items,
}: {
  items: { color: string; label: string; value?: string }[]
}) {
  return (
    <ul className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-1.5 text-2xs text-ink-2">
          <span
            aria-hidden
            className="inline-block h-2 w-2 shrink-0 rounded-sm"
            style={{ background: item.color }}
          />
          <span>{item.label}</span>
          {item.value && <span className="tnum font-medium text-ink">{item.value}</span>}
        </li>
      ))}
    </ul>
  )
}

import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'

import { useQuotes } from '../hooks/useQuotes'
import { rub, signed } from '../lib/format'
import { useSelection } from '../lib/store'
import { useTheme } from '../lib/theme'
import { Money, cx } from './ui'

const NAV = [
  { to: '/', label: 'Дашборд', end: true },
  { to: '/positions', label: 'Позиции' },
  { to: '/transactions', label: 'Сделки' },
  { to: '/calendar', label: 'Календарь' },
  { to: '/bonds', label: 'Облигации' },
  { to: '/metrics', label: 'Метрики' },
  { to: '/benchmarks', label: 'Бенчмарки' },
  { to: '/assets', label: 'Активы' },
  { to: '/import', label: 'Импорт' },
]

export default function Layout() {
  const { portfolios, selected, setSelected, toggle } = useSelection()
  const { connected } = useQuotes()
  const { resolved, toggle: toggleTheme } = useTheme()
  const [menuOpen, setMenuOpen] = useState(false)

  const visible = selected.length ? portfolios.filter((p) => selected.includes(p.id)) : portfolios
  const totalValue = visible.reduce((sum, p) => sum + p.total_value, 0)
  const dayChange = visible.reduce((sum, p) => sum + p.day_change, 0)

  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-20 border-b border-line bg-surface/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5">
          <div className="flex items-center gap-2">
            <span className="grid h-7 w-7 place-items-center rounded bg-[var(--series-1)] text-sm font-bold text-white">
              ₽
            </span>
            <span className="text-sm font-semibold text-ink">Инвестиции</span>
          </div>

          <nav className="order-3 -mx-1 flex w-full gap-0.5 overflow-x-auto md:order-none md:w-auto md:overflow-visible">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  cx(
                    'whitespace-nowrap rounded px-2.5 py-1.5 text-xs font-medium transition',
                    isActive ? 'bg-raised text-ink' : 'text-ink-2 hover:text-ink',
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-3">
            <div className="hidden text-right sm:block">
              <div className="tnum text-sm font-semibold text-ink">{rub(totalValue, 0)}</div>
              <div className="text-2xs">
                <Money value={dayChange} render={(v) => rub(v, 0)} /> <span className="text-muted">за день</span>
              </div>
            </div>

            <div className="relative">
              <button
                onClick={() => setMenuOpen((v) => !v)}
                className="rounded-md border border-line px-2.5 py-1.5 text-xs text-ink-2 hover:text-ink"
              >
                {selected.length === 0
                  ? `Все портфели (${portfolios.length})`
                  : `Портфелей: ${selected.length}`}
              </button>
              {menuOpen && (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} />
                  <div className="absolute right-0 z-20 mt-1 w-72 rounded-card border border-line bg-surface p-2 shadow-xl">
                    <button
                      onClick={() => setSelected([])}
                      className={cx(
                        'flex w-full items-center justify-between rounded px-2 py-1.5 text-xs hover:bg-raised',
                        selected.length === 0 ? 'font-semibold text-ink' : 'text-ink-2',
                      )}
                    >
                      <span>Все портфели</span>
                      {selected.length === 0 && <span aria-hidden>✓</span>}
                    </button>
                    <div className="my-1 border-t border-line" />
                    {portfolios.map((p) => (
                      <button
                        key={p.id}
                        onClick={() => toggle(p.id)}
                        className="flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-xs text-ink-2 hover:bg-raised"
                      >
                        <span className="flex min-w-0 items-center gap-2">
                          <span aria-hidden className="w-3 shrink-0">
                            {selected.includes(p.id) ? '✓' : ''}
                          </span>
                          <span className="truncate">{p.name}</span>
                        </span>
                        <span className="tnum shrink-0 text-muted">{rub(p.total_value, 0)}</span>
                      </button>
                    ))}
                    {!portfolios.length && (
                      <p className="px-2 py-1.5 text-2xs text-muted">
                        Портфелей пока нет — создайте на странице «Импорт»
                      </p>
                    )}
                  </div>
                </>
              )}
            </div>

            <span
              title={
                connected
                  ? 'Котировки приходят по WebSocket'
                  : 'Нет соединения с потоком котировок'
              }
              className="flex items-center gap-1.5 text-2xs text-muted"
            >
              <span
                aria-hidden
                className={cx(
                  'inline-block h-1.5 w-1.5 rounded-full',
                  connected ? 'bg-[var(--good)]' : 'bg-[var(--critical)]',
                )}
              />
              <span className="hidden lg:inline">{connected ? 'онлайн' : 'офлайн'}</span>
            </span>

            <button
              onClick={toggleTheme}
              title="Переключить тему"
              className="rounded-md border border-line px-2 py-1.5 text-xs text-ink-2 hover:text-ink"
            >
              {resolved === 'dark' ? '☾' : '☀'}
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1600px] px-4 py-5">
        <Outlet />
      </main>

      <footer className="mx-auto max-w-[1600px] px-4 pb-6 pt-2 text-2xs text-muted">
        Данные — Московская биржа (ISS). Котировки анонимного доступа приходят с
        задержкой около 15 минут. Расчёты носят справочный характер.
      </footer>
    </div>
  )
}

export function ValueWithSign({ value }: { value: number }) {
  return <span className="tnum">{signed(value)}</span>
}

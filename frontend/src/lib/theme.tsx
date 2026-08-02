import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

type Mode = 'light' | 'dark' | 'system'
type Resolved = 'light' | 'dark'

interface ThemeValue {
  mode: Mode
  resolved: Resolved
  setMode: (mode: Mode) => void
  toggle: () => void
}

const ThemeContext = createContext<ThemeValue | null>(null)
const STORAGE_KEY = 'im-theme'

function systemMode(): Resolved {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<Mode>(
    () => (localStorage.getItem(STORAGE_KEY) as Mode | null) ?? 'system',
  )
  const [systemResolved, setSystemResolved] = useState<Resolved>(systemMode)

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => setSystemResolved(media.matches ? 'dark' : 'light')
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  const resolved: Resolved = mode === 'system' ? systemResolved : mode

  useEffect(() => {
    const root = document.documentElement
    if (mode === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', mode)
    localStorage.setItem(STORAGE_KEY, mode)
  }, [mode])

  const setMode = useCallback((next: Mode) => setModeState(next), [])
  const toggle = useCallback(
    () => setModeState(resolved === 'dark' ? 'light' : 'dark'),
    [resolved],
  )

  const value = useMemo(
    () => ({ mode, resolved, setMode, toggle }),
    [mode, resolved, setMode, toggle],
  )
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeValue {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme вне ThemeProvider')
  return ctx
}

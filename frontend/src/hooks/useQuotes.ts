import { useEffect, useRef, useState } from 'react'

export interface QuoteTick {
  secid: string
  price: number
  price_norm: number
  prev_close: number | null
  change_pct: number | null
  accrued_interest: number | null
  source: string
  is_delayed: boolean
  updated_at: string
}

export type QuoteMap = Record<string, QuoteTick>

/**
 * Живые котировки по WebSocket. Бэкенд опрашивает биржу и рассылает изменения,
 * при обрыве соединения переподключаемся с нарастающей паузой.
 */
export function useQuotes(): { quotes: QuoteMap; connected: boolean } {
  const [quotes, setQuotes] = useState<QuoteMap>({})
  const [connected, setConnected] = useState(false)
  const attemptRef = useRef(0)

  useEffect(() => {
    let socket: WebSocket | null = null
    let timer: number | undefined
    let closed = false

    const connect = () => {
      if (closed) return
      const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
      socket = new WebSocket(`${protocol}://${location.host}/ws/quotes`)

      socket.onopen = () => {
        attemptRef.current = 0
        setConnected(true)
      }
      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          if (data.type !== 'quotes' && data.type !== 'snapshot') return
          setQuotes((prev) => {
            const next = { ...prev }
            for (const tick of data.quotes as QuoteTick[]) next[tick.secid] = tick
            return next
          })
        } catch {
          /* мусор в кадре — игнорируем */
        }
      }
      socket.onclose = () => {
        setConnected(false)
        if (closed) return
        const delay = Math.min(1000 * 2 ** attemptRef.current++, 30_000)
        timer = window.setTimeout(connect, delay)
      }
      socket.onerror = () => socket?.close()
    }

    connect()
    return () => {
      closed = true
      if (timer) window.clearTimeout(timer)
      socket?.close()
    }
  }, [])

  return { quotes, connected }
}

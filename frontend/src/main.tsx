import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import ReactDOM from 'react-dom/client'
import { RouterProvider, createBrowserRouter } from 'react-router-dom'

import Layout from './components/Layout'
import './index.css'
import { SelectionProvider } from './lib/store'
import { ThemeProvider } from './lib/theme'
import AssetsPage from './pages/AssetsPage'
import BenchmarksPage from './pages/BenchmarksPage'
import BondsPage from './pages/BondsPage'
import CalendarPage from './pages/CalendarPage'
import Dashboard from './pages/Dashboard'
import ImportPage from './pages/ImportPage'
import MetricsPage from './pages/MetricsPage'
import PositionsPage from './pages/PositionsPage'
import TransactionsPage from './pages/TransactionsPage'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

const router = createBrowserRouter([
  {
    path: '/',
    element: <Layout />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: 'positions', element: <PositionsPage /> },
      { path: 'transactions', element: <TransactionsPage /> },
      { path: 'calendar', element: <CalendarPage /> },
      { path: 'bonds', element: <BondsPage /> },
      { path: 'metrics', element: <MetricsPage /> },
      { path: 'benchmarks', element: <BenchmarksPage /> },
      { path: 'assets', element: <AssetsPage /> },
      { path: 'import', element: <ImportPage /> },
    ],
  },
])

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <SelectionProvider>
          <RouterProvider router={router} />
        </SelectionProvider>
      </ThemeProvider>
    </QueryClientProvider>
  </React.StrictMode>,
)

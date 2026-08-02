/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        surface: 'var(--surface-1)',
        plane: 'var(--plane)',
        raised: 'var(--surface-2)',
        ink: 'var(--text-primary)',
        'ink-2': 'var(--text-secondary)',
        muted: 'var(--text-muted)',
        line: 'var(--border)',
        grid: 'var(--gridline)',
        good: 'var(--good)',
        bad: 'var(--critical)',
        warn: 'var(--warning)',
        accent: 'var(--series-1)',
      },
      fontFamily: {
        sans: ['system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      borderRadius: { card: '10px' },
    },
  },
  plugins: [],
}

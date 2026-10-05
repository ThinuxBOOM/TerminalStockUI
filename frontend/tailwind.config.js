/** @type {import('tailwindcss').Config} */
// Design tokens. Green/red mean up/down only; interaction (links, buttons,
// focus, selection) uses the blue accent so "positive" and "clickable" never
// share a color. Chart series colors are validated for color-vision
// deficiency on the panel surface (see components/charts).
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        term: {
          bg: '#090c12',
          panel: '#10151e',
          panel2: '#151b26',
          elevated: '#1a2130',
          input: '#0c1018',
          overlay: '#090c12E6',
          border: '#1e2633',
          border2: '#2a3446',
          muted: '#8a94a6',
          faint: '#5d6778',
          text: '#e5eaf2',
          accent: '#4d8dff',
          accentHover: '#74a6ff',
          accentDim: '#14223b',
          green: '#34d399',
          greenDim: '#0f2b22',
          red: '#f87171',
          redDim: '#371718',
          amber: '#fbbf24',
          amberDim: '#382a0e',
          cyan: '#38bdf8',
          focus: '#4d8dff',
          // Chart series (dark steps of a CVD-validated categorical order).
          series1: '#3987e5',
          series2: '#d95926',
          series3: '#199e70',
          chartYellow: '#f5c518',
          chartPurple: '#a78bfa',
          chartOrange: '#ff8a3d',
          chartTeal: '#2dd4bf',
        },
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', 'ui-monospace', 'Menlo', 'Consolas', 'monospace'],
        sans: ['"Inter Variable"', 'Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
        display: ['2.25rem', { lineHeight: '2.5rem', letterSpacing: '-0.02em' }],
        'display-sm': ['1.5rem', { lineHeight: '2rem', letterSpacing: '-0.01em' }],
      },
      boxShadow: {
        panel: '0 1px 0 rgba(255,255,255,0.02) inset, 0 1px 2px rgba(0,0,0,0.35)',
        'panel-lg': '0 8px 30px rgba(0,0,0,0.45)',
        overlay: '0 16px 48px rgba(0,0,0,0.6)',
        glow: '0 0 0 1px rgba(77,141,255,0.35), 0 8px 30px rgba(77,141,255,0.15)',
      },
      borderRadius: {
        sm: '0.375rem',
        md: '0.5rem',
        lg: '0.75rem',
        xl: '1rem',
      },
      keyframes: {
        'fade-up': { from: { opacity: 0, transform: 'translateY(6px)' }, to: { opacity: 1, transform: 'none' } },
      },
      animation: {
        'fade-up': 'fade-up 220ms ease-out both',
      },
    },
  },
  plugins: [],
};

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  // Covers index.html + every src path (components/, features/, pages/,
  // hooks/, api/). js/jsx included so future plain-JS files are not purged.
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        term: {
          bg: '#0a0e14',
          panel: '#0f141d',
          panel2: '#141a24',
          border: '#1c2433',
          border2: '#2a3448',
          muted: '#8b94a7',
          text: '#d7deea',
          green: '#3ddc84',
          greenDim: '#1c4a35',
          red: '#ff5c5c',
          redDim: '#4a1c1c',
          amber: '#ffb454',
          amberDim: '#4a3a1c',
          cyan: '#56c8ff',
        },
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', 'ui-monospace', 'Menlo', 'Consolas', 'monospace'],
        sans: ['"Inter"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
        'display': ['2.25rem', { lineHeight: '2.5rem', letterSpacing: '-0.02em' }],
        'display-sm': ['1.5rem', { lineHeight: '2rem', letterSpacing: '-0.01em' }],
      },
      boxShadow: {
        panel: '0 1px 2px rgba(0,0,0,0.4)',
        'panel-lg': '0 4px 24px rgba(0,0,0,0.5)',
      },
      borderRadius: {
        lg: '0.625rem',
      },
    },
  },
  plugins: [],
};

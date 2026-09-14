/** @type {import('tailwindcss').Config} */
export default {
  // Covers index.html + every src path (components/, features/, pages/,
  // hooks/, api/). js/jsx included so future plain-JS files are not purged.
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        term: {
          bg: '#0a0e14',
          panel: '#0f141d',
          border: '#1c2433',
          muted: '#8b94a7',
          text: '#d7deea',
          green: '#3ddc84',
          red: '#ff5c5c',
          amber: '#ffb454',
          cyan: '#56c8ff',
        },
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', 'ui-monospace', 'Menlo', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
};

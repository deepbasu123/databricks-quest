/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        quest: {
          bg: '#0f172a',
          card: '#1e293b',
          border: '#334155',
          gold: '#f59e0b',
          purple: '#8b5cf6',
          cyan: '#06b6d4',
        },
        level: {
          bronze: '#cd7f32',
          silver: '#9ca3af',
          gold: '#eab308',
          platinum: '#a78bfa',
          elite: '#f97316',
        },
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'glow': 'glow 2s ease-in-out infinite alternate',
      },
      keyframes: {
        glow: {
          '0%': { boxShadow: '0 0 5px rgba(245, 158, 11, 0.3)' },
          '100%': { boxShadow: '0 0 20px rgba(245, 158, 11, 0.6)' },
        },
      },
    },
  },
  plugins: [],
}

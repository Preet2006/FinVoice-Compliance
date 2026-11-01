import type { Config } from 'tailwindcss'

export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Design system tokens from §3.2 of MASTER_IMPLEMENTATION_PLAN
        surface: {
          0: '#F7F8FA',
          1: '#FFFFFF',
          2: '#EEF1F5',
        },
        border: '#DDE1E7',
        text: {
          primary: '#13161B',
          secondary: '#5B6472',
        },
        brand: {
          DEFAULT: '#1F3A8F',
          hover: '#16296B',
        },
        severity: {
          critical: '#B3261E',
          high: '#C45A00',
          medium: '#9A7B00',
          low: '#5B6472',
        },
        success: '#1E7A34',
        warning: {
          bg: '#FFF4E5',
        },
        error: {
          bg: '#FCEAE9',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['IBM Plex Mono', 'monospace'],
      },
      spacing: {
        '4': '4px',
        '8': '8px',
        '12': '12px',
        '16': '16px',
        '24': '24px',
        '32': '32px',
        '48': '48px',
      },
      borderRadius: {
        'input': '6px',
        'card': '10px',
      },
      maxWidth: {
        'app': '1440px',
      },
    },
  },
  plugins: [],
} satisfies Config

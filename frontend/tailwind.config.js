/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#0F1115',
        card: '#171A21',
        line: '#262B35',
        line2: '#1F242D',
        edge: '#3A4150',
        fg: '#E8EAEE',
        sub: '#9AA3B2',
        cur: '#5B8DEF',
        up: '#4CC274',
        down: '#F06A6A',
        link: '#7FA6F5',
      },
      fontFamily: {
        sans: ['Inter', '"Noto Sans KR"', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}

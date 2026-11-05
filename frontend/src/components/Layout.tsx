import { NavLink, Outlet } from 'react-router-dom'
import { useHealth } from '../hooks'

const NAV = [
  { to: '/', label: '국면 비교', end: true },
  { to: '/events', label: '이벤트', end: false },
  { to: '/my', label: '내 계좌', end: false },
]

export default function Layout() {
  const health = useHealth()
  return (
    <div className="max-w-[1280px] mx-auto px-4 sm:px-6 pb-12 flex flex-col gap-6">
      <header className="flex flex-wrap items-center gap-4 py-4 border-b border-line">
        <div className="font-bold text-xl tracking-tight">stocker</div>
        <nav className="flex flex-wrap gap-1 ml-2" aria-label="페이지">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                `px-3.5 py-2.5 rounded-lg font-medium no-underline hover:no-underline ${
                  isActive ? 'bg-fg !text-bg' : '!text-fg'
                }`
              }
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto text-[13px] muted">
          개인 모드 · 로컬 실행 ·{' '}
          {health.data ? `데이터 ${health.data.last_trading_day}` : health.isError ? 'API 연결 안 됨' : '데이터 확인 중…'}
        </div>
      </header>
      <Outlet />
    </div>
  )
}

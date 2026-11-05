import { Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import EventsPage from './pages/EventsPage'
import MainPage from './pages/MainPage'
import MyPage from './pages/MyPage'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<MainPage />} />
        <Route path="events" element={<EventsPage />} />
        <Route path="my" element={<MyPage />} />
        <Route path="*" element={<div className="card p-6 muted">없는 페이지다.</div>} />
      </Route>
    </Routes>
  )
}

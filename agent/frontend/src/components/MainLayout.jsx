import { useEffect, useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'

const MENUS = [
  { path: '/agent', label: '智能问答', glyph: '智' },
  { path: '/knowledge', label: '知识库', glyph: '识' },
  { path: '/strategy', label: '检索策略', glyph: '检' },
  { path: '/logs', label: '日志', glyph: '日' },
]

export default function MainLayout() {
  const location = useLocation()
  const navigate = useNavigate()
  const [collapsed, setCollapsed] = useState(false)
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))

  const current = MENUS.find((m) => location.pathname.startsWith(m.path))

  useEffect(() => {
    document.title = `${current?.label ?? 'Agent'} · Agentic 领域知识库系统`
  }, [current])

  function toggleDark() {
    const next = !dark
    setDark(next)
    document.documentElement.classList.toggle('dark', next)
    localStorage.setItem('app-theme', next ? 'dark' : 'light')
  }

  return (
    <div className="app-shell">
      <aside className={`app-aside${collapsed ? ' is-collapsed' : ''}`}>
        <div className="app-brand">
          <div className="app-brand__title">Agentic 知识库</div>
          <div className="app-brand__sub">LLM 逐轮驱动检索决策</div>
        </div>

        <nav className="app-menu">
          {MENUS.map((m) => (
            <button
              key={m.path}
              className={`app-menu-item${current?.path === m.path ? ' is-active' : ''}`}
              onClick={() => navigate(m.path)}
            >
              <span className="app-menu-item__glyph">{m.glyph}</span>
              <span className="app-menu-item__label">{m.label}</span>
            </button>
          ))}
        </nav>

        <div className="app-footer">
          <button className="icon-btn" onClick={toggleDark} title="切换深色模式">
            {dark ? '☀' : '☾'}
          </button>
          <span className="app-footer__hint">第三版 · Agentic</span>
        </div>
      </aside>

      <div className="app-body">
        <header className="app-header">
          <div className="app-header__left">
            <button className="icon-btn" onClick={() => setCollapsed((c) => !c)} title="折叠侧栏">
              ☰
            </button>
            <span className="app-header__title">{current?.label ?? ''}</span>
          </div>
          <span className="app-status">
            <span className="app-status__dot" />
            系统运行中
          </span>
        </header>
        <main className="app-main">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
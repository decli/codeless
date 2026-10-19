import { CircleCheck, CircleX, Info, PanelLeftOpen } from 'lucide-react'
import { useState } from 'react'
import { AppProvider } from './app-context'
import { SettingsModal } from './components/Settings'
import { Sidebar } from './components/Sidebar'
import { IconButton } from './components/ui'
import { matchRoute, usePath } from './lib/router'
import { useToasts } from './lib/toast'
import { Home } from './pages/Home'
import { ProjectPage } from './pages/ProjectPage'

function readCollapsed(): boolean {
  try {
    return localStorage.getItem('codeless.sidebar') === 'collapsed'
  } catch {
    return false
  }
}

function Shell() {
  const route = matchRoute(usePath())
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const toggle = (value: boolean) => {
    setCollapsed(value)
    try {
      localStorage.setItem('codeless.sidebar', value ? 'collapsed' : 'open')
    } catch {
      /* 忽略 */
    }
  }
  return (
    <div className={`app${collapsed ? ' sidebar-collapsed' : ''}`}>
      {!collapsed && <Sidebar activeId={route.name === 'project' ? route.id : undefined} onCollapse={() => toggle(true)} />}
      <main className="main">
        {collapsed && (
          <IconButton label="展开侧边栏" size="sm" className="sidebar-open" onClick={() => toggle(false)}>
            <PanelLeftOpen />
          </IconButton>
        )}
        {route.name === 'project' ? <ProjectPage key={route.id} id={route.id} /> : <Home />}
      </main>
      <SettingsModal />
      <Toasts />
    </div>
  )
}

function Toasts() {
  const toasts = useToasts()
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.kind}`}>
          {t.kind === 'error' ? <CircleX /> : t.kind === 'success' ? <CircleCheck /> : <Info />}
          <span>{t.text}</span>
        </div>
      ))}
    </div>
  )
}

export default function App() {
  return (
    <AppProvider>
      <Shell />
    </AppProvider>
  )
}

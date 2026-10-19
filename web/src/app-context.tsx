// 全局数据：服务信息、模型设置、项目列表
import { createContext, type ReactNode, useCallback, useContext, useEffect, useState } from 'react'
import { api, type Info, type Project, type Settings } from './api'

export type SettingsTab = 'models' | 'agent' | 'about'

interface AppContextValue {
  info: Info | null
  settings: Settings | null
  projects: Project[] | null
  reloadSettings: () => Promise<void>
  reloadProjects: () => Promise<void>
  settingsTab: SettingsTab | null
  openSettings: (tab?: SettingsTab) => void
  closeSettings: () => void
}

const AppContext = createContext<AppContextValue | null>(null)

export function AppProvider({ children }: { children: ReactNode }) {
  const [info, setInfo] = useState<Info | null>(null)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [projects, setProjects] = useState<Project[] | null>(null)
  const [settingsTab, setSettingsTab] = useState<SettingsTab | null>(null)

  const reloadSettings = useCallback(async () => {
    const [s, i] = await Promise.all([api.settings(), api.info()])
    setSettings(s)
    setInfo(i)
  }, [])

  const reloadProjects = useCallback(async () => {
    try {
      setProjects(await api.projects())
    } catch {
      /* 列表刷新失败不打扰用户，下次轮询再试 */
    }
  }, [])

  useEffect(() => {
    reloadSettings().catch(() => undefined)
    reloadProjects()
  }, [reloadSettings, reloadProjects])

  // 沙箱状态启动时还在检测，过几秒再取一次
  useEffect(() => {
    if (info?.sandbox_status !== 'checking') return
    const timer = setTimeout(() => api.info().then(setInfo).catch(() => undefined), 2500)
    return () => clearTimeout(timer)
  }, [info])

  // 有项目在跑时刷新得勤一些，侧边栏的状态点才跟得上
  const anyRunning = projects?.some((p) => p.running) ?? false
  useEffect(() => {
    const timer = setInterval(reloadProjects, anyRunning ? 3000 : 15000)
    return () => clearInterval(timer)
  }, [anyRunning, reloadProjects])

  const value: AppContextValue = {
    info,
    settings,
    projects,
    reloadSettings,
    reloadProjects,
    settingsTab,
    openSettings: (tab = 'models') => setSettingsTab(tab),
    closeSettings: () => setSettingsTab(null),
  }
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>
}

export function useApp(): AppContextValue {
  const ctx = useContext(AppContext)
  if (!ctx) throw new Error('useApp 必须在 AppProvider 里使用')
  return ctx
}

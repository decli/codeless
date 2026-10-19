// 主题：跟随系统 / 浅色 / 深色
import { useEffect, useState } from 'react'

export type Theme = 'system' | 'light' | 'dark'

function read(): Theme {
  try {
    const t = localStorage.getItem('codeless.theme')
    if (t === 'light' || t === 'dark') return t
  } catch {
    /* 无痕模式等情况下读不到 */
  }
  return 'system'
}

export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(read)
  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') delete root.dataset.theme
    else root.dataset.theme = theme
    try {
      if (theme === 'system') localStorage.removeItem('codeless.theme')
      else localStorage.setItem('codeless.theme', theme)
    } catch {
      /* 忽略 */
    }
  }, [theme])
  return [theme, setTheme]
}

export function isDark(): boolean {
  const t = document.documentElement.dataset.theme
  if (t) return t === 'dark'
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

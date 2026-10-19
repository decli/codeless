// 极简路由：只有首页和项目页两种，用 History API，不引第三方库
import { useSyncExternalStore } from 'react'

const listeners = new Set<() => void>()

function subscribe(fn: () => void) {
  listeners.add(fn)
  window.addEventListener('popstate', fn)
  return () => {
    listeners.delete(fn)
    window.removeEventListener('popstate', fn)
  }
}

export function navigate(path: string, replace = false) {
  if (path === location.pathname) return
  if (replace) history.replaceState(null, '', path)
  else history.pushState(null, '', path)
  listeners.forEach((fn) => fn())
}

export function usePath(): string {
  return useSyncExternalStore(subscribe, () => location.pathname)
}

export type Route = { name: 'home' } | { name: 'project'; id: string }

export function matchRoute(path: string): Route {
  const m = path.match(/^\/p\/([a-z0-9]{6,32})\/?$/)
  if (m) return { name: 'project', id: m[1] }
  return { name: 'home' }
}

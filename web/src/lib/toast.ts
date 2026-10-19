// 轻提示：全局一个队列，组件里 toast.error(...) 直接用
import { useSyncExternalStore } from 'react'

export interface Toast {
  id: number
  kind: 'error' | 'success' | 'info'
  text: string
}

let items: Toast[] = []
let counter = 0
const listeners = new Set<() => void>()

function emit() {
  listeners.forEach((fn) => fn())
}

function push(kind: Toast['kind'], text: string) {
  const id = ++counter
  items = [...items.slice(-3), { id, kind, text }]
  emit()
  setTimeout(() => {
    items = items.filter((t) => t.id !== id)
    emit()
  }, kind === 'error' ? 6000 : 3000)
}

export const toast = {
  error: (e: unknown) => push('error', e instanceof Error ? e.message : String(e)),
  success: (text: string) => push('success', text),
  info: (text: string) => push('info', text),
}

export function useToasts(): Toast[] {
  return useSyncExternalStore(
    (fn) => {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    () => items,
  )
}

// 项目时间线：先收快照，再按序号收增量操作。断线时浏览器自动带 Last-Event-ID 续传。
import { useEffect, useSyncExternalStore } from 'react'
import type { Item, Op, Snapshot, TimelineState } from '../api'

export type Connection = 'connecting' | 'live' | 'offline'

export interface TimelineView {
  items: Item[]
  state: TimelineState
  connection: Connection
  loaded: boolean
}

class TimelineStore {
  view: TimelineView = { items: [], state: {}, connection: 'connecting', loaded: false }
  private index = new Map<string, number>()
  private listeners = new Set<() => void>()
  private source: EventSource | null = null
  refs = 0
  readonly id: string

  constructor(id: string) {
    this.id = id
  }

  subscribe = (fn: () => void) => {
    this.listeners.add(fn)
    return () => {
      this.listeners.delete(fn)
    }
  }

  getView = () => this.view

  private emit(patch: Partial<TimelineView>) {
    this.view = { ...this.view, ...patch }
    this.listeners.forEach((fn) => fn())
  }

  connect() {
    if (this.source) return
    const source = new EventSource(`/api/projects/${this.id}/events?after=-1`)
    this.source = source
    source.addEventListener('open', () => this.emit({ connection: 'live' }))
    source.addEventListener('error', () => {
      if (source.readyState === EventSource.CLOSED) {
        // 服务端彻底断开（比如重启）：过一会儿重新连，会收到新快照
        this.source = null
        setTimeout(() => this.refs > 0 && this.connect(), 2000)
      }
      this.emit({ connection: 'offline' })
    })
    source.addEventListener('snapshot', (e) => this.reset(JSON.parse((e as MessageEvent).data)))
    source.addEventListener('ops', (e) => this.applyAll(JSON.parse((e as MessageEvent).data)))
  }

  close() {
    this.source?.close()
    this.source = null
  }

  private reset(snap: Snapshot) {
    this.index = new Map(snap.items.map((item, i) => [item.id, i]))
    this.emit({ items: snap.items, state: snap.state, connection: 'live', loaded: true })
  }

  private applyAll(ops: Op[]) {
    let items = this.view.items
    let state = this.view.state
    let copied = false
    const ensureCopy = () => {
      if (!copied) {
        items = items.slice()
        copied = true
      }
    }
    for (const op of ops) {
      if (op.op === 'add') {
        ensureCopy()
        this.index.set(op.item.id, items.length)
        items.push(op.item)
      } else if (op.op === 'append' || op.op === 'update') {
        const i = this.index.get(op.id)
        if (i === undefined) continue
        ensureCopy()
        const prev = items[i] as unknown as Record<string, unknown>
        items[i] = (op.op === 'append'
          ? { ...prev, [op.field]: String(prev[op.field] ?? '') + op.text }
          : { ...prev, ...op.patch }) as unknown as Item
      } else if (op.op === 'state') {
        state = { ...state, ...op.patch }
      }
    }
    this.emit({ items, state, connection: 'live' })
  }
}

const stores = new Map<string, TimelineStore>()

function acquire(id: string): TimelineStore {
  let store = stores.get(id)
  if (!store) {
    store = new TimelineStore(id)
    stores.set(id, store)
  }
  return store
}

export function useTimeline(id: string): TimelineView {
  const store = acquire(id)
  useEffect(() => {
    store.refs += 1
    store.connect()
    return () => {
      store.refs -= 1
      // 切换项目时稍等再断开，来回切换不用重连
      setTimeout(() => {
        if (store.refs === 0) {
          store.close()
          stores.delete(store.id)
        }
      }, 10_000)
    }
  }, [store])
  return useSyncExternalStore(store.subscribe, store.getView)
}

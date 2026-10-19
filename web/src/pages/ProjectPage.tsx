// 项目页：左边对话，右边工作台（预览 / 代码 / 改动 / 终端）
import {
  ArrowLeft, CircleCheck, CircleDot, Circle, Download, Ellipsis, FileCode2, History, ListChecks, MessageSquare,
  MonitorPlay, Pencil, SquareTerminal, Trash2, TriangleAlert, WifiOff, Wrench,
} from 'lucide-react'
import { type PointerEvent as ReactPointerEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type Activity, type Item, type PreviewError, type Project, type TimelineState, type Todo } from '../api'
import { useApp } from '../app-context'
import { CodePanel } from '../components/CodeView'
import { Composer } from '../components/Composer'
import { HistoryPanel } from '../components/History'
import { LogsPanel } from '../components/Logs'
import { PreviewPanel } from '../components/Preview'
import { RenameDialog } from '../components/Sidebar'
import { Timeline } from '../components/Timeline'
import { Button, Confirm, Empty, IconButton, Menu, Spinner, useNow } from '../components/ui'
import { clock, money, tokens } from '../lib/format'
import { navigate } from '../lib/router'
import { toast } from '../lib/toast'
import { useTimeline } from '../store/timeline'

type Tab = 'preview' | 'code' | 'history' | 'logs'

const CHAT_MIN = 360
const CHAT_DEFAULT = 460

function readWidth(): number {
  try {
    return Number(localStorage.getItem('codeless.chatWidth')) || CHAT_DEFAULT
  } catch {
    return CHAT_DEFAULT
  }
}

export function ProjectPage({ id }: { id: string }) {
  const { reloadProjects, settings } = useApp()
  const [project, setProject] = useState<Project | null>(null)
  const [missing, setMissing] = useState(false)
  const view = useTimeline(id)
  const { items, state } = view
  const running = !!state.running
  const [tab, setTab] = useState<Tab>('preview')
  const [mobileView, setMobileView] = useState<'chat' | 'work'>('chat')
  const [focusSha, setFocusSha] = useState<string | null>(null)
  const [undo, setUndo] = useState<{ sha: string; message: string } | null>(null)
  const [renaming, setRenaming] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [chatWidth, setChatWidth] = useState(readWidth)

  const loadProject = useCallback(async () => {
    try {
      setProject(await api.project(id))
      setMissing(false)
    } catch {
      setMissing(true)
    }
  }, [id])

  useEffect(() => {
    setProject(null)
    setTab('preview')
    setFocusSha(null)
    loadProject()
  }, [id, loadProject])

  // 每轮结束（以及第一轮开始改名后）刷新项目信息和侧边栏
  const turns = useMemo(() => items.filter((i) => i.kind === 'turn_end').length, [items])
  useEffect(() => {
    loadProject()
    reloadProjects()
  }, [turns, running, loadProject, reloadProjects])

  // 文件版本号：写文件、改文件、命令执行、回滚之后递增，代码和改动面板据此刷新
  const fileVersion = useMemo(() => fileVersionOf(items), [items])
  const lastTurn = useMemo(() => [...items].reverse().find((i) => i.kind === 'turn_end'), [items])
  const reloadSignal = lastTurn && lastTurn.kind === 'turn_end' && lastTurn.checkpoint ? turns : 0

  const send = async (text: string, images: { media_type: string; data: string }[]) => {
    await api.send(id, text, images)
  }

  const stop = useCallback(() => {
    api.stop(id).catch(toast.error)
  }, [id])

  const onDrag = (e: ReactPointerEvent) => {
    const startX = e.clientX
    const start = chatWidth
    const target = e.currentTarget as HTMLElement
    target.setPointerCapture(e.pointerId)
    document.body.classList.add('is-resizing')
    const move = (ev: PointerEvent) => {
      const max = Math.max(CHAT_MIN, window.innerWidth - 480)
      setChatWidth(Math.min(max, Math.max(CHAT_MIN, start + ev.clientX - startX)))
    }
    const up = () => {
      document.body.classList.remove('is-resizing')
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      setChatWidth((w) => {
        try {
          localStorage.setItem('codeless.chatWidth', String(w))
        } catch {
          /* 忽略 */
        }
        return w
      })
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  if (missing) {
    return (
      <div className="main-empty">
        <Empty icon={<TriangleAlert />} title="项目不存在" actions={<Button onClick={() => navigate('/')}>回到首页</Button>}>
          它可能已经被删除了。
        </Empty>
      </div>
    )
  }

  const profile = settings?.profiles.find((p) => p.id === (project?.profile_id || settings.default_profile)) || settings?.profiles[0]
  const errors = state.preview_errors || []
  const usage = state.usage_total || project?.usage
  const tabs: [Tab, string, typeof MonitorPlay][] = [
    ['preview', '预览', MonitorPlay],
    ['code', '代码', FileCode2],
    ['history', '改动', History],
    ['logs', '终端', SquareTerminal],
  ]

  return (
    <div className="project" style={{ ['--chat-w' as string]: `${chatWidth}px` }}>
      <header className="project-header">
        <IconButton className="mobile-only" label="所有项目" onClick={() => navigate('/')}><ArrowLeft /></IconButton>
        <div className="project-title">
          <h2 className="truncate" title={project?.name}>{project?.name || '…'}</h2>
          {running && <span className="badge badge-accent"><span className="dot dot-accent dot-pulse" />工作中</span>}
          {view.connection === 'offline' && <span className="badge badge-warn"><WifiOff />连接断开，正在重连</span>}
        </div>
        <div className="segmented mobile-switch" role="group" aria-label="视图">
          <button type="button" aria-pressed={mobileView === 'chat'} onClick={() => setMobileView('chat')}><MessageSquare />对话</button>
          <button type="button" aria-pressed={mobileView === 'work'} onClick={() => setMobileView('work')}><MonitorPlay />预览</button>
        </div>
        <div className="project-actions">
          {usage && (
            <span className="usage" title={`累计输入 ${usage.input_tokens} token（缓存命中 ${usage.cached_tokens}），输出 ${usage.output_tokens} token`}>
              {tokens(usage.input_tokens + usage.output_tokens)} token
              {!!(usage.cost ?? project?.cost) && ` · ${money(usage.cost ?? project?.cost)}`}
            </span>
          )}
          <IconButton label="下载源码" onClick={() => window.open(api.downloadUrl(id), '_blank')}><Download /></IconButton>
          <Menu
            align="right"
            trigger={({ toggle }) => <IconButton label="更多" onClick={toggle}><Ellipsis /></IconButton>}
          >
            {(close) => (
              <>
                <button type="button" className="menu-item" onClick={() => { close(); setRenaming(true) }}><Pencil />重命名</button>
                <button type="button" className="menu-item menu-item-danger" onClick={() => { close(); setDeleting(true) }}><Trash2 />删除项目</button>
              </>
            )}
          </Menu>
        </div>
      </header>

      <div className={`project-body show-${mobileView}`}>
        <section className="chat">
          {!view.loaded ? (
            <div className="chat-loading"><Spinner /></div>
          ) : items.length === 0 ? (
            <div className="chat-empty">
              <Empty icon={<MessageSquare />} title="开始对话">描述你想做的程序，或者让 agent 修改现有代码。</Empty>
            </div>
          ) : (
            <Timeline
              items={items}
              running={running}
              actions={{
                onViewCheckpoint: (sha) => {
                  setFocusSha(sha)
                  setTab('history')
                  setMobileView('work')
                },
                onUndo: (sha, message) => setUndo({ sha, message }),
              }}
            />
          )}
          <div className="chat-dock">
            {running && <ActivityBar state={state} items={items} onStop={stop} />}
            {state.todos && state.todos.length > 0 && <TodoPanel todos={state.todos} running={running} />}
            {!running && errors.length > 0 && <ErrorsBanner projectId={id} errors={errors} />}
            <Composer
              variant="dock"
              running={running}
              placeholder="继续提要求，比如：把按钮改成圆角、加一个暗色模式"
              profileId={project?.profile_id}
              onProfileChange={async (pid) => {
                try {
                  setProject(await api.updateProject(id, { profile_id: pid }))
                } catch (e) {
                  toast.error(e)
                }
              }}
              onSubmit={send}
              onStop={stop}
              vision={profile?.vision}
            />
          </div>
        </section>

        <div className="splitter" onPointerDown={onDrag} role="separator" aria-orientation="vertical" />

        <section className="workbench">
          <div className="workbench-tabs tabs" role="tablist">
            {tabs.map(([key, label, Icon]) => (
              <button key={key} type="button" role="tab" className="tab" aria-selected={tab === key} onClick={() => setTab(key)}>
                <Icon />
                {label}
                {key === 'preview' && errors.length > 0 && <span className="badge badge-danger">{errors.length}</span>}
              </button>
            ))}
          </div>
          <div className="workbench-body">
            {tab === 'preview' && (
              <PreviewPanel projectId={id} preview={state.preview} reloadSignal={reloadSignal} onShowLogs={() => setTab('logs')} />
            )}
            {tab === 'code' && <CodePanel projectId={id} version={fileVersion} running={running} />}
            {tab === 'history' && <HistoryPanel projectId={id} version={turns + fileVersion} focus={focusSha} running={running} />}
            {tab === 'logs' && <LogsPanel projectId={id} version={fileVersion} />}
          </div>
        </section>
      </div>

      {undo && (
        <Confirm
          title="撤销本轮改动"
          confirmText="撤销"
          onClose={() => setUndo(null)}
          onConfirm={async () => {
            try {
              await api.restore(id, undo.sha)
              toast.success('已撤销，当前代码回到这一轮开始前的样子')
            } catch (e) {
              toast.error(e)
            }
          }}
        >
          代码会回到「{undo.message}」这一轮开始之前的样子。撤销本身也会保存成检查点，之后可以在「改动」里再恢复回来。
        </Confirm>
      )}
      {renaming && project && <RenameDialog project={project} onClose={() => setRenaming(false)} onDone={() => { loadProject(); reloadProjects() }} />}
      {deleting && (
        <Confirm
          title="删除项目"
          danger
          confirmText="删除"
          onClose={() => setDeleting(false)}
          onConfirm={async () => {
            try {
              await api.deleteProject(id)
              await reloadProjects()
              navigate('/', true)
            } catch (e) {
              toast.error(e)
            }
          }}
        >
          确定删除「{project?.name}」吗？代码、对话记录和沙箱都会被删除，无法恢复。
        </Confirm>
      )}
    </div>
  )
}

function fileVersionOf(items: Item[]): number {
  let n = 0
  for (const item of items) {
    if (item.kind === 'tool' && item.status !== 'running' && ['write_file', 'edit_file', 'bash', 'start_process'].includes(item.name)) n++
    if (item.kind === 'notice' && 'restore' in item) n++
    if (item.kind === 'turn_end') n++
  }
  return n
}

// --- 正在做什么 ---

const TOOL_VERB: Record<string, string> = {
  write_file: '写文件', edit_file: '修改文件', bash: '命令', start_process: '启动命令', todo_write: '任务清单',
}

function activityText(a: Activity | null | undefined): string {
  if (!a) return '正在工作'
  switch (a.kind) {
    case 'waiting': return '等待模型响应'
    case 'preparing': return '正在准备沙箱'
    case 'thinking': return '正在思考'
    case 'writing': return '正在回复'
    case 'tool_args': return `正在生成${TOOL_VERB[a.name || ''] || '工具参数'}${a.chars ? `（${tokens(a.chars)} 字符）` : ''}`
    case 'tool': return a.name === 'bash' ? `正在运行 ${a.title}` : `正在${a.title}`
    case 'compacting': return '正在压缩上下文'
    case 'checking': return '正在自动检查预览'
    default: return '正在工作'
  }
}

function ActivityBar({ state, items, onStop }: { state: TimelineState; items: Item[]; onStop: () => void }) {
  const now = useNow(true)
  const started = useMemo(() => {
    for (let i = items.length - 1; i >= 0; i--) {
      const it = items[i]
      if (it.kind === 'turn_end') break
      if (it.kind === 'user' && !it.queued && it.source !== 'autofix') return it.at
    }
    return null
  }, [items])
  const usage = state.turn_usage
  return (
    <div className="activity">
      <Spinner small />
      <span className="activity-text truncate">{activityText(state.activity)}</span>
      {started && <span className="activity-time mono">{clock(now / 1000 - started)}</span>}
      {usage && <span className="activity-usage">{usage.steps} 步 · {tokens(usage.input_tokens + usage.output_tokens)} token</span>}
      <span className="spacer" />
      <button type="button" className="activity-stop" onClick={onStop}>停止 <kbd>Esc</kbd></button>
    </div>
  )
}

function TodoPanel({ todos, running }: { todos: Todo[]; running: boolean }) {
  const [open, setOpen] = useState(running)
  useEffect(() => {
    setOpen(running)
  }, [running])
  const done = todos.filter((t) => t.status === 'completed').length
  const current = todos.find((t) => t.status === 'in_progress')
  return (
    <div className={`todos${open ? ' is-open' : ''}`}>
      <button type="button" className="todos-head" onClick={() => setOpen(!open)}>
        <ListChecks />
        <span>任务清单</span>
        <span className="todos-count">{done}/{todos.length}</span>
        <span className="todos-bar"><span style={{ width: `${(done / todos.length) * 100}%` }} /></span>
        {!open && current && <span className="todos-current truncate">{current.content}</span>}
      </button>
      {open && (
        <ul className="todos-list">
          {todos.map((t, i) => (
            <li key={i} className={`todo todo-${t.status}`}>
              {t.status === 'completed' ? <CircleCheck /> : t.status === 'in_progress' && running ? <CircleDot /> : <Circle />}
              <span>{t.content}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function ErrorsBanner({ projectId, errors }: { projectId: string; errors: PreviewError[] }) {
  const [busy, setBusy] = useState(false)
  const total = errors.reduce((n, e) => n + e.count, 0)
  const latest = errors[errors.length - 1]
  const mounted = useRef(true)
  useEffect(() => () => {
    mounted.current = false
  }, [])
  return (
    <div className="alert alert-danger errors-banner">
      <TriangleAlert />
      <div className="alert-body">
        <div className="alert-title">预览里出现了 {total} 个错误</div>
        <div className="errors-latest mono truncate" title={latest.message}>{latest.message}</div>
      </div>
      <div className="alert-actions">
        <Button size="sm" variant="ghost" onClick={() => api.clearErrors(projectId).catch(toast.error)}>忽略</Button>
        <Button
          size="sm"
          variant="primary"
          icon={<Wrench />}
          loading={busy}
          onClick={async () => {
            setBusy(true)
            try {
              await api.fix(projectId)
            } catch (e) {
              toast.error(e)
            } finally {
              if (mounted.current) setBusy(false)
            }
          }}
        >
          让 agent 修复
        </Button>
      </div>
    </div>
  )
}

// 对话时间线：用户消息、思考、回答、工具调用、提示、每轮小结
import {
  ArrowDown, Brain, ChevronRight, CircleAlert, CircleCheck, CircleStop, CircleX, FilePenLine, FilePlus2,
  FileText, FolderTree, GitCompare, Info, Play, ScanEye, ScrollText, Search, SquareTerminal, TriangleAlert,
  Undo2, Wrench,
} from 'lucide-react'
import { type ReactNode, memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { Item, NoticeItem, TextItem, ToolItem, TurnEndItem, UserItem } from '../api'
import { duration, money, tokens } from '../lib/format'
import { renderMarkdown } from '../lib/markdown'
import { DiffStat, DiffView } from './Diff'
import { Button, Modal, Spinner } from './ui'

export interface TimelineActions {
  onViewCheckpoint: (sha: string) => void
  onUndo: (sha: string, message: string) => void
}

type Block = { type: 'item'; item: Item } | { type: 'tools'; items: ToolItem[] }

function toBlocks(items: Item[]): Block[] {
  const blocks: Block[] = []
  for (const item of items) {
    const last = blocks[blocks.length - 1]
    if (item.kind === 'tool') {
      if (last?.type === 'tools') last.items.push(item)
      else blocks.push({ type: 'tools', items: [item] })
    } else {
      blocks.push({ type: 'item', item })
    }
  }
  return blocks
}

export function Timeline({ items, running, actions }: { items: Item[]; running: boolean; actions: TimelineActions }) {
  const scroller = useRef<HTMLDivElement>(null)
  const stick = useRef(true)
  const [showJump, setShowJump] = useState(false)
  const blocks = useMemo(() => toBlocks(items), [items])

  // 用户停在底部附近时跟随新内容滚动；往上翻了就不打扰，给一个回到底部的按钮
  const onScroll = () => {
    const el = scroller.current
    if (!el) return
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    stick.current = near
    setShowJump(!near)
  }

  // 用户自己发了新消息时，不管之前翻到哪里都回到底部
  const lastUserId = useMemo(() => {
    for (let i = items.length - 1; i >= 0; i--) {
      const it = items[i]
      if (it.kind === 'user' && it.source !== 'autofix') return it.id
    }
    return ''
  }, [items])
  const seenUser = useRef(lastUserId)
  useLayoutEffect(() => {
    const el = scroller.current
    if (seenUser.current !== lastUserId) {
      seenUser.current = lastUserId
      stick.current = true
      setShowJump(false)
    }
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }, [items, lastUserId])

  useEffect(() => {
    const el = scroller.current
    if (el) el.scrollTop = el.scrollHeight
  }, [])

  const jump = () => {
    const el = scroller.current
    if (!el) return
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
    stick.current = true
  }

  return (
    <div className="timeline-wrap">
      <div className="timeline" ref={scroller} onScroll={onScroll}>
        <div className="timeline-inner">
          {blocks.map((block) =>
            block.type === 'tools' ? (
              <ToolGroup key={block.items[0].id} items={block.items} />
            ) : (
              <ItemView key={block.item.id} item={block.item} actions={actions} running={running} />
            ),
          )}
        </div>
      </div>
      {showJump && (
        <button type="button" className="jump-bottom" onClick={jump} aria-label="回到底部">
          <ArrowDown />
        </button>
      )}
    </div>
  )
}

const ItemView = memo(function ItemView({ item, actions, running }: { item: Item; actions: TimelineActions; running: boolean }) {
  switch (item.kind) {
    case 'user':
      return item.source === 'autofix' ? <AutofixMessage item={item} /> : <UserMessage item={item} />
    case 'assistant':
      return <AssistantMessage item={item} />
    case 'thinking':
      return <Thinking item={item} />
    case 'notice':
      return <Notice item={item} />
    case 'turn_end':
      return <TurnEnd item={item} actions={actions} running={running} />
    default:
      return null
  }
})

// --- 用户 ---

function UserMessage({ item }: { item: UserItem }) {
  const [zoom, setZoom] = useState<string | null>(null)
  return (
    <div className={`msg-user${item.discarded ? ' is-discarded' : ''}`}>
      <div className="msg-user-bubble">
        {item.images && item.images.length > 0 && (
          <div className="msg-images">
            {item.images.map((src) => (
              <button type="button" key={src} onClick={() => setZoom(src)}>
                <img src={src} alt="附图" loading="lazy" />
              </button>
            ))}
          </div>
        )}
        {item.text && <div className="msg-user-text">{item.text}</div>}
      </div>
      {item.queued && <div className="msg-user-note">排队中，会在 agent 下一步时插入</div>}
      {item.discarded && <div className="msg-user-note">已停止，这条没有发出</div>}
      {zoom && <ImageViewer src={zoom} onClose={() => setZoom(null)} />}
    </div>
  )
}

function AutofixMessage({ item }: { item: UserItem }) {
  const match = item.text.match(/第 (\d+)\/(\d+) 次/)
  const problems = item.text.split('\n').filter((l) => l.startsWith('- ')).map((l) => l.slice(2))
  return (
    <div className="msg-autofix">
      <div className="msg-autofix-head">
        <Wrench />
        <span>自动检查发现问题，交给 agent 修复{match ? `（第 ${match[1]}/${match[2]} 次）` : ''}</span>
      </div>
      {problems.length > 0 && (
        <ul className="msg-autofix-list">
          {problems.slice(0, 6).map((p, i) => <li key={i}>{p}</li>)}
        </ul>
      )}
    </div>
  )
}

// --- 回答与思考 ---

function AssistantMessage({ item }: { item: TextItem }) {
  const html = useMemo(() => renderMarkdown(item.text || ''), [item.text])
  return (
    <div className={`msg-assistant${item.streaming ? ' is-streaming' : ''}`}>
      <div className="md" dangerouslySetInnerHTML={{ __html: html }} />
      {item.interrupted && <div className="msg-interrupted">（已中断）</div>}
    </div>
  )
}

function Thinking({ item }: { item: TextItem }) {
  const [open, setOpen] = useState<boolean | null>(null)
  const expanded = open ?? !!item.streaming
  const body = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    if (item.streaming && body.current) body.current.scrollTop = body.current.scrollHeight
  }, [item.text, item.streaming])
  const preview = (item.text || '').replace(/\s+/g, ' ').trim()
  return (
    <div className={`thinking${expanded ? ' is-open' : ''}`}>
      <button type="button" className="thinking-head" onClick={() => setOpen(!expanded)}>
        {item.streaming ? <Spinner small /> : <Brain />}
        <span className="thinking-label">{item.streaming ? '思考中' : '思考过程'}</span>
        {!expanded && <span className="thinking-preview truncate">{preview}</span>}
        <ChevronRight className="chevron" />
      </button>
      {expanded && <div className="thinking-body" ref={body}>{item.text}</div>}
    </div>
  )
}

// --- 工具 ---

const TOOL_ICONS: Record<string, typeof FileText> = {
  list_files: FolderTree,
  read_file: FileText,
  write_file: FilePlus2,
  edit_file: FilePenLine,
  search: Search,
  bash: SquareTerminal,
  start_process: Play,
  process_logs: ScrollText,
  stop_process: CircleStop,
  check_preview: ScanEye,
}

function ToolGroup({ items }: { items: ToolItem[] }) {
  return (
    <div className="tool-group">
      {items.map((item) => <ToolRow key={item.id} item={item} />)}
    </div>
  )
}

const ToolRow = memo(function ToolRow({ item }: { item: ToolItem }) {
  const [open, setOpen] = useState<boolean | null>(null)
  const live = item.status === 'running' && item.name === 'bash' && !!item.output
  const expanded = open ?? live
  const Icon = TOOL_ICONS[item.name] || Wrench
  const detail = item.detail || {}
  const seconds = item.ended ? item.ended - item.at : undefined
  const isCommand = item.name === 'bash'
  return (
    <div className={`tool tool-${item.status}${expanded ? ' is-open' : ''}`}>
      <button type="button" className="tool-head" onClick={() => setOpen(!expanded)}>
        <Icon className="tool-icon" />
        <span className={`tool-title truncate${isCommand ? ' mono' : ''}`}>{item.title}</span>
        <span className="tool-meta">
          {(item.name === 'write_file' || item.name === 'edit_file') && item.status === 'ok' && (
            <DiffStat additions={detail.additions} deletions={detail.deletions} />
          )}
          {isCommand && typeof detail.exit_code === 'number' && detail.exit_code !== 0 && (
            <span className="badge badge-danger">退出码 {detail.exit_code}</span>
          )}
          {item.name === 'check_preview' && item.status !== 'running' && (
            <span className={`badge ${detail.ok ? 'badge-success' : 'badge-danger'}`}>
              {detail.ok ? '正常' : `${(detail.problems || []).length || 1} 个问题`}
            </span>
          )}
          {seconds !== undefined && seconds >= 1 && <span className="tool-time">{seconds.toFixed(seconds < 10 ? 1 : 0)}s</span>}
          <ToolStatus status={item.status} />
        </span>
      </button>
      {expanded && <ToolBody item={item} />}
    </div>
  )
})

function ToolStatus({ status }: { status: ToolItem['status'] }) {
  if (status === 'running') return <Spinner small />
  if (status === 'ok') return <CircleCheck className="tool-status tool-status-ok" />
  if (status === 'error') return <CircleX className="tool-status tool-status-error" />
  return <CircleAlert className="tool-status tool-status-warn" />
}

function ToolBody({ item }: { item: ToolItem }) {
  const detail = item.detail || {}
  const [zoom, setZoom] = useState(false)
  if ((item.name === 'write_file' || item.name === 'edit_file') && detail.diff) {
    return <div className="tool-body tool-body-flush"><DiffView diff={detail.diff} maxLines={400} /></div>
  }
  if (item.name === 'bash') {
    return (
      <div className="tool-body tool-body-flush">
        <Terminal text={`$ ${String(item.args?.command ?? '')}\n${item.output || (item.status === 'running' ? '' : '（无输出）')}`} />
      </div>
    )
  }
  if (item.name === 'check_preview') {
    return (
      <div className="tool-body">
        {detail.screenshot && (
          <button type="button" className="shot" onClick={() => setZoom(true)}>
            <img src={detail.screenshot} alt="预览截图" loading="lazy" />
          </button>
        )}
        <pre className="tool-output">{item.output}</pre>
        {zoom && <ImageViewer src={detail.screenshot} onClose={() => setZoom(false)} />}
      </div>
    )
  }
  return (
    <div className="tool-body">
      <pre className="tool-output">{item.output || (item.status === 'running' ? '…' : '（无输出）')}</pre>
    </div>
  )
}

export function Terminal({ text, className = '' }: { text: string; className?: string }) {
  const ref = useRef<HTMLPreElement>(null)
  useLayoutEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [text])
  return <pre ref={ref} className={`terminal ${className}`}>{text}</pre>
}

// --- 提示与小结 ---

function Notice({ item }: { item: NoticeItem }) {
  const icons: Record<NoticeItem['level'], ReactNode> = {
    info: <Info />,
    warn: <TriangleAlert />,
    error: <CircleX />,
    success: <CircleCheck />,
  }
  return (
    <div className={`notice notice-${item.level}`}>
      {icons[item.level]}
      <span>{item.text}</span>
    </div>
  )
}

const OUTCOME: Record<TurnEndItem['outcome'], string> = {
  done: '',
  stopped: '已停止',
  error: '出错结束',
  limit: '达到上限',
  refused: '模型拒绝',
}

function TurnEnd({ item, actions, running }: { item: TurnEndItem; actions: TimelineActions; running: boolean }) {
  const u = item.usage
  const cp = item.checkpoint
  return (
    <div className="turn-end">
      <div className="turn-end-stats">
        {OUTCOME[item.outcome] && <span className={`badge ${item.outcome === 'error' ? 'badge-danger' : 'badge-warn'}`}>{OUTCOME[item.outcome]}</span>}
        {u && (
          <span title={`输入 ${u.input_tokens}（缓存命中 ${u.cached_tokens}）· 输出 ${u.output_tokens}`}>
            {duration(u.seconds)} · {u.steps} 步 · {tokens(u.input_tokens)} 输入 / {tokens(u.output_tokens)} 输出
            {u.cost !== undefined && ` · ${money(u.cost)}`}
          </span>
        )}
      </div>
      {cp && (
        <div className="turn-end-change">
          <span className="turn-end-files">
            改动 {cp.files.length} 个文件 <DiffStat additions={cp.additions} deletions={cp.deletions} />
          </span>
          <Button size="sm" variant="ghost" icon={<GitCompare />} onClick={() => actions.onViewCheckpoint(cp.sha)}>查看改动</Button>
          <Button size="sm" variant="ghost" icon={<Undo2 />} disabled={running} onClick={() => actions.onUndo(`${cp.sha}^`, cp.message)}>撤销本轮</Button>
        </div>
      )}
    </div>
  )
}

export function ImageViewer({ src, onClose }: { src: string; onClose: () => void }) {
  return (
    <Modal title="查看图片" onClose={onClose}>
      <img className="image-viewer" src={src} alt="" />
    </Modal>
  )
}

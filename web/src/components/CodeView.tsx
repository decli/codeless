// 代码面板：文件树 + 高亮查看，可以手动编辑保存
import { ChevronRight, File, FileCode2, Folder, FolderOpen, Pencil, RefreshCw, Save } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type FileEntry } from '../api'
import { bytes } from '../lib/format'
import { highlight, languageOf } from '../lib/highlight'
import { toast } from '../lib/toast'
import { Button, Empty, IconButton, Spinner } from './ui'

interface TreeNode {
  name: string
  path: string
  type: 'file' | 'dir'
  size?: number
  children: TreeNode[]
}

function buildTree(entries: FileEntry[]): TreeNode[] {
  const root: TreeNode = { name: '', path: '', type: 'dir', children: [] }
  const dirs = new Map<string, TreeNode>([['', root]])
  for (const e of entries) {
    const parts = e.path.split('/')
    const name = parts.pop() || ''
    const parent = dirs.get(parts.join('/')) || root
    const node: TreeNode = { name, path: e.path, type: e.type, size: e.size, children: [] }
    parent.children.push(node)
    if (e.type === 'dir') dirs.set(e.path, node)
  }
  const sort = (nodes: TreeNode[]) => {
    nodes.sort((a, b) => (a.type === b.type ? a.name.localeCompare(b.name) : a.type === 'dir' ? -1 : 1))
    nodes.forEach((n) => sort(n.children))
  }
  sort(root.children)
  return root.children
}

const IMAGE = /\.(png|jpe?g|gif|webp|ico|bmp|avif)$/i

export function CodePanel({ projectId, version, running }: { projectId: string; version: number; running: boolean }) {
  const [entries, setEntries] = useState<FileEntry[] | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [openDirs, setOpenDirs] = useState<Set<string>>(new Set())

  const load = useCallback(async () => {
    try {
      const { files } = await api.files(projectId)
      setEntries(files)
    } catch (e) {
      toast.error(e)
    }
  }, [projectId])

  useEffect(() => {
    load()
  }, [load, version])

  const tree = useMemo(() => buildTree(entries || []), [entries])

  // 默认选中一个有代表性的文件
  useEffect(() => {
    if (selected || !entries?.length) return
    const files = entries.filter((e) => e.type === 'file').map((e) => e.path)
    const pick = ['AGENTS.md', 'README.md', 'src/App.tsx', 'main.py', 'app.py', 'index.html', 'server.js']
      .find((p) => files.includes(p)) || files[0]
    if (pick) {
      setSelected(pick)
      const parts = pick.split('/')
      setOpenDirs(new Set(parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join('/'))))
    }
  }, [entries, selected])

  const toggle = (path: string) => {
    setOpenDirs((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  return (
    <div className="panel code">
      <div className="code-tree">
        <div className="code-tree-head">
          <span>文件</span>
          <IconButton label="刷新" size="sm" onClick={load}><RefreshCw /></IconButton>
        </div>
        <div className="code-tree-body">
          {entries === null && <div className="code-tree-hint"><Spinner small /> 加载中</div>}
          {entries?.length === 0 && <div className="code-tree-hint">项目还是空的</div>}
          <TreeList nodes={tree} depth={0} openDirs={openDirs} selected={selected} onToggle={toggle} onSelect={setSelected} />
        </div>
      </div>
      <div className="code-main">
        {selected ? (
          <FileViewer key={selected} projectId={projectId} path={selected} version={version} running={running} />
        ) : (
          <Empty icon={<FileCode2 />} title="选择一个文件">agent 写的代码都在这里，可以查看，也可以手动修改。</Empty>
        )}
      </div>
    </div>
  )
}

function TreeList({ nodes, depth, openDirs, selected, onToggle, onSelect }: {
  nodes: TreeNode[]
  depth: number
  openDirs: Set<string>
  selected: string | null
  onToggle: (path: string) => void
  onSelect: (path: string) => void
}) {
  return (
    <>
      {nodes.map((node) => {
        const open = openDirs.has(node.path)
        return (
          <div key={node.path}>
            <button
              type="button"
              className={`tree-row${selected === node.path ? ' is-selected' : ''}`}
              style={{ paddingLeft: 8 + depth * 14 }}
              onClick={() => (node.type === 'dir' ? onToggle(node.path) : onSelect(node.path))}
              title={node.path}
            >
              {node.type === 'dir' ? (
                <>
                  <ChevronRight className={`tree-chevron${open ? ' is-open' : ''}`} />
                  {open ? <FolderOpen className="tree-icon" /> : <Folder className="tree-icon" />}
                </>
              ) : (
                <>
                  <span className="tree-chevron-space" />
                  <File className="tree-icon" />
                </>
              )}
              <span className="truncate">{node.name}</span>
            </button>
            {node.type === 'dir' && open && (
              <TreeList nodes={node.children} depth={depth + 1} openDirs={openDirs} selected={selected} onToggle={onToggle} onSelect={onSelect} />
            )}
          </div>
        )
      })}
    </>
  )
}

function FileViewer({ projectId, path, version, running }: { projectId: string; path: string; version: number; running: boolean }) {
  const [file, setFile] = useState<{ content: string | null; size: number; binary: boolean } | null>(null)
  const [error, setError] = useState('')
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const isImage = IMAGE.test(path)

  useEffect(() => {
    if (editing || isImage) return
    let cancelled = false
    api.file(projectId, path)
      .then((f) => !cancelled && (setFile(f), setError('')))
      .catch((e) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [projectId, path, version, editing, isImage])

  const html = useMemo(() => (file?.content != null ? highlight(file.content, languageOf(path)) : ''), [file, path])
  const lineCount = useMemo(() => (file?.content ? file.content.split('\n').length : 0), [file])

  const save = async () => {
    setSaving(true)
    try {
      await api.saveFile(projectId, path, draft)
      setFile({ content: draft, size: draft.length, binary: false })
      setEditing(false)
      toast.success('已保存，agent 下一轮会看到你的修改')
    } catch (e) {
      toast.error(e)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="file-view">
      <div className="file-view-head">
        <span className="file-path mono truncate" title={path}>{path}</span>
        {file && !isImage && <span className="faint">{lineCount} 行 · {bytes(file.size)}</span>}
        <span className="spacer" />
        {editing ? (
          <>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>取消</Button>
            <Button size="sm" variant="primary" icon={<Save />} loading={saving} onClick={save}>保存</Button>
          </>
        ) : (
          file?.content != null && (
            <Button
              size="sm"
              variant="ghost"
              icon={<Pencil />}
              disabled={running}
              title={running ? 'agent 工作时不能编辑' : undefined}
              onClick={() => {
                setDraft(file.content || '')
                setEditing(true)
              }}
            >
              编辑
            </Button>
          )
        )}
      </div>
      <div className="file-view-body">
        {error && <div className="file-view-msg">{error}</div>}
        {isImage && <div className="file-image"><img src={`${api.rawUrl(projectId, path)}&v=${version}`} alt={path} /></div>}
        {!isImage && !error && !file && <div className="file-view-msg"><Spinner small /></div>}
        {file?.binary && <div className="file-view-msg">二进制文件，无法以文本显示（{bytes(file.size)}）</div>}
        {editing ? (
          <textarea
            className="code-editor"
            value={draft}
            spellCheck={false}
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === 's') {
                e.preventDefault()
                save()
              }
              if (e.key === 'Tab') {
                e.preventDefault()
                const el = e.currentTarget
                const { selectionStart: a, selectionEnd: b } = el
                const next = `${draft.slice(0, a)}  ${draft.slice(b)}`
                setDraft(next)
                requestAnimationFrame(() => el.setSelectionRange(a + 2, a + 2))
              }
            }}
          />
        ) : (
          file?.content != null && (
            <div className="code-block">
              <div className="code-lines" aria-hidden="true">
                {Array.from({ length: lineCount }, (_, i) => <span key={i}>{i + 1}</span>)}
              </div>
              <pre className="code-text"><code className="hljs" dangerouslySetInnerHTML={{ __html: html }} /></pre>
            </div>
          )
        )}
      </div>
    </div>
  )
}

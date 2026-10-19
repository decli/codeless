import {
  Code2, Ellipsis, Monitor, Moon, PanelLeftClose, Pencil, Plus, Search, Settings, Sun, Trash2,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { api, type Project } from '../api'
import { useApp } from '../app-context'
import { relativeTime } from '../lib/format'
import { navigate } from '../lib/router'
import { type Theme, useTheme } from '../lib/theme'
import { toast } from '../lib/toast'
import { Confirm, IconButton, Menu, Modal, Button } from './ui'

export function Sidebar({ activeId, onCollapse }: { activeId?: string; onCollapse: () => void }) {
  const { projects, info, reloadProjects, openSettings } = useApp()
  const [query, setQuery] = useState('')
  const [theme, setTheme] = useTheme()
  const [renaming, setRenaming] = useState<Project | null>(null)
  const [deleting, setDeleting] = useState<Project | null>(null)

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (projects || []).filter((p) => !q || p.name.toLowerCase().includes(q))
  }, [projects, query])

  const sandboxOk = info?.sandbox_status === 'ok'
  const sandboxChecking = !info || info.sandbox_status === 'checking'

  return (
    <aside className="sidebar">
      <div className="sidebar-top">
        <button type="button" className="brand" onClick={() => navigate('/')}>
          <span className="brand-mark"><Code2 /></span>
          <span className="brand-name">codeless</span>
        </button>
        <IconButton label="收起侧边栏" size="sm" onClick={onCollapse}><PanelLeftClose /></IconButton>
      </div>

      <div className="sidebar-actions">
        <Button block icon={<Plus />} onClick={() => navigate('/')}>新建项目</Button>
        {(projects?.length ?? 0) > 6 && (
          <label className="sidebar-search">
            <Search />
            <input placeholder="搜索项目" value={query} onChange={(e) => setQuery(e.target.value)} />
          </label>
        )}
      </div>

      <div className="sidebar-section-label">项目</div>
      <nav className="project-list">
        {projects === null && <div className="sidebar-hint">加载中…</div>}
        {projects?.length === 0 && <div className="sidebar-hint">还没有项目，在右边描述你想做的东西就能开始。</div>}
        {projects && projects.length > 0 && shown.length === 0 && <div className="sidebar-hint">没有匹配的项目</div>}
        {shown.map((p) => (
          <div key={p.id} className={`project-link${p.id === activeId ? ' is-active' : ''}`}>
            <button type="button" className="project-link-main" onClick={() => navigate(`/p/${p.id}`)}>
              <span className="project-link-name truncate">{p.name}</span>
              <span className="project-link-meta">
                {p.running ? <span className="dot dot-accent dot-pulse" title="正在工作" /> : relativeTime(p.updated_at)}
              </span>
            </button>
            <Menu
              align="right"
              trigger={({ toggle, open }) => (
                <IconButton label="更多操作" size="sm" className={`project-link-more${open ? ' is-open' : ''}`} onClick={toggle}>
                  <Ellipsis />
                </IconButton>
              )}
            >
              {(close) => (
                <>
                  <button type="button" className="menu-item" onClick={() => { close(); setRenaming(p) }}>
                    <Pencil /> 重命名
                  </button>
                  <button type="button" className="menu-item menu-item-danger" onClick={() => { close(); setDeleting(p) }}>
                    <Trash2 /> 删除项目
                  </button>
                </>
              )}
            </Menu>
          </div>
        ))}
      </nav>

      <div className="sidebar-bottom">
        <div className="sandbox-status" title={sandboxOk || sandboxChecking ? info?.sandbox : info?.sandbox_status}>
          <span className={`dot ${sandboxChecking ? '' : sandboxOk ? 'dot-success' : 'dot-danger'}`} />
          <span className="truncate">{sandboxChecking ? '正在检查沙箱…' : sandboxOk ? info?.sandbox : '沙箱不可用'}</span>
        </div>
        <div className="sidebar-bottom-row">
          <Button variant="ghost" size="sm" icon={<Settings />} onClick={() => openSettings()}>设置</Button>
          <ThemeSwitch theme={theme} onChange={setTheme} />
        </div>
      </div>

      {renaming && (
        <RenameDialog
          project={renaming}
          onClose={() => setRenaming(null)}
          onDone={reloadProjects}
        />
      )}
      {deleting && (
        <Confirm
          title="删除项目"
          danger
          confirmText="删除"
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            try {
              await api.deleteProject(deleting.id)
              if (deleting.id === activeId) navigate('/', true)
              await reloadProjects()
            } catch (e) {
              toast.error(e)
            }
          }}
        >
          确定删除「{deleting.name}」吗？代码、对话记录和沙箱都会被删除，无法恢复。
        </Confirm>
      )}
    </aside>
  )
}

function ThemeSwitch({ theme, onChange }: { theme: Theme; onChange: (t: Theme) => void }) {
  const options: [Theme, string, typeof Sun][] = [['light', '浅色', Sun], ['dark', '深色', Moon], ['system', '跟随系统', Monitor]]
  return (
    <div className="segmented" role="group" aria-label="主题">
      {options.map(([value, label, Icon]) => (
        <button key={value} type="button" aria-pressed={theme === value} title={label} onClick={() => onChange(value)}>
          <Icon />
        </button>
      ))}
    </div>
  )
}

export function RenameDialog({ project, onClose, onDone }: { project: Project; onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState(project.name)
  const [busy, setBusy] = useState(false)
  const save = async () => {
    if (!name.trim()) return
    setBusy(true)
    try {
      await api.updateProject(project.id, { name: name.trim() })
      onDone()
      onClose()
    } catch (e) {
      toast.error(e)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal
      small
      title="重命名项目"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button variant="primary" loading={busy} disabled={!name.trim()} onClick={save}>保存</Button>
        </>
      }
    >
      <input
        className="input"
        autoFocus
        value={name}
        maxLength={60}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && save()}
      />
    </Modal>
  )
}

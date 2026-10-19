// 改动面板：每轮一个检查点，看改了哪些文件、具体 diff，可以恢复到任意版本
import { GitCommitHorizontal, History as HistoryIcon, RotateCcw } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type FileChange } from '../api'
import { relativeTime } from '../lib/format'
import { toast } from '../lib/toast'
import { DiffStat, DiffView } from './Diff'
import { Button, Confirm, Empty, Spinner } from './ui'

interface CheckpointRow {
  sha: string
  message: string
  time: number
}

const STATUS_LABEL: Record<FileChange['status'], string> = { added: '新增', modified: '修改', deleted: '删除', renamed: '重命名' }

export function HistoryPanel({ projectId, version, focus, running }: {
  projectId: string
  version: number
  focus: string | null
  running: boolean
}) {
  const [rows, setRows] = useState<CheckpointRow[] | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [restoring, setRestoring] = useState<CheckpointRow | null>(null)

  const load = useCallback(async () => {
    try {
      setRows(await api.checkpoints(projectId))
    } catch (e) {
      toast.error(e)
    }
  }, [projectId])

  useEffect(() => {
    load()
  }, [load, version])

  useEffect(() => {
    if (focus) setSelected(focus)
  }, [focus])

  useEffect(() => {
    if (!selected && rows?.length) setSelected(rows[0].sha)
  }, [rows, selected])

  if (rows === null) return <div className="panel center"><Spinner /></div>
  if (rows.length <= 1) {
    return (
      <div className="panel">
        <Empty icon={<HistoryIcon />} title="还没有改动记录">agent 每完成一轮就会保存一个检查点，可以在这里查看改动，或者把代码恢复到之前的版本。</Empty>
      </div>
    )
  }

  return (
    <div className="panel history">
      <div className="history-list">
        {rows.map((row, i) => (
          <button
            type="button"
            key={row.sha}
            className={`history-row${selected === row.sha ? ' is-selected' : ''}`}
            onClick={() => setSelected(row.sha)}
          >
            <GitCommitHorizontal className="history-icon" />
            <span className="history-text">
              <span className="history-message truncate">{row.message}</span>
              <span className="history-meta">
                {relativeTime(row.time)} · <span className="mono">{row.sha.slice(0, 7)}</span>
                {i === 0 && <span className="badge badge-accent">当前</span>}
              </span>
            </span>
          </button>
        ))}
      </div>
      <div className="history-detail">
        {selected && (
          <CheckpointDetail
            key={selected}
            projectId={projectId}
            sha={selected}
            isCurrent={rows[0]?.sha === selected}
            running={running}
            onRestore={() => setRestoring(rows.find((r) => r.sha === selected) || null)}
          />
        )}
      </div>
      {restoring && (
        <Confirm
          title="恢复到这个版本"
          confirmText="恢复"
          onClose={() => setRestoring(null)}
          onConfirm={async () => {
            try {
              await api.restore(projectId, restoring.sha)
              toast.success('已恢复，当前改动也保存成了检查点，随时可以再恢复回来')
              setSelected(null)
              await load()
            } catch (e) {
              toast.error(e)
            }
          }}
        >
          项目代码会恢复到「{restoring.message}」完成时的样子。这个操作会生成一个新的检查点，之后的版本不会丢，随时可以再恢复回来。
        </Confirm>
      )}
    </div>
  )
}

function CheckpointDetail({ projectId, sha, isCurrent, running, onRestore }: {
  projectId: string
  sha: string
  isCurrent: boolean
  running: boolean
  onRestore: () => void
}) {
  const [files, setFiles] = useState<FileChange[] | null>(null)
  const [diffs, setDiffs] = useState<Record<string, string>>({})
  const [open, setOpen] = useState<string | null>(null)

  useEffect(() => {
    api.checkpoint(projectId, sha).then((r) => {
      setFiles(r.files)
      if (r.files.length) setOpen(r.files[0].path)
    }).catch(toast.error)
  }, [projectId, sha])

  useEffect(() => {
    if (!open || diffs[open] !== undefined) return
    api.diff(projectId, sha, open).then((r) => setDiffs((d) => ({ ...d, [open]: r.diff }))).catch(toast.error)
  }, [open, diffs, projectId, sha])

  return (
    <div className="checkpoint">
      <div className="checkpoint-head">
        <span className="faint">{files ? `${files.length} 个文件` : '加载中…'}</span>
        <span className="spacer" />
        {!isCurrent && (
          <Button size="sm" icon={<RotateCcw />} disabled={running} onClick={onRestore}>恢复到这个版本</Button>
        )}
      </div>
      {files?.length === 0 && <div className="checkpoint-empty">这个检查点没有文件改动</div>}
      {files?.map((f) => (
        <div key={f.path} className={`change${open === f.path ? ' is-open' : ''}`}>
          <button type="button" className="change-head" onClick={() => setOpen(open === f.path ? null : f.path)}>
            <span className={`change-status change-${f.status}`}>{STATUS_LABEL[f.status]}</span>
            <span className="mono truncate">{f.path}</span>
            <span className="spacer" />
            {f.binary ? <span className="faint">二进制</span> : <DiffStat additions={f.additions} deletions={f.deletions} />}
          </button>
          {open === f.path && !f.binary && (
            diffs[f.path] === undefined ? <div className="change-loading"><Spinner small /></div> : <DiffView diff={diffs[f.path]} />
          )}
        </div>
      ))}
    </div>
  )
}

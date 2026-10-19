// 终端面板：后台进程（开发服务器等）的实时日志
import { CircleStop, Play, SquareTerminal } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, type ProcessInfo } from '../api'
import { toast } from '../lib/toast'
import { Terminal } from './Timeline'
import { Button, Empty } from './ui'

const MAX_LOG = 300_000

export function LogsPanel({ projectId, version }: { projectId: string; version: number }) {
  const [processes, setProcesses] = useState<ProcessInfo[]>([])
  const [name, setName] = useState('preview')

  useEffect(() => {
    let alive = true
    const load = () => api.processes(projectId).then((list) => alive && setProcesses(list)).catch(() => undefined)
    load()
    const timer = setInterval(load, 3000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [projectId, version])

  useEffect(() => {
    if (processes.length && !processes.some((p) => p.name === name)) setName(processes[0].name)
  }, [processes, name])

  const current = processes.find((p) => p.name === name)

  if (!processes.length) {
    return (
      <div className="panel">
        <Empty icon={<SquareTerminal />} title="没有后台进程">agent 启动开发服务器或接口服务后，它们的输出会实时显示在这里。</Empty>
      </div>
    )
  }

  return (
    <div className="panel logs">
      <div className="panel-toolbar">
        <div className="segmented" role="group" aria-label="进程">
          {processes.map((p) => (
            <button key={p.name} type="button" aria-pressed={p.name === name} onClick={() => setName(p.name)}>
              <span className={`dot ${p.running ? 'dot-success' : 'dot-danger'}`} />
              {p.name}
            </button>
          ))}
        </div>
        {current && <span className="logs-command mono truncate" title={current.command}>{current.command}</span>}
        <span className="spacer" />
        {current?.name === 'preview' && (
          <Button size="sm" variant="ghost" icon={<Play />} onClick={() => api.restartPreview(projectId).catch(toast.error)}>重启</Button>
        )}
        {current?.running && (
          <Button size="sm" variant="ghost" icon={<CircleStop />} onClick={() => api.stopProcess(projectId, current.name).catch(toast.error)}>停止</Button>
        )}
      </div>
      <LogStream key={`${projectId}:${name}`} projectId={projectId} name={name} />
    </div>
  )
}

function LogStream({ projectId, name }: { projectId: string; name: string }) {
  const [text, setText] = useState('')
  useEffect(() => {
    const source = new EventSource(`/api/projects/${projectId}/processes/${encodeURIComponent(name)}/logs`)
    source.addEventListener('reset', (e) => setText(JSON.parse((e as MessageEvent).data).log || ''))
    source.addEventListener('log', (e) => {
      const chunk = JSON.parse((e as MessageEvent).data) as string
      setText((prev) => {
        const next = prev + chunk
        return next.length > MAX_LOG ? next.slice(-MAX_LOG) : next
      })
    })
    return () => source.close()
  }, [projectId, name])
  return <Terminal className="logs-terminal" text={text || '（暂无输出）'} />
}

// 预览面板：iframe 加载 <项目>.<预览域名>，地址栏、刷新、手机宽度、在新标签页打开
import { ExternalLink, Monitor, MonitorPlay, Play, RotateCw, ScrollText, Smartphone, TriangleAlert } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api, type PreviewInfo } from '../api'
import { useApp } from '../app-context'
import { toast } from '../lib/toast'
import { Button, Empty, IconButton, Spinner } from './ui'

export function previewOrigin(projectId: string, domain: string): string {
  const port = location.port ? `:${location.port}` : ''
  return `${location.protocol}//${projectId}.${domain}${port}`
}

export function PreviewPanel({ projectId, preview, reloadSignal, onShowLogs }: {
  projectId: string
  preview?: PreviewInfo
  reloadSignal: number
  onShowLogs: () => void
}) {
  const { info } = useApp()
  const [path, setPath] = useState('/')
  const [draft, setDraft] = useState('/')
  const [device, setDevice] = useState<'desktop' | 'mobile'>('desktop')
  const [nonce, setNonce] = useState(0)
  const [loading, setLoading] = useState(true)
  const status = preview?.status ?? 'none'
  const origin = info ? previewOrigin(projectId, info.preview_domain) : ''

  // 预览进程从启动中变成运行中、或者一轮改动结束时，刷新一次
  const prevStatus = useRef(status)
  useEffect(() => {
    if (prevStatus.current !== 'running' && status === 'running') setNonce((n) => n + 1)
    prevStatus.current = status
  }, [status])
  useEffect(() => {
    if (reloadSignal) setNonce((n) => n + 1)
  }, [reloadSignal])
  useEffect(() => {
    setLoading(true)
  }, [nonce])

  // 页面内路由变化时同步地址栏（注入脚本通过 postMessage 告诉我们）
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.origin !== origin || e.data?.source !== 'codeless-preview') return
      if (e.data.kind === 'nav' && typeof e.data.path === 'string') setDraft(e.data.path)
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [origin])

  const go = (target: string) => {
    const clean = target.startsWith('/') ? target : `/${target}`
    setPath(clean)
    setDraft(clean)
    setNonce((n) => n + 1)
  }

  const restart = async () => {
    try {
      await api.restartPreview(projectId)
    } catch (e) {
      toast.error(e)
    }
  }

  const src = `${origin}${path}`
  const live = status === 'running' || status === 'starting' || status === 'idle'

  return (
    <div className="panel preview">
      <div className="panel-toolbar">
        <PreviewStatus status={status} />
        <form
          className="address"
          onSubmit={(e) => {
            e.preventDefault()
            go(draft || '/')
          }}
        >
          <span className="address-host truncate">{origin.replace(/^https?:\/\//, '')}</span>
          <input
            className="address-path"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            spellCheck={false}
            aria-label="页面路径"
          />
        </form>
        <div className="segmented" role="group" aria-label="设备">
          <button type="button" aria-pressed={device === 'desktop'} title="桌面宽度" onClick={() => setDevice('desktop')}>
            <Monitor />
          </button>
          <button type="button" aria-pressed={device === 'mobile'} title="手机宽度" onClick={() => setDevice('mobile')}>
            <Smartphone />
          </button>
        </div>
        <IconButton label="刷新" size="sm" disabled={!live} onClick={() => setNonce((n) => n + 1)}><RotateCw /></IconButton>
        <IconButton label="在新标签页打开" size="sm" disabled={!live} onClick={() => window.open(src, '_blank', 'noopener')}>
          <ExternalLink />
        </IconButton>
      </div>
      <div className={`preview-stage preview-${device}`}>
        {status === 'none' && (
          <Empty icon={<MonitorPlay />} title="还没有可预览的内容">
            让 agent 做一个网页、小游戏或接口服务，它把服务启动起来后会显示在这里。命令行程序的运行结果会直接出现在对话里。
          </Empty>
        )}
        {status === 'exited' && (
          <Empty
            icon={<TriangleAlert />}
            title="预览进程已退出"
            actions={
              <>
                <Button icon={<ScrollText />} onClick={onShowLogs}>查看日志</Button>
                <Button variant="primary" icon={<Play />} onClick={restart}>重新启动</Button>
              </>
            }
          >
            {preview?.exit_code != null && <>退出码 {preview.exit_code}。</>}
            可以看看日志里的报错，或者直接告诉 agent 修复。
          </Empty>
        )}
        {live && (
          <div className="preview-frame-wrap">
            {(loading || status !== 'running') && (
              <div className="preview-loading">
                <Spinner />
                <span>{status === 'running' ? '正在加载页面…' : '正在启动预览…'}</span>
              </div>
            )}
            {status === 'running' && (
              <iframe
                key={nonce}
                className="preview-frame"
                src={src}
                title="预览"
                onLoad={() => setLoading(false)}
                sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads"
                allow="clipboard-read; clipboard-write; fullscreen"
              />
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function PreviewStatus({ status }: { status: PreviewInfo['status'] }) {
  const map: Record<PreviewInfo['status'], [string, string]> = {
    none: ['dot', '未启动'],
    idle: ['dot dot-warn', '待启动'],
    starting: ['dot dot-warn dot-pulse', '启动中'],
    running: ['dot dot-success', '运行中'],
    exited: ['dot dot-danger', '已退出'],
  }
  const [cls, label] = map[status]
  return (
    <span className="preview-status" title={label}>
      <span className={cls} />
      <span>{label}</span>
    </span>
  )
}

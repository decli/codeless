// 首页：描述想做的东西，直接开始一个新项目
import { ArrowRight, Code2, Gamepad2, LayoutDashboard, NotebookPen, ServerCog, Settings, Terminal, Timer, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api'
import { useApp } from '../app-context'
import { Composer } from '../components/Composer'
import { Button, IconButton } from '../components/ui'
import { relativeTime } from '../lib/format'
import { navigate } from '../lib/router'

const EXAMPLES = [
  { icon: Timer, title: '番茄钟', prompt: '做一个番茄钟网页：25 分钟专注、5 分钟休息，可以暂停和重置，记录今天完成了几个番茄，界面简洁好看，适配手机。' },
  { icon: Gamepad2, title: '贪吃蛇小游戏', prompt: '做一个贪吃蛇小游戏：方向键和触屏滑动都能控制，有计分和最高分记录，速度随分数变快。' },
  { icon: NotebookPen, title: '笔记应用', prompt: '用 FastAPI 做一个 Markdown 笔记应用：后端用 SQLite 存储，支持新建、编辑、删除、搜索笔记，前端可以实时预览 Markdown。' },
  { icon: LayoutDashboard, title: '数据看板', prompt: '做一个销售数据看板：用模拟数据展示月度销售额趋势、各地区占比、热销商品排行，图表要能交互，不要用外部 CDN。' },
  { icon: ServerCog, title: '接口服务', prompt: '用 Express 写一个待办事项的 REST API（增删改查，数据存在 JSON 文件里），再做一个简单的网页调用这些接口。' },
  { icon: Terminal, title: '命令行工具', prompt: '写一个 Python 命令行工具：统计一个文本文件里每个词出现的次数，输出前 20 个高频词和柱状图，然后用一段示例文本跑一下给我看结果。' },
]

export function Home() {
  const { info, settings, projects, openSettings, reloadProjects } = useApp()
  const [profileId, setProfileId] = useState('')
  const [seed, setSeed] = useState<{ text: string; n: number } | null>(null)
  const noModel = settings !== null && settings.profiles.length === 0
  const sandboxDown = info && info.sandbox_status !== 'ok' && info.sandbox_status !== 'checking'
  const profile = settings?.profiles.find((p) => p.id === (profileId || settings.default_profile))

  const start = async (text: string, images: { media_type: string; data: string }[]) => {
    if (noModel) {
      openSettings('models')
      return false
    }
    const project = await api.createProject({ prompt: text, images, profile_id: profileId || undefined })
    await reloadProjects()
    navigate(`/p/${project.id}`)
  }

  return (
    <div className="home">
      {/* 窄屏没有侧边栏，顶部补一条品牌和设置入口 */}
      <div className="home-topbar">
        <span className="brand">
          <span className="brand-mark"><Code2 /></span>
          <span className="brand-name">codeless</span>
        </span>
        <IconButton label="设置" onClick={() => openSettings()}><Settings /></IconButton>
      </div>
      <div className="home-inner">
        <div className="home-hero">
          <h1 className="home-title">想做点什么？</h1>
          <p className="home-sub">描述你想要的程序，codeless 会在沙箱里写代码、运行、检查，有问题自己修好。支持 Python 和 Node.js。</p>
        </div>

        {noModel && (
          <div className="alert alert-warn">
            <TriangleAlert />
            <div className="alert-body">
              <div className="alert-title">还没有配置模型</div>
              先添加一个模型：DeepSeek、通义千问、Kimi、Claude 等都可以，填上 API Key 就能用。
            </div>
            <div className="alert-actions">
              <Button size="sm" variant="primary" onClick={() => openSettings('models')}>去配置</Button>
            </div>
          </div>
        )}
        {sandboxDown && (
          <div className="alert alert-danger">
            <TriangleAlert />
            <div className="alert-body">
              <div className="alert-title">沙箱不可用</div>
              {info.sandbox_status}
            </div>
          </div>
        )}

        {/* 点示例时把文字填进输入框：换 key 重建输入框最简单 */}
        <Composer
          key={seed?.n ?? 0}
          variant="hero"
          autoFocus
          initialText={seed?.text}
          placeholder="比如：做一个记账网页，可以按月份统计支出，数据存在浏览器里"
          profileId={profileId}
          onProfileChange={setProfileId}
          onSubmit={start}
          vision={profile?.vision}
        />

        <div className="examples">
          {EXAMPLES.map((ex) => (
            <button key={ex.title} type="button" className="example" onClick={() => setSeed({ text: ex.prompt, n: Date.now() })}>
              <ex.icon />
              <span>{ex.title}</span>
            </button>
          ))}
        </div>

        {projects && projects.length > 0 && (
          <div className="recent">
            <div className="recent-head">
              <span>最近的项目</span>
            </div>
            <div className="recent-grid">
              {projects.slice(0, 6).map((p) => (
                <button key={p.id} type="button" className="recent-card" onClick={() => navigate(`/p/${p.id}`)}>
                  <span className="recent-name truncate">{p.name}</span>
                  <span className="recent-meta">
                    {p.running ? <><span className="dot dot-accent dot-pulse" />正在工作</> : relativeTime(p.updated_at)}
                    <ArrowRight className="recent-arrow" />
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

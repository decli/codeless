// 设置：模型档案（任意 OpenAI 兼容服务或 Anthropic 协议）和 agent 参数
import { ArrowLeft, Bot, Check, ChevronDown, Cpu, Eye, Info, Pencil, Plus, Server, Star, Trash2, Zap } from 'lucide-react'
import { type ReactNode, useEffect, useState } from 'react'
import { type AgentSettings, api, type Preset, type Profile } from '../api'
import { type SettingsTab, useApp } from '../app-context'
import { toast } from '../lib/toast'
import { Button, Confirm, Field, IconButton, Modal, Spinner, Switch } from './ui'

export function SettingsModal() {
  const { settingsTab, closeSettings, openSettings } = useApp()
  if (!settingsTab) return null
  const tabs: [SettingsTab, string, ReactNode][] = [
    ['models', '模型', <Cpu key="m" />],
    ['agent', 'Agent', <Bot key="a" />],
    ['about', '关于', <Info key="i" />],
  ]
  return (
    <Modal title="设置" onClose={closeSettings}>
      <div className="settings">
        <nav className="settings-nav">
          {tabs.map(([key, label, icon]) => (
            <button key={key} type="button" className={`settings-nav-item${settingsTab === key ? ' is-active' : ''}`} onClick={() => openSettings(key)}>
              {icon}
              {label}
            </button>
          ))}
        </nav>
        <div className="settings-content">
          {settingsTab === 'models' && <ModelsSection />}
          {settingsTab === 'agent' && <AgentSection />}
          {settingsTab === 'about' && <AboutSection />}
        </div>
      </div>
    </Modal>
  )
}

// --- 模型 ---

function ModelsSection() {
  const { settings, reloadSettings } = useApp()
  const [editing, setEditing] = useState<Partial<Profile> | null>(null)
  const [deleting, setDeleting] = useState<Profile | null>(null)

  if (!settings) return <Spinner />
  if (editing) {
    return (
      <ProfileForm
        initial={editing}
        presets={settings.presets}
        onBack={() => setEditing(null)}
        onSaved={async () => {
          await reloadSettings()
          setEditing(null)
        }}
      />
    )
  }

  return (
    <div className="section">
      <div className="section-head">
        <div>
          <h3 className="section-title">模型</h3>
          <p className="section-desc">可以接入任何兼容 OpenAI 接口的服务（DeepSeek、通义千问、Kimi、智谱、豆包、OpenRouter、Ollama 等），或者 Anthropic 协议的 Claude。API Key 只保存在本机，不会进入沙箱。</p>
        </div>
        <Button variant="primary" icon={<Plus />} onClick={() => setEditing({})}>添加模型</Button>
      </div>
      {settings.profiles.length === 0 ? (
        <div className="profile-empty">还没有模型。点「添加模型」，选一个服务商、填上 API Key 就能开始。</div>
      ) : (
        <div className="profile-list">
          {settings.profiles.map((p) => {
            const isDefault = p.id === settings.default_profile
            return (
              <div key={p.id} className="profile-card">
                <div className="profile-icon"><Server /></div>
                <div className="profile-info">
                  <div className="profile-name">
                    <span className="truncate">{p.name}</span>
                    {isDefault && <span className="badge badge-accent">默认</span>}
                  </div>
                  <div className="profile-model mono truncate">{p.model || '未填模型名'}</div>
                  <div className="profile-tags">
                    <span className="badge">{p.provider === 'anthropic' ? 'Anthropic 协议' : 'OpenAI 兼容'}</span>
                    {p.thinking && <span className="badge"><Zap />思考</span>}
                    {p.vision && <span className="badge"><Eye />识图</span>}
                    {!p.api_key_set && !/localhost|127\.0\.0\.1|host\.docker\.internal/.test(p.base_url) && (
                      <span className="badge badge-warn">未填 Key</span>
                    )}
                  </div>
                </div>
                <div className="profile-actions">
                  {!isDefault && (
                    <IconButton label="设为默认" size="sm" onClick={async () => {
                      await api.setDefaultProfile(p.id).catch(toast.error)
                      reloadSettings()
                    }}><Star /></IconButton>
                  )}
                  <IconButton label="编辑" size="sm" onClick={() => setEditing(p)}><Pencil /></IconButton>
                  <IconButton label="删除" size="sm" variant="danger-ghost" onClick={() => setDeleting(p)}><Trash2 /></IconButton>
                </div>
              </div>
            )
          })}
        </div>
      )}
      {deleting && (
        <Confirm
          title="删除模型"
          danger
          confirmText="删除"
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            await api.deleteProfile(deleting.id).catch(toast.error)
            await reloadSettings()
          }}
        >
          确定删除「{deleting.name}」吗？用这个模型的项目会改用默认模型。
        </Confirm>
      )}
    </div>
  )
}

type Draft = Partial<Profile> & { api_key?: string }

function ProfileForm({ initial, presets, onBack, onSaved }: {
  initial: Partial<Profile>
  presets: Preset[]
  onBack: () => void
  onSaved: () => Promise<void>
}) {
  const isNew = !initial.id
  const [step, setStep] = useState<'preset' | 'form'>(isNew ? 'preset' : 'form')
  const [preset, setPreset] = useState<Preset | null>(null)
  const [draft, setDraft] = useState<Draft>({
    provider: 'openai', context_window: 128000, max_output_tokens: 0, effort: '', ...initial, api_key: '',
  })
  const [advanced, setAdvanced] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null)

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDraft((d) => ({ ...d, [key]: value }))
    setResult(null)
  }

  const choose = (p: Preset) => {
    setPreset(p)
    const model = p.models[0] || ''
    const price = p.prices[model]
    setDraft((d) => ({
      ...d,
      name: p.key === 'custom' ? '' : p.label,
      provider: p.provider,
      base_url: p.base_url,
      model,
      context_window: p.context_window,
      max_output_tokens: p.max_output_tokens,
      thinking: p.thinking,
      vision: p.vision,
      price_input: price?.[0] ?? 0,
      price_cached: price?.[1] ?? 0,
      price_output: price?.[2] ?? 0,
    }))
    setStep('form')
  }

  useEffect(() => {
    if (!preset || !draft.model) return
    const price = preset.prices[draft.model]
    if (price) setDraft((d) => ({ ...d, price_input: price[0], price_cached: price[1], price_output: price[2] }))
  }, [draft.model, preset])

  const payload = () => {
    const { api_key, ...rest } = draft
    // 编辑时 Key 留空表示保留原来的
    return { ...rest, api_key: api_key || (isNew ? '' : null) }
  }

  const save = async () => {
    setSaving(true)
    try {
      await api.saveProfile(payload())
      toast.success('模型已保存')
      await onSaved()
    } catch (e) {
      toast.error(e)
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    setTesting(true)
    setResult(null)
    try {
      const r = await api.testProfile({ ...draft, api_key: draft.api_key || undefined })
      setResult(r.ok ? { ok: true, text: `连接成功，用时 ${r.seconds} 秒。模型回复：${r.reply || '（空）'}` } : { ok: false, text: r.error || '连接失败' })
    } catch (e) {
      setResult({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally {
      setTesting(false)
    }
  }

  if (step === 'preset') {
    return (
      <div className="section">
        <div className="section-head">
          <div className="section-back">
            <IconButton label="返回" size="sm" onClick={onBack}><ArrowLeft /></IconButton>
            <h3 className="section-title">选择服务商</h3>
          </div>
        </div>
        <div className="preset-grid">
          {presets.map((p) => (
            <button key={p.key} type="button" className="preset" onClick={() => choose(p)}>
              <span className="preset-name">{p.label}</span>
              <span className="preset-url mono truncate">{p.base_url ? p.base_url.replace(/^https?:\/\//, '') : '自定义接口地址'}</span>
            </button>
          ))}
        </div>
      </div>
    )
  }

  const valid = !!draft.model?.trim() && (!!draft.base_url?.trim() || draft.provider === 'anthropic')
  const suggestions = preset?.models || presets.find((p) => p.base_url && p.base_url === draft.base_url)?.models || []

  return (
    <div className="section">
      <div className="section-head">
        <div className="section-back">
          <IconButton label="返回" size="sm" onClick={isNew ? () => setStep('preset') : onBack}><ArrowLeft /></IconButton>
          <h3 className="section-title">{isNew ? `添加模型${preset ? ` · ${preset.label}` : ''}` : `编辑「${initial.name}」`}</h3>
        </div>
      </div>
      {preset?.note && <div className="alert alert-info"><Info /><div className="alert-body">{preset.note}</div></div>}
      <div className="form">
        <div className="field-row">
          <Field label="名称">
            <input className="input" value={draft.name || ''} placeholder="比如：DeepSeek" onChange={(e) => set('name', e.target.value)} />
          </Field>
          <Field label="协议">
            <div className="segmented segmented-block">
              <button type="button" aria-pressed={draft.provider === 'openai'} onClick={() => set('provider', 'openai')}>OpenAI 兼容</button>
              <button type="button" aria-pressed={draft.provider === 'anthropic'} onClick={() => set('provider', 'anthropic')}>Anthropic</button>
            </div>
          </Field>
        </div>
        <Field label="接口地址" hint={draft.provider === 'openai' ? '一般以 /v1 结尾，填到 /chat/completions 之前' : '留空使用 Anthropic 官方地址'}>
          <input className="input mono" value={draft.base_url || ''} placeholder="https://api.example.com/v1" onChange={(e) => set('base_url', e.target.value)} />
        </Field>
        <Field
          label="API Key"
          extra={preset?.docs && <a href={preset.docs} target="_blank" rel="noreferrer">获取 Key</a>}
          hint={!isNew && initial.api_key_set ? `已保存 ${initial.api_key_hint}，留空表示不修改` : '只保存在本机的 data/settings.json 里'}
        >
          <input className="input mono" type="password" autoComplete="off" value={draft.api_key || ''} placeholder={!isNew && initial.api_key_set ? '••••••••' : 'sk-…'} onChange={(e) => set('api_key', e.target.value)} />
        </Field>
        <Field label="模型" hint="需要支持工具调用（function calling）的模型">
          <input className="input mono" list="model-suggestions" value={draft.model || ''} placeholder="模型名" onChange={(e) => set('model', e.target.value)} />
          <datalist id="model-suggestions">
            {suggestions.map((m) => <option key={m} value={m} />)}
          </datalist>
        </Field>
        <div className="switch-stack">
          <Switch checked={!!draft.vision} onChange={(v) => set('vision', v)} label="支持识图：用户贴的截图、预览截图会发给模型" />
          {draft.provider === 'anthropic' && (
            <Switch checked={!!draft.thinking} onChange={(v) => set('thinking', v)} label="开启思考：先推理再动手，效果更好但更慢" />
          )}
        </div>

        <button type="button" className={`disclosure${advanced ? ' is-open' : ''}`} onClick={() => setAdvanced(!advanced)}>
          <ChevronDown />高级设置
        </button>
        {advanced && (
          <div className="form-advanced">
            <div className="field-row">
              <Field label="上下文窗口（token）" hint="超过它的 75% 时自动压缩历史">
                <input className="input" type="number" min={4000} value={draft.context_window ?? 128000} onChange={(e) => set('context_window', Number(e.target.value))} />
              </Field>
              <Field label="单次最大输出（token）" hint="0 表示用服务默认值">
                <input className="input" type="number" min={0} value={draft.max_output_tokens ?? 0} onChange={(e) => set('max_output_tokens', Number(e.target.value))} />
              </Field>
            </div>
            <Field label="推理强度" hint="只对支持的模型生效，留空用默认">
              <select className="select" value={draft.effort || ''} onChange={(e) => set('effort', e.target.value)}>
                <option value="">默认</option>
                <option value="low">low</option>
                <option value="medium">medium</option>
                <option value="high">high</option>
              </select>
            </Field>
            <Field label="价格（美元 / 百万 token）" hint="填了就能看到每轮花费，也能在 Agent 设置里限制单轮花费；不填只显示用量">
              <div className="price-row">
                <label><span>输入</span><input className="input" type="number" min={0} step="0.01" value={draft.price_input ?? 0} onChange={(e) => set('price_input', Number(e.target.value))} /></label>
                <label><span>缓存命中</span><input className="input" type="number" min={0} step="0.01" value={draft.price_cached ?? 0} onChange={(e) => set('price_cached', Number(e.target.value))} /></label>
                <label><span>输出</span><input className="input" type="number" min={0} step="0.01" value={draft.price_output ?? 0} onChange={(e) => set('price_output', Number(e.target.value))} /></label>
              </div>
            </Field>
          </div>
        )}

        {result && (
          <div className={`alert ${result.ok ? 'alert-success' : 'alert-danger'}`}>
            {result.ok ? <Check /> : <Info />}
            <div className="alert-body">{result.text}</div>
          </div>
        )}
        <div className="form-actions">
          <Button loading={testing} disabled={!valid} onClick={test}>测试连接</Button>
          <span className="spacer" />
          <Button variant="ghost" onClick={onBack}>取消</Button>
          <Button variant="primary" loading={saving} disabled={!valid} onClick={save}>保存</Button>
        </div>
      </div>
    </div>
  )
}

// --- Agent ---

function AgentSection() {
  const { settings, reloadSettings } = useApp()
  const [draft, setDraft] = useState<AgentSettings | null>(settings?.agent || null)
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    if (settings) setDraft(settings.agent)
  }, [settings])
  if (!draft) return <Spinner />
  const set = <K extends keyof AgentSettings>(k: K, v: AgentSettings[K]) => setDraft({ ...draft, [k]: v })
  const dirty = JSON.stringify(draft) !== JSON.stringify(settings?.agent)
  return (
    <div className="section">
      <div className="section-head">
        <div>
          <h3 className="section-title">Agent</h3>
          <p className="section-desc">控制 agent 每一轮能做多少事，以及做完后是否自动检查和修复。</p>
        </div>
      </div>
      <div className="form">
        <div className="setting-row">
          <div>
            <div className="setting-title">每轮结束后自动检查预览</div>
            <div className="field-hint">用无头浏览器打开预览，发现报错、白屏、资源 404 就自动交给 agent 修复</div>
          </div>
          <Switch checked={draft.auto_check} onChange={(v) => set('auto_check', v)} />
        </div>
        <div className="field-row">
          <Field label="自动修复最多几次" hint="每轮结束后检查不通过时">
            <input className="input" type="number" min={0} max={10} value={draft.autofix_rounds} onChange={(e) => set('autofix_rounds', Number(e.target.value))} />
          </Field>
          <Field label="每轮最多调用模型次数" hint="防止陷入死循环">
            <input className="input" type="number" min={5} max={500} value={draft.max_steps} onChange={(e) => set('max_steps', Number(e.target.value))} />
          </Field>
        </div>
        <div className="field-row">
          <Field label="单轮花费上限（美元）" hint="0 表示不限，需要在模型里填价格">
            <input className="input" type="number" min={0} step="0.1" value={draft.turn_budget_usd} onChange={(e) => set('turn_budget_usd', Number(e.target.value))} />
          </Field>
          <Field label="命令默认超时（秒）" hint="安装依赖、构建等命令">
            <input className="input" type="number" min={10} max={600} value={draft.command_timeout} onChange={(e) => set('command_timeout', Number(e.target.value))} />
          </Field>
        </div>
        <div className="form-actions">
          <span className="spacer" />
          <Button
            variant="primary"
            disabled={!dirty}
            loading={saving}
            onClick={async () => {
              setSaving(true)
              try {
                await api.saveAgent(draft)
                await reloadSettings()
                toast.success('已保存')
              } catch (e) {
                toast.error(e)
              } finally {
                setSaving(false)
              }
            }}
          >
            保存
          </Button>
        </div>
      </div>
    </div>
  )
}

// --- 关于 ---

function AboutSection() {
  const { info } = useApp()
  if (!info) return <Spinner />
  const network: Record<string, string> = {
    allowlist: '只能访问 npm、PyPI 等包管理源',
    open: '可以访问外网',
    none: '完全断网',
    local: '本地进程，未隔离',
  }
  const rows: [string, ReactNode][] = [
    ['版本', info.version],
    ['沙箱', info.sandbox],
    ['沙箱状态', info.sandbox_status === 'ok' ? '正常' : info.sandbox_status === 'checking' ? '检查中' : info.sandbox_status],
    ['沙箱网络', network[info.network] || info.network],
    ['预览地址', <span className="mono" key="d">{`<项目>.${info.preview_domain}`}</span>],
    ['浏览器自检', info.browser_check ? '已启用（Playwright）' : '未安装 Playwright，只做 HTTP 检查'],
  ]
  return (
    <div className="section">
      <div className="section-head">
        <div>
          <h3 className="section-title">关于</h3>
          <p className="section-desc">codeless 是一个基座模型可配置的编程 agent：在隔离沙箱里写 Python / Node.js 程序，实时预览，出错自己修。</p>
        </div>
      </div>
      <dl className="about">
        {rows.map(([k, v]) => (
          <div key={k} className="about-row">
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

import { ArrowUp, Check, ChevronDown, Cpu, ImagePlus, Settings2, Square, X } from 'lucide-react'
import { type ClipboardEvent, type DragEvent, useEffect, useRef, useState } from 'react'
import type { ImageInput, Profile } from '../api'
import { useApp } from '../app-context'
import { type PendingImage, toImage } from '../lib/images'
import { toast } from '../lib/toast'
import { IconButton, Menu } from './ui'

interface ComposerProps {
  variant: 'hero' | 'dock'
  running?: boolean
  placeholder?: string
  profileId?: string
  onProfileChange?: (id: string) => void
  onSubmit: (text: string, images: ImageInput[]) => Promise<boolean | void>
  onStop?: () => void
  autoFocus?: boolean
  vision?: boolean
  initialText?: string
}

export function Composer({
  variant, running, placeholder, profileId, onProfileChange, onSubmit, onStop, autoFocus, vision, initialText,
}: ComposerProps) {
  const [text, setText] = useState(initialText || '')
  const [images, setImages] = useState<PendingImage[]>([])
  const [busy, setBusy] = useState(false)
  const [dragging, setDragging] = useState(false)
  const area = useRef<HTMLTextAreaElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  // 高度随内容增长
  useEffect(() => {
    const el = area.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, variant === 'hero' ? 320 : 240)}px`
  }, [text, variant])

  useEffect(() => {
    const el = area.current
    if (!autoFocus || !el) return
    el.focus()
    el.setSelectionRange(el.value.length, el.value.length)
  }, [autoFocus])

  // Esc 停止
  useEffect(() => {
    if (!running || !onStop) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !document.querySelector('.modal-overlay')) onStop()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [running, onStop])

  const addFiles = async (files: FileList | File[]) => {
    const list = Array.from(files).filter((f) => f.type.startsWith('image/'))
    if (!list.length) return
    try {
      const added = await Promise.all(list.map(toImage))
      setImages((prev) => [...prev, ...added].slice(0, 4))
    } catch (e) {
      toast.error(e)
    }
  }

  const submit = async () => {
    const value = text.trim()
    if ((!value && !images.length) || busy) return
    setBusy(true)
    try {
      const ok = await onSubmit(value, images.map(({ media_type, data }) => ({ media_type, data })))
      if (ok !== false) {
        setText('')
        setImages([])
      }
    } catch (e) {
      toast.error(e)
    } finally {
      setBusy(false)
      area.current?.focus()
    }
  }

  const onPaste = (e: ClipboardEvent) => {
    const files = Array.from(e.clipboardData.files)
    if (files.some((f) => f.type.startsWith('image/'))) {
      e.preventDefault()
      addFiles(files)
    }
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragging(false)
    addFiles(e.dataTransfer.files)
  }

  const empty = !text.trim() && !images.length
  const showStop = running && empty
  const hint = running ? '补充要求或纠正方向，会在 agent 下一步时插入…' : placeholder

  return (
    <div
      className={`composer composer-${variant}${dragging ? ' is-dragging' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      {images.length > 0 && (
        <div className="composer-images">
          {images.map((img, i) => (
            <div className="composer-image" key={i}>
              <img src={img.url} alt={img.name} />
              <button type="button" aria-label="移除图片" onClick={() => setImages(images.filter((_, j) => j !== i))}>
                <X />
              </button>
            </div>
          ))}
          {vision === false && <span className="composer-warn">当前模型没有开启识图，图片不会发给模型</span>}
        </div>
      )}
      <textarea
        ref={area}
        className="composer-input"
        rows={variant === 'hero' ? 3 : 1}
        value={text}
        placeholder={hint}
        onChange={(e) => setText(e.target.value)}
        onPaste={onPaste}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            submit()
          }
        }}
      />
      <div className="composer-bar">
        <div className="composer-tools">
          <IconButton label="添加图片（也可以直接粘贴截图）" size="sm" onClick={() => fileInput.current?.click()}>
            <ImagePlus />
          </IconButton>
          <input
            ref={fileInput}
            type="file"
            accept="image/png,image/jpeg,image/gif,image/webp"
            multiple
            hidden
            onChange={(e) => {
              if (e.target.files) addFiles(e.target.files)
              e.target.value = ''
            }}
          />
          {onProfileChange && <ModelPicker value={profileId} onChange={onProfileChange} />}
        </div>
        <div className="composer-send">
          {variant === 'hero' && <span className="composer-hint"><kbd>Enter</kbd> 发送 <kbd>Shift</kbd>+<kbd>Enter</kbd> 换行</span>}
          {showStop ? (
            <IconButton label="停止（Esc）" variant="secondary" onClick={onStop} className="composer-stop">
              <Square />
            </IconButton>
          ) : (
            <IconButton
              label={running ? '插话' : '发送'}
              variant="primary"
              disabled={empty || busy}
              onClick={submit}
            >
              <ArrowUp />
            </IconButton>
          )}
        </div>
      </div>
    </div>
  )
}

function ModelPicker({ value, onChange }: { value?: string; onChange: (id: string) => void }) {
  const { settings, openSettings } = useApp()
  const profiles = settings?.profiles || []
  const current: Profile | undefined =
    profiles.find((p) => p.id === value) || profiles.find((p) => p.id === settings?.default_profile) || profiles[0]
  return (
    <Menu
      placement="up"
      trigger={({ toggle }) => (
        <button type="button" className="model-picker" onClick={toggle}>
          <Cpu />
          <span className="truncate">{current ? current.name : '未配置模型'}</span>
          <ChevronDown />
        </button>
      )}
    >
      {(close) => (
        <>
          <div className="menu-label">选择模型</div>
          {profiles.map((p) => (
            <button key={p.id} type="button" className="menu-item" onClick={() => { onChange(p.id); close() }}>
              <span className="menu-item-text">
                <span className="truncate">{p.name}</span>
                <span className="menu-item-sub mono truncate">{p.model}</span>
              </span>
              {current?.id === p.id && <Check className="menu-check" />}
            </button>
          ))}
          {profiles.length > 0 && <div className="menu-sep" />}
          <button type="button" className="menu-item" onClick={() => { close(); openSettings('models') }}>
            <Settings2 /> 管理模型
          </button>
        </>
      )}
    </Menu>
  )
}

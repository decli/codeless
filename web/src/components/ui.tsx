// 基础组件：统一尺寸和样式，页面里不直接写按钮、输入框的样式
import { Loader2, X } from 'lucide-react'
import {
  type ButtonHTMLAttributes,
  type ReactNode,
  useEffect,
  useRef,
  useState,
} from 'react'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'danger-ghost'
type Size = 'sm' | 'md' | 'lg'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  icon?: ReactNode
  loading?: boolean
  block?: boolean
}

export function Button({
  variant = 'secondary', size = 'md', icon, loading, block, className = '', children, disabled, ...rest
}: ButtonProps) {
  const cls = ['btn', `btn-${variant}`, size !== 'md' && `btn-${size}`, block && 'btn-block', className]
    .filter(Boolean).join(' ')
  return (
    <button type="button" className={cls} disabled={disabled || loading} {...rest}>
      {loading ? <Loader2 className="spinner" /> : icon}
      {children}
    </button>
  )
}

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string
  variant?: Variant
  size?: Size
  active?: boolean
}

export function IconButton({ label, variant = 'ghost', size = 'md', active, className = '', children, ...rest }: IconButtonProps) {
  const cls = ['btn', 'btn-icon', `btn-${variant}`, size !== 'md' && `btn-${size}`, active && 'is-active', className]
    .filter(Boolean).join(' ')
  return (
    <button type="button" className={cls} title={label} aria-label={label} {...rest}>
      {children}
    </button>
  )
}

export function Spinner({ small }: { small?: boolean }) {
  return <Loader2 className={small ? 'spinner spinner-sm' : 'spinner'} aria-label="加载中" />
}

export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label?: ReactNode }) {
  return (
    <label className="switch">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="switch-track" />
      {label}
    </label>
  )
}

export function Field({ label, hint, extra, children }: { label: ReactNode; hint?: ReactNode; extra?: ReactNode; children: ReactNode }) {
  return (
    <div className="field">
      <div className="field-label">
        <span>{label}</span>
        {extra}
      </div>
      {children}
      {hint && <div className="field-hint">{hint}</div>}
    </div>
  )
}

export function Empty({ icon, title, children, actions }: { icon: ReactNode; title: ReactNode; children?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon}</div>
      <div className="empty-title">{title}</div>
      {children && <div className="empty-text">{children}</div>}
      {actions && <div className="empty-actions">{actions}</div>}
    </div>
  )
}

export function Modal({ title, onClose, children, footer, small }: {
  title: ReactNode
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  small?: boolean
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={small ? 'modal modal-sm' : 'modal'} role="dialog" aria-modal="true">
        <div className="modal-header">
          <div className="modal-title">{title}</div>
          <IconButton label="关闭" onClick={onClose}><X /></IconButton>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  )
}

/** 下拉菜单：点外面或按 Esc 关闭 */
export function Menu({ trigger, children, placement = 'down', align = 'left' }: {
  trigger: (props: { open: boolean; toggle: () => void }) => ReactNode
  children: (close: () => void) => ReactNode
  placement?: 'up' | 'down'
  align?: 'left' | 'right'
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])
  return (
    <div className="menu-anchor" ref={ref}>
      {trigger({ open, toggle: () => setOpen((v) => !v) })}
      {open && (
        <div className={`menu menu-${placement}${align === 'right' ? ' menu-right' : ''}`} role="menu">
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  )
}

export function Confirm({ title, children, confirmText = '确定', danger, onConfirm, onClose }: {
  title: string
  children: ReactNode
  confirmText?: string
  danger?: boolean
  onConfirm: () => Promise<void> | void
  onClose: () => void
}) {
  const [busy, setBusy] = useState(false)
  return (
    <Modal
      small
      title={title}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button
            variant={danger ? 'danger' : 'primary'}
            loading={busy}
            onClick={async () => {
              setBusy(true)
              try {
                await onConfirm()
                onClose()
              } finally {
                setBusy(false)
              }
            }}
          >
            {confirmText}
          </Button>
        </>
      }
    >
      <div className="confirm-text">{children}</div>
    </Modal>
  )
}

/** 每秒刷新一次的计时 */
export function useNow(active: boolean, interval = 1000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => setNow(Date.now()), interval)
    return () => clearInterval(timer)
  }, [active, interval])
  return now
}

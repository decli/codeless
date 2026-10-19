// 统一 diff 展示：按行着色，带新旧行号
import { useMemo } from 'react'

interface Line {
  kind: 'add' | 'del' | 'ctx' | 'hunk' | 'meta'
  text: string
  old?: number
  new?: number
}

function parse(diff: string): Line[] {
  const out: Line[] = []
  let oldNo = 0
  let newNo = 0
  for (const raw of diff.split('\n')) {
    if (raw.startsWith('@@')) {
      const m = raw.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)/)
      if (m) {
        oldNo = Number(m[1])
        newNo = Number(m[2])
      }
      out.push({ kind: 'hunk', text: raw })
    } else if (raw.startsWith('+++') || raw.startsWith('---') || raw.startsWith('diff ') || raw.startsWith('index ')
      || raw.startsWith('new file') || raw.startsWith('deleted file') || raw.startsWith('similarity') || raw.startsWith('rename ')) {
      continue
    } else if (raw.startsWith('+')) {
      out.push({ kind: 'add', text: raw.slice(1), new: newNo++ })
    } else if (raw.startsWith('-')) {
      out.push({ kind: 'del', text: raw.slice(1), old: oldNo++ })
    } else if (raw.startsWith('\\')) {
      out.push({ kind: 'meta', text: raw })
    } else if (raw || out.length) {
      out.push({ kind: 'ctx', text: raw.slice(1), old: oldNo++, new: newNo++ })
    }
  }
  while (out.length && out[out.length - 1].kind === 'ctx' && !out[out.length - 1].text) out.pop()
  return out
}

export function DiffView({ diff, maxLines }: { diff: string; maxLines?: number }) {
  const lines = useMemo(() => parse(diff || ''), [diff])
  if (!lines.length) return <div className="diff-empty">没有文本改动</div>
  const shown = maxLines ? lines.slice(0, maxLines) : lines
  return (
    <div className="diff">
      <table>
        <tbody>
          {shown.map((line, i) =>
            line.kind === 'hunk' ? (
              <tr key={i} className="diff-hunk">
                <td colSpan={3}>{line.text}</td>
              </tr>
            ) : (
              <tr key={i} className={`diff-${line.kind}`}>
                <td className="diff-no">{line.old ?? ''}</td>
                <td className="diff-no">{line.new ?? ''}</td>
                <td className="diff-code">
                  <span className="diff-sign">{line.kind === 'add' ? '+' : line.kind === 'del' ? '−' : ' '}</span>
                  {line.text}
                </td>
              </tr>
            ),
          )}
        </tbody>
      </table>
      {maxLines && lines.length > maxLines && <div className="diff-more">还有 {lines.length - maxLines} 行未显示</div>}
    </div>
  )
}

export function DiffStat({ additions, deletions }: { additions?: number; deletions?: number }) {
  return (
    <span className="diffstat">
      {!!additions && <span className="diffstat-add">+{additions}</span>}
      {!!deletions && <span className="diffstat-del">−{deletions}</span>}
    </span>
  )
}

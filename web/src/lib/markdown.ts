// Markdown 渲染：marked 解析，DOMPurify 清洗，代码块高亮
import DOMPurify from 'dompurify'
import { Marked } from 'marked'
import { highlight } from './highlight'

const marked = new Marked({
  gfm: true,
  breaks: true,
  renderer: {
    code({ text, lang }) {
      const language = (lang || '').split(/\s/)[0]
      const label = language ? `<span class="md-code-lang">${language}</span>` : ''
      return `<pre class="md-code">${label}<code class="hljs">${highlight(text, language)}</code></pre>`
    },
  },
})

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noreferrer noopener')
  }
})

export function renderMarkdown(text: string): string {
  const html = marked.parse(text, { async: false }) as string
  return DOMPurify.sanitize(html, { ADD_ATTR: ['target'] })
}

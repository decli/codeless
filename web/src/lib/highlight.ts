// 代码高亮：只注册常用语言，控制包体积
import hljs from 'highlight.js/lib/core'
import bash from 'highlight.js/lib/languages/bash'
import css from 'highlight.js/lib/languages/css'
import diff from 'highlight.js/lib/languages/diff'
import dockerfile from 'highlight.js/lib/languages/dockerfile'
import ini from 'highlight.js/lib/languages/ini'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import markdown from 'highlight.js/lib/languages/markdown'
import python from 'highlight.js/lib/languages/python'
import sql from 'highlight.js/lib/languages/sql'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import yaml from 'highlight.js/lib/languages/yaml'

const langs = { bash, css, diff, dockerfile, ini, javascript, json, markdown, python, sql, typescript, xml, yaml }
for (const [name, lang] of Object.entries(langs)) hljs.registerLanguage(name, lang)
hljs.registerAliases(['sh', 'shell', 'zsh', 'console'], { languageName: 'bash' })
hljs.registerAliases(['js', 'jsx', 'mjs', 'cjs'], { languageName: 'javascript' })
hljs.registerAliases(['ts', 'tsx'], { languageName: 'typescript' })
hljs.registerAliases(['html', 'htm', 'svg', 'vue'], { languageName: 'xml' })
hljs.registerAliases(['py'], { languageName: 'python' })
hljs.registerAliases(['yml'], { languageName: 'yaml' })
hljs.registerAliases(['toml', 'env', 'cfg'], { languageName: 'ini' })
hljs.registerAliases(['md'], { languageName: 'markdown' })

const EXT: Record<string, string> = {
  py: 'python', js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript',
  ts: 'typescript', tsx: 'typescript', html: 'xml', htm: 'xml', svg: 'xml', vue: 'xml', xml: 'xml',
  css: 'css', scss: 'css', json: 'json', md: 'markdown', yml: 'yaml', yaml: 'yaml', sh: 'bash',
  toml: 'ini', ini: 'ini', env: 'ini', cfg: 'ini', sql: 'sql', diff: 'diff', patch: 'diff',
}

export function languageOf(path: string): string | undefined {
  const name = path.split('/').pop() || ''
  if (name === 'Dockerfile') return 'dockerfile'
  if (name.startsWith('.env')) return 'ini'
  return EXT[name.split('.').pop()?.toLowerCase() || '']
}

function escape(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

export function highlight(code: string, lang?: string): string {
  if (code.length > 200_000) return escape(code)
  try {
    if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang, ignoreIllegals: true }).value
  } catch {
    /* 回退为纯文本 */
  }
  return escape(code)
}

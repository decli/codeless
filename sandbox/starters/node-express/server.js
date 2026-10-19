import express from 'express'

const app = express()
const port = Number(process.env.PORT) || 3000

app.use(express.json())
app.use(express.static('public'))

app.get('/api/health', (_req, res) => {
  res.json({ ok: true })
})

// 预览经 codeless 反向代理访问，必须监听 0.0.0.0
app.listen(port, '0.0.0.0', () => {
  console.log(`listening on http://0.0.0.0:${port}`)
})

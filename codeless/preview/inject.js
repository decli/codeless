// codeless 预览注入脚本：把页面里的运行时错误报给编排服务，agent 能看到并自己修。
(function () {
  if (window.__codelessPreview) return
  window.__codelessPreview = true

  var sent = 0
  var seen = {}
  var IGNORE = /ResizeObserver loop|Script error\.?$|chrome-extension:|moz-extension:/

  function text(value) {
    if (value == null) return ''
    if (value instanceof Error) return value.stack || value.message
    if (typeof value === 'object') {
      try { return JSON.stringify(value) } catch (e) { return String(value) }
    }
    return String(value)
  }

  function report(kind, message, stack) {
    message = text(message).slice(0, 2000)
    if (!message || IGNORE.test(message)) return
    var key = kind + message.slice(0, 200)
    if (seen[key] || sent > 40) return
    seen[key] = true
    sent += 1
    var body = JSON.stringify({
      kind: kind,
      message: message,
      stack: text(stack).slice(0, 4000),
      url: location.pathname + location.search,
    })
    try {
      navigator.sendBeacon('/__codeless__/errors', new Blob([body], { type: 'application/json' }))
    } catch (e) {}
  }

  window.addEventListener('error', function (event) {
    var target = event.target
    if (target && target !== window && (target.src || target.href)) {
      report('resource', '资源加载失败：' + (target.src || target.href))
      return
    }
    report('error', event.message, event.error && event.error.stack)
  }, true)

  window.addEventListener('unhandledrejection', function (event) {
    var reason = event.reason
    report('rejection', (reason && reason.message) || reason, reason && reason.stack)
  })

  var originalError = console.error
  console.error = function () {
    try {
      report('console', Array.prototype.map.call(arguments, text).join(' '))
    } catch (e) {}
    return originalError.apply(console, arguments)
  }

  // 把页面内的路由变化告诉外层，地址栏跟着变
  function notify() {
    try {
      parent.postMessage({ source: 'codeless-preview', kind: 'nav', path: location.pathname + location.search + location.hash }, '*')
    } catch (e) {}
  }
  ;['pushState', 'replaceState'].forEach(function (name) {
    var original = history[name]
    history[name] = function () {
      var result = original.apply(this, arguments)
      notify()
      return result
    }
  })
  window.addEventListener('popstate', notify)
  window.addEventListener('hashchange', notify)
  notify()
})()

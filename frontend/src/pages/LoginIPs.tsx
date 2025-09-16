import { useState, useRef } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { ExternalLink, RefreshCw, AlertCircle } from "lucide-react"

export default function LoginIPsPage() {
  const [isLoading, setIsLoading] = useState(true)
  const [hasError, setHasError] = useState(false)
  const [key, setKey] = useState(0) // 用于强制刷新iframe
  const iframeRef = useRef<HTMLIFrameElement>(null) // 引用 iframe 元素

  // 使用本地代理路径，避免跨域和认证问题
  const ipMonitorUrl = "/ipmonitor"  // 通过 Vite 代理转发到内网 8000 端口

  // 将以 / 开头且未带 /ipmonitor 前缀的路径，统一加上前缀
  const addIpmonitorPrefix = (url: string): string => {
    try {
      if (!url) return url
      if (url.startsWith('/ipmonitor')) return url
      if (url.startsWith('/')) return `/ipmonitor${url}`
      return url
    } catch {
      return url
    }
  }

  // 在 iframe 内注入补丁：重写 a.href / form.action，并拦截 fetch 与 XHR
  const injectPrefixPatches = () => {
    const iframe = iframeRef.current
    if (!iframe) return
    const win = iframe.contentWindow as any
    const doc = iframe.contentDocument as Document | null
    if (!win || !doc) return

    // 若跨域或已注入过，安全处理
    try {
      // 已存在清理函数时先清理，避免重复注入
      if (typeof win.__ipmonitorCleanup === 'function') {
        win.__ipmonitorCleanup()
      }
    } catch {
      // 可能跨域，直接返回
      return
    }

    const rewriteAttributes = () => {
      // 链接类
      doc.querySelectorAll('a[href^="/"]').forEach((el) => {
        const a = el as HTMLAnchorElement
        const href = a.getAttribute('href') || ''
        const newHref = addIpmonitorPrefix(href)
        if (newHref !== href) a.setAttribute('href', newHref)
      })
      doc.querySelectorAll('form[action^="/"]').forEach((el) => {
        const f = el as HTMLFormElement
        const action = f.getAttribute('action') || ''
        const newAction = addIpmonitorPrefix(action)
        if (newAction !== action) f.setAttribute('action', newAction)
      })

      // 资源类（img/script/link/iframe/source/video/audio）
      const srcSelectors = [
        'img[src^="/"]',
        'script[src^="/"]',
        'iframe[src^="/"]',
        'source[src^="/"]',
        'video[src^="/"]',
        'audio[src^="/"]'
      ]
      doc.querySelectorAll(srcSelectors.join(',')).forEach((el) => {
        const src = (el as HTMLElement).getAttribute('src') || ''
        const newSrc = addIpmonitorPrefix(src)
        if (newSrc !== src) (el as HTMLElement).setAttribute('src', newSrc)
      })
      // link 标签的 href（CSS 等）
      doc.querySelectorAll('link[href^="/"]').forEach((el) => {
        const href = (el as HTMLElement).getAttribute('href') || ''
        const newHref = addIpmonitorPrefix(href)
        if (newHref !== href) (el as HTMLElement).setAttribute('href', newHref)
      })
    }

    // 点击/提交事件层面兜底，防止动态生成的链接未被改写
    const clickHandler = (e: any) => {
      try {
        const target = (e.target as Element)?.closest?.('a') as HTMLAnchorElement | null
        if (!target) return
        const href = target.getAttribute('href') || ''
        if (href.startsWith('/') && !href.startsWith('/ipmonitor')) {
          e.preventDefault()
          win.location.href = addIpmonitorPrefix(href)
        }
      } catch {}
    }

    const submitHandler = (e: any) => {
      try {
        const form = (e.target as Element)?.closest?.('form') as HTMLFormElement | null
        if (!form) return
        const action = form.getAttribute('action') || ''
        if (action.startsWith('/') && !action.startsWith('/ipmonitor')) {
          form.setAttribute('action', addIpmonitorPrefix(action))
        }
      } catch {}
    }

    // 拦截 fetch
    const originalFetch = win.fetch
    const patchedFetch = (input: any, init?: any) => {
      try {
        if (typeof input === 'string') {
          input = addIpmonitorPrefix(input)
        } else if (input && typeof input.url === 'string') {
          input = new win.Request(addIpmonitorPrefix(input.url), input)
        }
      } catch {}
      return originalFetch(input, init)
    }

    // 拦截 XHR
    const originalXHROpen = win.XMLHttpRequest?.prototype?.open
    if (originalXHROpen) {
      win.XMLHttpRequest.prototype.open = function(method: string, url: string, async?: boolean, user?: string, password?: string) {
        try {
          if (typeof url === 'string') {
            url = addIpmonitorPrefix(url)
          }
        } catch {}
        return originalXHROpen.call(this, method, url, async, user, password)
      }
    }

    // 监听 DOM 变化，处理动态添加/修改的链接与表单
    const observer = new win.MutationObserver((mutations: any[]) => {
      for (const m of mutations) {
        if (m.type === 'childList') {
          m.addedNodes?.forEach?.((node: any) => {
            if (node?.querySelectorAll) {
              node.querySelectorAll?.('a[href^="/"], form[action^="/"], img[src^="/"], script[src^="/"], iframe[src^="/"], source[src^="/"], video[src^="/"], audio[src^="/"], link[href^="/"]').forEach((el: Element) => {
                if (el.tagName === 'A') {
                  const a = el as HTMLAnchorElement
                  const href = a.getAttribute('href') || ''
                  const newHref = addIpmonitorPrefix(href)
                  if (newHref !== href) a.setAttribute('href', newHref)
                } else if (el.tagName === 'FORM') {
                  const f = el as HTMLFormElement
                  const action = f.getAttribute('action') || ''
                  const newAction = addIpmonitorPrefix(action)
                  if (newAction !== action) f.setAttribute('action', newAction)
                } else if ((el as HTMLElement).hasAttribute('src')) {
                  const src = (el as HTMLElement).getAttribute('src') || ''
                  const newSrc = addIpmonitorPrefix(src)
                  if (newSrc !== src) (el as HTMLElement).setAttribute('src', newSrc)
                } else if (el.tagName === 'LINK') {
                  const href = (el as HTMLElement).getAttribute('href') || ''
                  const newHref = addIpmonitorPrefix(href)
                  if (newHref !== href) (el as HTMLElement).setAttribute('href', newHref)
                }
              })
            }
          })
        } else if (m.type === 'attributes') {
          const el = m.target as Element
          if (el.tagName === 'A' && m.attributeName === 'href') {
            const href = el.getAttribute('href') || ''
            el.setAttribute('href', addIpmonitorPrefix(href))
          } else if (el.tagName === 'FORM' && m.attributeName === 'action') {
            const action = el.getAttribute('action') || ''
            el.setAttribute('action', addIpmonitorPrefix(action))
          } else if (m.attributeName === 'src' && (el as HTMLElement).hasAttribute('src')) {
            const src = (el as HTMLElement).getAttribute('src') || ''
            el.setAttribute('src', addIpmonitorPrefix(src))
          } else if (el.tagName === 'LINK' && m.attributeName === 'href') {
            const href = el.getAttribute('href') || ''
            el.setAttribute('href', addIpmonitorPrefix(href))
          }
        }
      }
    })

    // 初始化一次改写
    rewriteAttributes()

    // 事件监听（捕获阶段更稳妥）
    doc.addEventListener('click', clickHandler, true)
    doc.addEventListener('submit', submitHandler, true)

    // 启动观察
    observer.observe(doc.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ['href', 'action', 'src'] })

    // 应用 fetch 补丁
    win.__origFetch = originalFetch
    win.fetch = patchedFetch

    // 应用 XHR 补丁
    win.__origXHROpen = originalXHROpen

    // 清理函数，便于 iframe 重新加载或组件卸载时恢复
    const cleanup = () => {
      try {
        observer.disconnect()
        doc.removeEventListener('click', clickHandler, true)
        doc.removeEventListener('submit', submitHandler, true)
        if (win.__origFetch) win.fetch = win.__origFetch
        if (win.__origXHROpen) win.XMLHttpRequest.prototype.open = win.__origXHROpen
      } catch {}
    }
    win.__ipmonitorCleanup = cleanup
  }

  // iframe加载完成处理
  const handleIframeLoad = () => {
    setIsLoading(false)
    setHasError(false)
    // 完成加载后注入改写逻辑
    injectPrefixPatches()
  }

  // iframe加载错误处理
  const handleIframeError = () => {
    setIsLoading(false)
    setHasError(true)
  }

  // 刷新iframe
  const handleRefresh = () => {
    setIsLoading(true)
    setHasError(false)
    setKey(prev => prev + 1)
  }

  // 在新窗口打开
  const handleOpenExternal = () => {
    window.open(ipMonitorUrl, '_blank')
  }

  return (
    <div className="h-screen flex flex-col">
      {/* 顶部工具栏 */}
      <Card className="rounded-none border-b border-l-0 border-r-0 border-t-0">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <CardTitle className="text-xl font-bold">Login IP监测</CardTitle>
              <span className="text-xs text-muted-foreground">
                （页面还在调试中，若有功能问题请内网访问：
                <a
                  href="http://10.6.20.138:8000"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline"
                >
                  http://10.6.20.138:8000
                </a>
                ）
              </span>
            </div>
            <div className="flex items-center gap-2">
              {hasError && (
                <div className="flex items-center gap-1 text-destructive text-sm">
                  <AlertCircle className="h-4 w-4" />
                  <span>连接失败</span>
                </div>
              )}
              {isLoading && (
                <div className="flex items-center gap-1 text-muted-foreground text-sm">
                  <RefreshCw className="h-4 w-4 animate-spin" />
                  <span>加载中...</span>
                </div>
              )}
              <Button
                variant="outline"
                size="sm"
                onClick={handleRefresh}
                disabled={isLoading}
              >
                <RefreshCw className="h-4 w-4 mr-1" />
                刷新
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={handleOpenExternal}
              >
                <ExternalLink className="h-4 w-4 mr-1" />
                新窗口打开
              </Button>
            </div>
          </div>
        </CardHeader>
      </Card>

      {/* iframe容器 */}
      <div className="flex-1 relative bg-background">
        {hasError ? (
          <div className="flex items-center justify-center h-full">
            <Card className="w-96">
              <CardContent className="pt-6">
                <div className="text-center space-y-4">
                  <AlertCircle className="h-12 w-12 text-destructive mx-auto" />
                  <div>
                    <h3 className="text-lg font-semibold">无法连接到IP监测服务</h3>
                    <p className="text-sm text-muted-foreground mt-1">
                      请检查内网连接或联系系统管理员
                    </p>
                    <p className="text-xs text-muted-foreground mt-2">
                      目标地址: {ipMonitorUrl}
                    </p>
                  </div>
                  <div className="flex gap-2 justify-center">
                    <Button onClick={handleRefresh}>
                      <RefreshCw className="h-4 w-4 mr-1" />
                      重试连接
                    </Button>
                    <Button variant="outline" onClick={handleOpenExternal}>
                      <ExternalLink className="h-4 w-4 mr-1" />
                      直接访问
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>
        ) : (
          <iframe
            key={key}
            src={ipMonitorUrl}
            className="w-full h-full border-0"
            title="Login IP监测系统"
            ref={iframeRef}
            onLoad={handleIframeLoad}
            onError={handleIframeError}
            // 允许iframe中的内容使用相机、麦克风等（如果需要）
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
            // 沙箱设置（根据需要调整）
            sandbox="allow-same-origin allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-top-navigation allow-top-navigation-by-user-activation"
          />
        )}
      </div>
    </div>
  )
}



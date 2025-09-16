import { useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { ExternalLink, RefreshCw, AlertCircle } from "lucide-react"

export default function BasisPage() {
  const [isLoading, setIsLoading] = useState(true)
  const [hasError, setHasError] = useState(false)
  const [key, setKey] = useState(0) // 用于强制刷新iframe

  // 使用本地代理路径，避免跨域和认证问题
  const dashUrl = "/dash"  // 通过 Vite 代理转发到内网 8050 端口

  // iframe加载完成处理
  const handleIframeLoad = () => {
    setIsLoading(false)
    setHasError(false)
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
    window.open(dashUrl, '_blank')
  }

  return (
    <div className="h-screen flex flex-col">
      {/* 顶部工具栏 */}
      <Card className="rounded-none border-b border-l-0 border-r-0 border-t-0">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-xl font-bold">基差分析</CardTitle>
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
                    <h3 className="text-lg font-semibold">无法连接到基差分析服务</h3>
                    <p className="text-sm text-muted-foreground mt-1">
                      请检查内网连接或联系系统管理员
                    </p>
                    <p className="text-xs text-muted-foreground mt-2">
                      目标地址: {dashUrl}
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
            src={dashUrl}
            className="w-full h-full border-0"
            title="基差分析 - Dash应用"
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



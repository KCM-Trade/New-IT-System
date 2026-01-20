import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"

// 内网服务地址
const TARGET_URL = "http://10.6.20.138:8000"

export default function LoginIPsPage() {
  const handleRedirect = () => {
    window.open(TARGET_URL, "_blank", "noopener,noreferrer")
  }

  return (
    <div className="relative min-h-[calc(100vh-6rem)] w-full overflow-hidden rounded-xl bg-background">
      {/* 内容卡片 */}
      <div className="relative z-10 flex min-h-[calc(100vh-6rem)] items-center justify-center p-6">
        <Card className="w-full max-w-xl text-center shadow-lg border-none bg-background/80">
          <CardHeader>
            <CardTitle className="text-2xl font-semibold">Login IP监测</CardTitle>
            <CardDescription>
              访问 Login IP 监测分析系统
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-6">
            <div className="text-sm text-muted-foreground space-y-2">
              <p>该服务运行在公司内网环境。</p>
              <p>
                目标地址: <span className="font-mono text-xs">{TARGET_URL}</span>
              </p>
              <p className="text-xs opacity-80">
                (迁移服务器更换IP后, 会重新部署到此页面, ETA: 2026-03)
              </p>
            </div>
            
            <Button size="lg" className="mx-auto px-10" onClick={handleRedirect}>
              前往访问
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

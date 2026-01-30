import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { useI18n } from "@/components/i18n-provider"

// 该地址位于公司内网，只能在企业网络或VPN环境访问
const IBID_TARGET_URL = "http://10.6.20.138:8088/"

export default function IbidLotsPage() {
  const { t } = useI18n()

  const handleRedirect = () => {
    window.open(IBID_TARGET_URL, "_blank", "noopener,noreferrer")
  }

  return (
    <div className="relative min-h-[calc(100vh-6rem)] w-full overflow-hidden rounded-xl bg-background">
      {/* Content card */}
      <div className="relative z-10 flex min-h-[calc(100vh-6rem)] items-center justify-center p-6">
        <Card className="w-full max-w-xl text-center shadow-lg border-none bg-background/80">
          <CardHeader>
            <CardTitle className="text-2xl font-semibold">{t("pages.ibidLots")}</CardTitle>
            <CardDescription>{t("ibidLotsPage.description")}</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-6">
            <p className="text-sm text-muted-foreground">{t("ibidLotsPage.note")}</p>
            <Button size="lg" className="mx-auto px-10" onClick={handleRedirect}>
              {t("ibidLotsPage.button")}
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}


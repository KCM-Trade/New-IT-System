import { useI18n } from "@/components/i18n-provider"

// Home page - placeholder for future dashboard content
export default function HomePage() {
  const { t } = useI18n()

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-6 p-6">
      <div className="text-center space-y-3">
        <h1 className="text-3xl font-bold tracking-tight">
          {t("header.title")}
        </h1>
        <p className="text-muted-foreground text-lg">
          {t("pages.homeWelcome")}
        </p>
      </div>
    </div>
  )
}

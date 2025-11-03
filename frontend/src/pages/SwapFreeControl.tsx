import BlurText from "@/components/blur-text"
import { useI18n } from "@/components/i18n-provider"

// Swap Free Control page component
export default function SwapFreeControlPage() {
  const { t } = useI18n()
  
  return (
    <div className="flex min-h-svh items-center justify-center">
      <BlurText
        text={t("pages.swapFreeControl") + " 开发中"}
        delay={150}
        animateBy="words"
        direction="top"
        className="text-3xl font-semibold"
        repeatEveryMs={5000}
      />
    </div>
  )
}

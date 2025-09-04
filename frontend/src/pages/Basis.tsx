import BlurText from "@/components/blur-text"
import Iridescence from "@/components/Iridescence"

export default function BasisPage() {
  return (
    <div className="relative min-h-svh">
      <div className="absolute inset-0 z-0 pointer-events-none">
        <Iridescence color={[1, 1, 1]} mouseReact={false} amplitude={0.1} speed={0.1} />
      </div>
      <div className="relative z-10 flex min-h-svh items-center justify-center">
        <div className="text-center space-y-2">
          <BlurText
            text="基差分析 开发ing"
            delay={150}
            animateBy="words"
            direction="top"
            className="text-3xl font-semibold"
            repeatEveryMs={5000}
          />
          {/* Static line (no animation) */}
          <p className="text-3xl font-semibold">请先通过 analysis.kohleservices.com 访问</p>
        </div>
      </div>
    </div>
  )
}



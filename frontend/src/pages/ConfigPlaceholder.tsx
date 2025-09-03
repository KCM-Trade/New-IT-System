import BlurText from "@/components/blur-text"
import DarkVeil from "@/components/DarkVeil"

export default function ConfigPlaceholder() {
  return (
    <div className="relative min-h-svh">
      {/* background layer: DarkVeil fills the page */}
      <div className="absolute inset-0 z-0">
        <DarkVeil
          speed={0.5}
          hueShift={0}
          noiseIntensity={0}
          scanlineFrequency={0.5}
          scanlineIntensity={0.5}
          warpAmount={2.9}
        />
      </div>
      {/* content layer: always above background */}
      <div className="relative z-10 flex min-h-svh items-center justify-center">
        <BlurText
          text="Configuration Page 开发ing"
          delay={150}
          animateBy="words"
          direction="top"
          className="text-3xl font-semibold text-white"
          repeatEveryMs={5000}
        />
      </div>
    </div>
  )
}



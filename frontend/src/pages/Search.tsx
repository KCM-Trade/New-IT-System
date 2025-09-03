import BlurText from "@/components/blur-text"

export default function SearchPage() {
  return (
    <div className="flex min-h-svh items-center justify-center">
      <BlurText
        text="Search 开发ing"
        delay={150}
        animateBy="words"
        direction="top"
        className="text-3xl font-semibold"
        repeatEveryMs={5000}
      />
    </div>
  )
}



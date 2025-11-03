import { Languages } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useI18n } from "@/components/i18n-provider"
import type { Language } from "@/i18n"

// Language options with display names
const languageOptions: { code: Language; label: string; native: string }[] = [
  { code: "zh-CN", label: "Chinese", native: "简体中文" },
  { code: "en-US", label: "English", native: "English" },
]

// Language toggle button component
export function LanguageToggle() {
  const { language, setLanguage } = useI18n()

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="icon" aria-label="Toggle language">
          <Languages className="h-[1.2rem] w-[1.2rem]" />
          <span className="sr-only">Toggle language</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {languageOptions.map((option) => (
          <DropdownMenuItem
            key={option.code}
            onClick={() => setLanguage(option.code)}
            className={language === option.code ? "bg-accent" : ""}
          >
            <span className="flex items-center gap-2">
              <span className="font-medium">{option.native}</span>
              <span className="text-xs text-muted-foreground">({option.label})</span>
            </span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}


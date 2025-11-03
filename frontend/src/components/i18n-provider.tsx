import { createContext, useContext, useEffect, useState, type ReactNode } from "react"
import { type Language, defaultLanguage, type TFunctionWithParams, createT } from "@/i18n"

// Props for the I18nProvider component
type I18nProviderProps = {
  children: ReactNode
  defaultLang?: Language
  storageKey?: string
}

// Context state shape so that consumers can read and set the language
type I18nProviderState = {
  language: Language
  setLanguage: (lang: Language) => void
  t: TFunctionWithParams
}

// Default (safe) initial state used before the provider mounts
const initialState: I18nProviderState = {
  language: defaultLanguage,
  setLanguage: () => null,
  t: () => "",
}

const I18nProviderContext = createContext<I18nProviderState>(initialState)

export function I18nProvider({
  children,
  defaultLang = defaultLanguage,
  storageKey = "vite-ui-language",
  ...props
}: I18nProviderProps) {
  // Initialize from localStorage or fallback to default
  const [language, setLanguage] = useState<Language>(() => {
    const stored = localStorage.getItem(storageKey) as Language | null
    return stored && (stored === "zh-CN" || stored === "en-US") ? stored : defaultLang
  })

  // Create translation function for current language
  const t = createT(language)

  // Set HTML lang attribute for accessibility
  useEffect(() => {
    document.documentElement.lang = language
  }, [language])

  // Persist choice to localStorage for future visits
  const value: I18nProviderState = {
    language,
    setLanguage: (nextLang: Language) => {
      localStorage.setItem(storageKey, nextLang)
      setLanguage(nextLang)
    },
    t,
  }

  return (
    <I18nProviderContext.Provider {...props} value={value}>
      {children}
    </I18nProviderContext.Provider>
  )
}

// Hook to consume the I18nProvider context safely
export const useI18n = () => {
  const context = useContext(I18nProviderContext)
  if (context === undefined) {
    throw new Error("useI18n must be used within an I18nProvider")
  }
  return context
}


/**
 * The page's translator as a hook. The app-wide `t` from useI18n() is a new
 * function on every render; this one only changes with the language, so it can
 * sit in dependency arrays (the grid's columnDefs depend on it).
 */
import { useMemo } from "react";

import { useI18n } from "@/components/i18n-provider";

import { makeTranslate, type Translate } from "./labels";

export function useLedgerT(): Translate {
  const { language } = useI18n();
  return useMemo(() => makeTranslate(language), [language]);
}

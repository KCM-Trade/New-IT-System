/**
 * Translation keys for every enum value of the ledger contract, plus the
 * translator the page uses. The backend sends stable lowercase strings; the
 * UI owns the wording (i18n/locales, namespace `fundRiskLedgerPage`).
 */
import { createT, type Language } from "@/i18n";

import type {
  Authority,
  ClientSource,
  Direction,
  FrozenScope,
  OrderWarning,
  RowSource,
  TransferSucceeded,
} from "./types";

export type TranslateParams = Record<string, string | number>;
/** `t(key, params?)` — same call shape as the app-wide translator. */
export type Translate = (key: string, params?: TranslateParams) => string;

/**
 * Translator for one language. Placeholders are filled in a single pass with
 * a function replacer, so free text interpolated into a message (server error
 * details, staff notes) is inserted verbatim: `$&` or `{other}` inside the
 * value is not interpreted.
 */
export function makeTranslate(language: Language): Translate {
  const base = createT(language);
  return (key, params) => {
    const text = base(key);
    if (!params) return text;
    return text.replace(/\{(\w+)\}/g, (whole, name: string) =>
      name in params ? String(params[name]) : whole,
    );
  };
}

export const DIRECTION_LABEL_KEYS: Record<Direction, string> = {
  deposit: "fundRiskLedgerPage.enums.direction.deposit",
  withdrawal: "fundRiskLedgerPage.enums.direction.withdrawal",
};

export const FROZEN_SCOPE_LABEL_KEYS: Record<FrozenScope, string> = {
  none: "fundRiskLedgerPage.enums.frozenScope.none",
  one_card: "fundRiskLedgerPage.enums.frozenScope.one_card",
  all_cards: "fundRiskLedgerPage.enums.frozenScope.all_cards",
  non_counter: "fundRiskLedgerPage.enums.frozenScope.non_counter",
  unknown: "fundRiskLedgerPage.enums.frozenScope.unknown",
};

export const AUTHORITY_LABEL_KEYS: Record<Authority, string> = {
  bank: "fundRiskLedgerPage.enums.authority.bank",
  police_antifraud: "fundRiskLedgerPage.enums.authority.police_antifraud",
  alipay: "fundRiskLedgerPage.enums.authority.alipay",
  unknown: "fundRiskLedgerPage.enums.authority.unknown",
};

export const CLIENT_SOURCE_LABEL_KEYS: Record<ClientSource, string> = {
  online: "fundRiskLedgerPage.enums.clientSource.online",
  offline: "fundRiskLedgerPage.enums.clientSource.offline",
};

export const TRANSFER_SUCCEEDED_LABEL_KEYS: Record<TransferSucceeded, string> = {
  yes: "fundRiskLedgerPage.enums.transferSucceeded.yes",
  no: "fundRiskLedgerPage.enums.transferSucceeded.no",
  unknown: "fundRiskLedgerPage.enums.transferSucceeded.unknown",
};

export const ROW_SOURCE_LABEL_KEYS: Record<RowSource, string> = {
  manual: "fundRiskLedgerPage.enums.rowSource.manual",
  import: "fundRiskLedgerPage.enums.rowSource.import",
};

/** Readable notes for the non-blocking warnings of an order preview. */
export const ORDER_WARNING_LABEL_KEYS: Record<OrderWarning, string> = {
  psp_unmapped: "fundRiskLedgerPage.enums.orderWarning.psp_unmapped",
  non_cn_psp: "fundRiskLedgerPage.enums.orderWarning.non_cn_psp",
  test_account: "fundRiskLedgerPage.enums.orderWarning.test_account",
  not_approved: "fundRiskLedgerPage.enums.orderWarning.not_approved",
  amount_not_cny: "fundRiskLedgerPage.enums.orderWarning.amount_not_cny",
};

/**
 * Label of an enum value in the current language. Never renders an empty cell:
 * a missing value is a dash and an unexpected one is shown as received.
 */
export function labelOf<K extends string>(
  t: Translate,
  keys: Record<K, string>,
  value: string | null | undefined,
): string {
  if (value === null || value === undefined || value === "") return "—";
  const key = (keys as Record<string, string | undefined>)[value];
  return key ? t(key) : value;
}

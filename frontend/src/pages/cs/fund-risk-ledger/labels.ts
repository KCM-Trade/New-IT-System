/**
 * Chinese labels for every enum value of the ledger contract. The backend
 * sends stable lowercase strings; the UI owns the wording.
 */
import type {
  Authority,
  ClientSource,
  Direction,
  FrozenScope,
  OrderWarning,
  RowSource,
  TransferSucceeded,
} from "./types";

export const DIRECTION_LABELS: Record<Direction, string> = {
  deposit: "入金",
  withdrawal: "出金",
};

export const FROZEN_SCOPE_LABELS: Record<FrozenScope, string> = {
  none: "未封",
  one_card: "其中一张",
  all_cards: "名下所有",
  non_counter: "非柜",
  unknown: "尚不知",
};

export const AUTHORITY_LABELS: Record<Authority, string> = {
  bank: "银行",
  police_antifraud: "反诈公安",
  alipay: "支付宝",
  unknown: "尚不知",
};

export const CLIENT_SOURCE_LABELS: Record<ClientSource, string> = {
  online: "线上",
  offline: "线下",
};

export const TRANSFER_SUCCEEDED_LABELS: Record<TransferSucceeded, string> = {
  yes: "是",
  no: "否",
  unknown: "尚不知",
};

export const ROW_SOURCE_LABELS: Record<RowSource, string> = {
  manual: "手工登记",
  import: "历史导入",
};

/** Readable notes for the non-blocking warnings of an order preview. */
export const ORDER_WARNING_LABELS: Record<OrderWarning, string> = {
  psp_unmapped: "该支付通道尚未归入任何渠道，渠道按「其他」登记",
  non_cn_psp: "该支付通道不属于中国区",
  test_account: "这是测试 / 员工账户",
  not_approved: "订单状态不是 approved（未完成或被拒绝）",
  amount_not_cny: "订单没有人民币面额，金额留空",
};

/** Label lookup that never renders an empty cell for an unexpected value. */
export function labelOf<K extends string>(
  map: Record<K, string>,
  value: string | null | undefined,
): string {
  if (value === null || value === undefined || value === "") return "—";
  return (map as Record<string, string>)[value] ?? value;
}

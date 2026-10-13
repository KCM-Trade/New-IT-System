import { describe, expect, it } from "vitest";

import {
  buildCreatePayload,
  buildFields,
  buildUpdatePayload,
  carryOverForm,
  defaultTransferSucceeded,
  effectiveVenue,
  emptyForm,
  fmtFreeze,
  fmtRegion,
  fromYmd,
  incidentToForm,
  isFormDirty,
  parseConflict,
  parseFreezeDays,
  parseTransactionId,
  listQuery,
  mergePagesById,
  toYmd,
} from "./helpers";
import {
  AUTHORITY_LABELS,
  CLIENT_SOURCE_LABELS,
  DIRECTION_LABELS,
  FROZEN_SCOPE_LABELS,
  labelOf,
  ORDER_WARNING_LABELS,
  ROW_SOURCE_LABELS,
  TRANSFER_SUCCEEDED_LABELS,
} from "./labels";
import { PROVINCES } from "./regions";
import {
  AUTHORITIES,
  CLIENT_SOURCES,
  DIRECTIONS,
  FROZEN_SCOPES,
  ORDER_WARNINGS,
  ROW_SOURCES,
  TRANSFER_SUCCEEDED,
  type IncidentFields,
} from "./types";

const TODAY = new Date(2026, 9, 13); // 2026-10-13 local

describe("dates", () => {
  it("formats and parses local calendar dates", () => {
    expect(toYmd(TODAY)).toBe("2026-10-13");
    expect(toYmd(fromYmd("2026-01-05")!)).toBe("2026-01-05");
    expect(fromYmd("2026/01/05")).toBeUndefined();
    expect(fromYmd(null)).toBeUndefined();
  });
});

describe("list requests", () => {
  it("asks for one page, newest record first, with no filters", () => {
    expect(listQuery(1, 500)).toBe("sort=id&order=desc&page=1&page_size=500");
    expect(listQuery(3, 5)).toBe("sort=id&order=desc&page=3&page_size=5");
  });

  it("merges pages and drops a row that arrives twice", () => {
    const a = { id: 9, v: "first" };
    const merged = mergePagesById([
      [a, { id: 8, v: "x" }],
      [{ id: 8, v: "shifted copy" }, { id: 7, v: "y" }],
      [],
    ]);
    expect(merged.map((r) => r.id)).toEqual([9, 8, 7]);
    expect(merged[1].v).toBe("x");
    expect(mergePagesById([])).toEqual([]);
  });
});

describe("transaction id input", () => {
  it("accepts positive integers only", () => {
    expect(parseTransactionId(" 985343 ")).toBe(985343);
    expect(parseTransactionId("0")).toBeNull();
    expect(parseTransactionId("85343-1")).toBeNull();
    expect(parseTransactionId("")).toBeNull();
    expect(parseTransactionId("1.5")).toBeNull();
  });
});

describe("default transfer_succeeded", () => {
  it("is yes for an approved order and no otherwise", () => {
    expect(defaultTransferSucceeded("approved")).toBe("yes");
    expect(defaultTransferSucceeded("Approved")).toBe("yes");
    expect(defaultTransferSucceeded("fresh")).toBe("no");
    expect(defaultTransferSucceeded("declined")).toBe("no");
    expect(defaultTransferSucceeded(null)).toBe("no");
  });

  it("seeds a new form from the order status", () => {
    expect(emptyForm(TODAY, "approved").transferSucceeded).toBe("yes");
    expect(emptyForm(TODAY, "fresh").transferSucceeded).toBe("no");
    // No order loaded yet -> the contract default.
    expect(emptyForm(TODAY).transferSucceeded).toBe("unknown");
    expect(emptyForm(TODAY).reportedDate).toBe("2026-10-13");
  });
});

describe("freeze days", () => {
  it("maps an empty box to null (not known yet)", () => {
    expect(parseFreezeDays("")).toEqual({ ok: true, value: null });
    expect(parseFreezeDays("  ")).toEqual({ ok: true, value: null });
  });

  it("accepts whole days in range, including zero", () => {
    expect(parseFreezeDays("0")).toEqual({ ok: true, value: 0 });
    expect(parseFreezeDays(" 180 ")).toEqual({ ok: true, value: 180 });
    expect(parseFreezeDays("36500")).toEqual({ ok: true, value: 36500 });
  });

  it("rejects anything else", () => {
    expect(parseFreezeDays("-1").ok).toBe(false);
    expect(parseFreezeDays("1.5").ok).toBe(false);
    expect(parseFreezeDays("半年").ok).toBe(false);
    expect(parseFreezeDays("36501").ok).toBe(false);
  });
});

/** The staff fields that have no control in the form. */
const nullHidden = {
  id_province: null,
  id_city: null,
  ip_region: null,
  pwa_domain: null,
  account_type: null,
  venue_override: null,
};

describe("form -> payload", () => {
  it("serialises an untouched form to the contract defaults", () => {
    expect(buildFields(emptyForm(TODAY))).toEqual({
      reported_date: "2026-10-13",
      transfer_succeeded: "unknown",
      frozen_scope: "unknown",
      freeze_days: null,
      freeze_note: null,
      authority: "unknown",
      client_source: null,
      client_bank_province: null,
      client_bank_city: null,
      trader_bank_province: null,
      trader_bank_city: null,
      id_province: null,
      id_city: null,
      ip_region: null,
      pwa_domain: null,
      account_type: null,
      venue_override: null,
      event_text: null,
      result_text: null,
    });
  });

  it("trims text and nulls empties", () => {
    const fields = buildFields({
      ...emptyForm(TODAY, "approved"),
      frozenScope: "one_card",
      freezeDaysText: "30",
      freezeNote: "  半年后复核 ",
      authority: "bank",
      clientSource: "offline",
      clientBankProvince: "广东",
      clientBankCity: " 深圳 ",
      traderBankCity: "   ",
      eventText: " 客户来电 ",
    });
    expect(fields.transfer_succeeded).toBe("yes");
    expect(fields.frozen_scope).toBe("one_card");
    expect(fields.freeze_days).toBe(30);
    expect(fields.freeze_note).toBe("半年后复核");
    expect(fields.authority).toBe("bank");
    expect(fields.client_source).toBe("offline");
    expect(fields.client_bank_province).toBe("广东");
    expect(fields.client_bank_city).toBe("深圳");
    expect(fields.trader_bank_city).toBeNull();
    expect(fields.event_text).toBe("客户来电");
    expect(fields.result_text).toBeNull();
  });

  it("throws a readable message on invalid days", () => {
    expect(() =>
      buildFields({ ...emptyForm(TODAY), freezeDaysText: "x" }),
    ).toThrow(/封卡天数/);
  });

  it("builds create and update bodies", () => {
    const form = emptyForm(TODAY, "approved");
    const create = buildCreatePayload(form, 985343, null);
    expect(create.transaction_id).toBe(985343);
    expect(create.incident_group_id).toBeNull();
    expect(buildCreatePayload(form, 985344, 12).incident_group_id).toBe(12);
    expect("version" in create).toBe(false);

    const update = buildUpdatePayload(form, 4, true);
    expect(update.version).toBe(4);
    expect(update.apply_to_group).toBe(true);
    expect("transaction_id" in update).toBe(false);
  });

  it("round-trips a stored record through the form", () => {
    const stored: IncidentFields = {
      reported_date: "2026-09-30",
      transfer_succeeded: "no",
      frozen_scope: "all_cards",
      freeze_days: 0,
      freeze_note: "当天解封",
      authority: "alipay",
      client_source: "online",
      client_bank_province: "内蒙古",
      client_bank_city: "包头",
      trader_bank_province: "香港",
      trader_bank_city: null,
      id_province: "四川",
      id_city: "成都",
      ip_region: "广东",
      pwa_domain: "example.com",
      account_type: "STD",
      venue_override: "SZ02",
      event_text: "事件",
      result_text: null,
    };
    const form = incidentToForm(stored);
    expect(form.freezeDaysText).toBe("0");
    expect(buildFields(form)).toEqual(stored);

    const unknownDays = incidentToForm({ ...stored, freeze_days: null });
    expect(unknownDays.freezeDaysText).toBe("");
    expect(buildFields(unknownDays).freeze_days).toBeNull();
  });

  it("sends the fields without a form control back untouched on edit", () => {
    // Imported legacy rows carry these; PUT replaces every staff field, so an
    // edit of the visible fields must not clear or normalise them.
    const stored: IncidentFields = {
      reported_date: "2025-03-02",
      transfer_succeeded: "unknown",
      frozen_scope: "unknown",
      freeze_days: null,
      freeze_note: null,
      authority: "unknown",
      client_source: null,
      client_bank_province: null,
      client_bank_city: null,
      trader_bank_province: null,
      trader_bank_city: null,
      id_province: "黑龙江省 ",
      id_city: " 哈尔滨",
      ip_region: "广东 深圳",
      pwa_domain: "App.Example.com/path ",
      account_type: "std ",
      venue_override: " gz01",
      event_text: null,
      result_text: null,
    };
    const hidden = {
      id_province: stored.id_province,
      id_city: stored.id_city,
      ip_region: stored.ip_region,
      pwa_domain: stored.pwa_domain,
      account_type: stored.account_type,
      venue_override: stored.venue_override,
    };
    // What staff can do in the form: change visible fields only.
    const edited = {
      ...incidentToForm(stored),
      frozenScope: "one_card" as const,
      freezeDaysText: "30",
      clientBankProvince: "广东",
      eventText: "补充事件",
    };
    const update = buildUpdatePayload(edited, 3, false);
    expect(update).toMatchObject(hidden);
    expect(update.frozen_scope).toBe("one_card");
    expect(update.freeze_days).toBe(30);

    // Null stays null (not ""), and an untouched form is not dirty.
    const nulls = incidentToForm({ ...stored, ...nullHidden });
    expect(buildUpdatePayload(nulls, 1, false)).toMatchObject(nullHidden);
    expect(isFormDirty(incidentToForm(stored), incidentToForm(stored))).toBe(false);
  });

  it("creates new records with the hidden fields null", () => {
    expect(buildCreatePayload(emptyForm(TODAY, "approved"), 985343, null)).toMatchObject(
      nullHidden,
    );
    // 保存并添加同事件订单: the next order must not inherit them either.
    const prev = incidentToForm({
      ...buildFields(emptyForm(TODAY)),
      id_province: "四川",
      venue_override: "SZ02",
    });
    expect(buildCreatePayload(carryOverForm(prev, TODAY), 985344, 12)).toMatchObject(
      nullHidden,
    );
  });
});

describe("same-incident carry over and dirty check", () => {
  it("keeps the incident-level fields and resets the order-level ones", () => {
    const prev = {
      ...emptyForm(TODAY, "approved"),
      reportedDate: "2026-10-01",
      authority: "police_antifraud" as const,
      eventText: "同一事件",
      resultText: "已核实",
      clientBankProvince: "广东",
      frozenScope: "one_card" as const,
      freezeDaysText: "7",
    };
    const next = carryOverForm(prev, TODAY, "fresh");
    expect(next.reportedDate).toBe("2026-10-01");
    expect(next.authority).toBe("police_antifraud");
    expect(next.eventText).toBe("同一事件");
    expect(next.resultText).toBe("已核实");
    expect(next.clientBankProvince).toBe("");
    expect(next.frozenScope).toBe("unknown");
    expect(next.freezeDaysText).toBe("");
    expect(next.transferSucceeded).toBe("no");
  });

  it("detects a changed field", () => {
    const base = emptyForm(TODAY);
    expect(isFormDirty({ ...base }, base)).toBe(false);
    expect(isFormDirty({ ...base, eventText: "x" }, base)).toBe(true);
    expect(isFormDirty({ ...base, freezeDaysText: "3" }, base)).toBe(true);
  });
});

describe("409 body", () => {
  it("reads the flat contract shape", () => {
    expect(
      parseConflict({ detail: "already recorded", existing_id: 7, current_version: null }),
    ).toEqual({ detail: "already recorded", existing_id: 7, current_version: null });
  });

  it("reads the same object nested under detail", () => {
    expect(
      parseConflict({ detail: { detail: "stale", existing_id: null, current_version: 5 } }),
    ).toEqual({ detail: "stale", existing_id: null, current_version: 5 });
  });

  it("survives an unexpected body", () => {
    expect(parseConflict(null)).toEqual({ detail: "", existing_id: null, current_version: null });
    expect(parseConflict({ detail: "x" }).existing_id).toBeNull();
  });
});

describe("display helpers", () => {
  it("prefers the venue correction over the CRM tag", () => {
    const order = { venue: "GZ01" } as never;
    expect(effectiveVenue({ venue_override: "SZ02", order })).toBe("SZ02");
    expect(effectiveVenue({ venue_override: null, order })).toBe("GZ01");
    expect(effectiveVenue({ venue_override: null, order: null })).toBeNull();
  });

  it("formats region and freeze cells", () => {
    expect(fmtRegion("广东", "深圳")).toBe("广东 深圳");
    expect(fmtRegion(null, "深圳")).toBe("深圳");
    expect(fmtRegion(null, null)).toBe("—");
    expect(fmtFreeze(30, null)).toBe("30 天");
    expect(fmtFreeze(0, "当天解封")).toBe("0 天（当天解封）");
    expect(fmtFreeze(null, "半年")).toBe("半年");
    expect(fmtFreeze(null, null)).toBe("尚不知");
  });
});

describe("enum label coverage", () => {
  const cases: [string, readonly string[], Record<string, string>][] = [
    ["Direction", DIRECTIONS, DIRECTION_LABELS],
    ["FrozenScope", FROZEN_SCOPES, FROZEN_SCOPE_LABELS],
    ["Authority", AUTHORITIES, AUTHORITY_LABELS],
    ["ClientSource", CLIENT_SOURCES, CLIENT_SOURCE_LABELS],
    ["TransferSucceeded", TRANSFER_SUCCEEDED, TRANSFER_SUCCEEDED_LABELS],
    ["RowSource", ROW_SOURCES, ROW_SOURCE_LABELS],
    ["OrderWarning", ORDER_WARNINGS, ORDER_WARNING_LABELS],
  ];

  it.each(cases)("%s: every contract value has a Chinese label", (_name, values, labels) => {
    expect(Object.keys(labels).sort()).toEqual([...values].sort());
    for (const v of values) {
      expect(labels[v]).toMatch(/[一-鿿]/);
    }
  });

  it("mirrors the contract's enum strings", () => {
    expect(DIRECTIONS).toEqual(["deposit", "withdrawal"]);
    expect(FROZEN_SCOPES).toEqual(["none", "one_card", "all_cards", "non_counter", "unknown"]);
    expect(AUTHORITIES).toEqual(["bank", "police_antifraud", "alipay", "unknown"]);
    expect(CLIENT_SOURCES).toEqual(["online", "offline"]);
    expect(TRANSFER_SUCCEEDED).toEqual(["yes", "no", "unknown"]);
    expect(ROW_SOURCES).toEqual(["manual", "import"]);
    expect(ORDER_WARNINGS).toEqual([
      "psp_unmapped",
      "non_cn_psp",
      "test_account",
      "not_approved",
      "amount_not_cny",
    ]);
  });

  it("falls back to the raw value for an unknown enum string", () => {
    expect(labelOf(AUTHORITY_LABELS, "bank")).toBe("银行");
    expect(labelOf(AUTHORITY_LABELS, "court")).toBe("court");
    expect(labelOf(AUTHORITY_LABELS, null)).toBe("—");
  });

  it("lists 34 distinct provinces", () => {
    expect(PROVINCES).toHaveLength(34);
    expect(new Set(PROVINCES).size).toBe(34);
    expect(PROVINCES[0]).toBe("北京");
    expect(PROVINCES[33]).toBe("澳门");
  });
});

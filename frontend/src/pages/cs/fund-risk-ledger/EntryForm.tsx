/**
 * 「登记」 tab of the fund risk-control ledger: register an incident inline.
 *
 * Idle:   a centred transaction-id input + one button, and the last few
 *         records underneath.
 * Active: the input moves to the top; the read-only order block (for staff to
 *         check) and the fields the CRM does not have render in the page.
 *         An id that is already recorded switches to edit mode on that record
 *         instead of failing.
 *
 * Order facts are never sent back: the create body carries only the
 * transaction id and the server takes its own snapshot.
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import {
  Calendar as CalendarIcon,
  Loader2,
  Plus,
  Search,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { apiFetch } from "@/lib/fetch";
import { formatHk } from "@/lib/hk-time";
import { cn } from "@/lib/utils";
import { readErrorDetail } from "@/pages/risk-alert-mail/helpers";

import {
  CONTROL_CLASS,
  SELECT_LIKE_TRIGGER_CLASS,
  TEXTAREA_CLASS,
  TRIGGER_ICON_CLASS,
  TXN_BAR_CLASS,
} from "./controlStyles";
import { ProvinceCombobox } from "./ProvinceCombobox";
import { RecentList } from "./RecentList";
import {
  API_BASE,
  buildCreatePayload,
  buildUpdatePayload,
  carryOverForm,
  defaultTransferSucceeded,
  emptyForm,
  fmtAmount,
  fromYmd,
  incidentToForm,
  isAbortError,
  isFormDirty,
  listQuery,
  parseConflict,
  parseTransactionId,
  RECENT_LIMIT,
  toYmd,
  type LedgerForm,
} from "./helpers";
import {
  AUTHORITY_LABELS,
  CLIENT_SOURCE_LABELS,
  DIRECTION_LABELS,
  FROZEN_SCOPE_LABELS,
  labelOf,
  ORDER_WARNING_LABELS,
  TRANSFER_SUCCEEDED_LABELS,
} from "./labels";
import {
  AUTHORITIES,
  CLIENT_SOURCES,
  FREEZE_NOTE_MAX,
  FROZEN_SCOPES,
  SHORT_MAX,
  TEXT_MAX,
  TRANSFER_SUCCEEDED,
  type Authority,
  type ClientSource,
  type FrozenScope,
  type Incident,
  type IncidentListResponse,
  type OrderPreviewResponse,
  type OrderSnapshot,
  type OrderWarning,
  type TransferSucceeded,
} from "./types";

/** A record handed over from the history tab. */
export type EntryIntent =
  | { kind: "edit"; row: Incident }
  /** New order joining the incident group of an existing row. */
  | { kind: "add-to-group"; row: Incident };

interface EntryFormProps {
  /** Set by the history tab's row actions; null when the tab opens on its own. */
  intent: EntryIntent | null;
  /** Called once the intent has been picked up, so a remount does not replay it. */
  onIntentConsumed: () => void;
  /** Reports unsaved edits so the page can guard a tab change. */
  onDirtyChange: (dirty: boolean) => void;
}

/** The incident group a new order will join, for the header chip. */
type GroupContext = { id: number; nextIndex: number };

const AMBER_NOTE =
  "rounded-md border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/40 dark:bg-amber-950/40 dark:text-amber-300";
const TXN_INPUT_ID = "frl-txn";
const TXN_ERROR_ID = "frl-txn-error";

/** Width shared by the transaction-id bar and the form, so they line up. */
const FORM_WIDTH = "max-w-2xl";
/** Radix Select cannot hold an empty-string item; stands for "not filled in". */
const NONE_VALUE = "__none__";

/**
 * One form row: label in a fixed 8rem column, control in the rest. Every
 * staff-entered field uses this and nothing else, so labels and controls line
 * up. The label is as tall as a control (h-9) to sit on the first text line.
 */
function Row({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <div className="grid grid-cols-1 gap-x-4 gap-y-1.5 sm:grid-cols-[8rem_minmax(0,1fr)]">
      <Label htmlFor={htmlFor} className="font-normal text-muted-foreground sm:h-9">
        {label}
      </Label>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

/** A dropdown over a fixed list of enum values, sized like every other control. */
function EnumSelect<T extends string>({
  id,
  value,
  options,
  labels,
  onChange,
}: {
  id: string;
  value: T;
  options: readonly T[];
  labels: Record<T, string>;
  onChange: (value: T) => void;
}) {
  return (
    <Select value={value} onValueChange={(v) => onChange(v as T)}>
      <SelectTrigger id={id} className={CONTROL_CLASS}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((v) => (
          <SelectItem key={v} value={v}>
            {labels[v]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function OrderFact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="break-words text-sm">{children}</dd>
    </div>
  );
}

async function fetchIncident(id: number, signal: AbortSignal): Promise<Incident> {
  const res = await apiFetch(`${API_BASE}/incidents/${id}`, { signal });
  if (!res.ok) throw new Error(await readErrorDetail(res));
  return (await res.json()) as Incident;
}

export function EntryForm({ intent, onIntentConsumed, onDirtyChange }: EntryFormProps) {
  // False = the centred idle view; true = the input sits on top and the page
  // below it belongs to one registration.
  const [active, setActive] = useState(false);

  // Transaction id input
  const [txnInput, setTxnInput] = useState("");
  const [lookupBusy, setLookupBusy] = useState(false);
  const [lookupError, setLookupError] = useState<string | null>(null);
  const txnInputRef = useRef<HTMLInputElement | null>(null);
  const [focusTick, setFocusTick] = useState(0);

  // Form context: either a fresh order (create) or a stored record (edit).
  const [order, setOrder] = useState<OrderSnapshot | null>(null);
  const [warnings, setWarnings] = useState<OrderWarning[]>([]);
  const [editing, setEditing] = useState<Incident | null>(null);
  const [alreadyRecorded, setAlreadyRecorded] = useState(false);
  const [group, setGroup] = useState<GroupContext | null>(null);
  const [recordLoading, setRecordLoading] = useState(false);

  const [form, setForm] = useState<LedgerForm>(() => emptyForm(new Date()));
  const [baseline, setBaseline] = useState<LedgerForm>(form);
  const formRef = useRef(form);
  const baselineRef = useRef(baseline);
  useEffect(() => {
    formRef.current = form;
    baselineRef.current = baseline;
  }, [form, baseline]);
  const [applyToGroup, setApplyToGroup] = useState(false);

  const [saving, setSaving] = useState(false);
  const [staleConflict, setStaleConflict] = useState(false);
  const [duplicateId, setDuplicateId] = useState<number | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [dateOpen, setDateOpen] = useState(false);

  // 最近登记
  const [recent, setRecent] = useState<Incident[]>([]);
  const [recentTick, setRecentTick] = useState(0);

  // One controller for the read requests of the form (order lookup, record
  // load / reload). Writes are never aborted.
  const readCtrl = useRef<AbortController | null>(null);
  const newReadSignal = () => {
    readCtrl.current?.abort();
    const controller = new AbortController();
    readCtrl.current = controller;
    return controller.signal;
  };
  useEffect(() => () => readCtrl.current?.abort(), []);

  const resetForm = (next: LedgerForm) => {
    setForm(next);
    setBaseline(next);
  };

  /** Clear everything that belongs to one registration (not `active`). */
  const clearContext = () => {
    setTxnInput("");
    setLookupBusy(false);
    setLookupError(null);
    setOrder(null);
    setWarnings([]);
    setEditing(null);
    setAlreadyRecorded(false);
    setGroup(null);
    setApplyToGroup(false);
    setSaving(false);
    setStaleConflict(false);
    setDuplicateId(null);
    setConfirmCancel(false);
    setRecordLoading(false);
  };

  /** Back to the centred idle view with a blank form. */
  const resetToIdle = () => {
    readCtrl.current?.abort();
    clearContext();
    resetForm(emptyForm(new Date()));
    setActive(false);
    setFocusTick((t) => t + 1);
  };

  const loadRecord = useCallback((record: Incident, recorded: boolean) => {
    setActive(true);
    setEditing(record);
    setOrder(record.order);
    setWarnings([]);
    setTxnInput(record.transaction_id === null ? "" : String(record.transaction_id));
    setAlreadyRecorded(recorded);
    setGroup(null);
    setApplyToGroup(false);
    setStaleConflict(false);
    setDuplicateId(null);
    setLookupError(null);
    const next = incidentToForm(record);
    setForm(next);
    setBaseline(next);
  }, []);

  /** Open a stored record for editing (history row action / recent list). */
  const startEdit = (row: Incident) => {
    clearContext();
    setActive(true);
    // Always start from the stored record, not from the list row — the row
    // may be minutes old and its `version` decides the 409.
    const signal = newReadSignal();
    setRecordLoading(true);
    (async () => {
      try {
        loadRecord(await fetchIncident(row.id, signal), false);
      } catch (err) {
        if (isAbortError(err)) return;
        // Fall back to the list row so the record stays editable; a stale
        // version is still caught by the server.
        loadRecord(row, false);
        toast.error(
          `读取最新记录失败，显示的是列表中的内容: ${err instanceof Error ? err.message : String(err)}`,
        );
      } finally {
        if (!signal.aborted) setRecordLoading(false);
      }
    })();
  };

  /** Register another order into the incident group of `row`. */
  const startAddToGroup = (row: Incident) => {
    readCtrl.current?.abort();
    clearContext();
    setActive(true);
    setGroup({
      id: row.incident_group_id,
      nextIndex: Math.max(row.group_size, 1) + 1,
    });
    resetForm(carryOverForm(incidentToForm(row), new Date()));
    setFocusTick((t) => t + 1);
  };

  // Pick up a record handed over from the history tab. No cleanup here: the
  // parent clears `intent` right away, and that re-run must not abort the
  // read this run started (the unmount effect above does the aborting).
  useEffect(() => {
    if (!intent) return;
    onIntentConsumed();
    if (intent.kind === "edit") startEdit(intent.row);
    else startAddToGroup(intent.row);
    // Only a new intent object is a trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intent]);

  // 最近登记: on mount and after every save.
  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        const res = await apiFetch(
          `${API_BASE}/incidents?${listQuery(1, RECENT_LIMIT)}`,
          { signal: controller.signal },
        );
        // Secondary block: on failure it simply stays as it was.
        if (!res.ok) return;
        const body = (await res.json()) as IncidentListResponse;
        setRecent(body.data.slice(0, RECENT_LIMIT));
      } catch (err) {
        if (isAbortError(err)) return;
      }
    })();
    return () => controller.abort();
  }, [recentTick]);

  // Put the cursor in the transaction-id input: on mount, when returning to
  // idle, and when the next order of the same incident is expected.
  useEffect(() => {
    txnInputRef.current?.focus();
  }, [focusTick]);

  const patch = (p: Partial<LedgerForm>) => setForm((f) => ({ ...f, ...p }));

  const isEdit = editing !== null;
  const showForm = isEdit || order !== null;
  // Also true after 「更换订单」 with typed text still held for the next order.
  const dirty = active && isFormDirty(form, baseline);

  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);

  const requestCancel = () => {
    if (saving) return;
    if (dirty) setConfirmCancel(true);
    else resetToIdle();
  };

  // ── order lookup ────────────────────────────────────────────────────────
  const handleLookup = async () => {
    const id = parseTransactionId(txnInput);
    if (id === null) {
      setLookupError("请输入正确的订单号（纯数字）");
      return;
    }
    const signal = newReadSignal();
    setLookupBusy(true);
    setLookupError(null);
    setDuplicateId(null);
    try {
      const res = await apiFetch(`${API_BASE}/order/${id}`, { signal });
      if (res.status === 404) {
        setLookupError(`CRM 中没有订单号 ${id}，请核对后重试`);
        return;
      }
      if (res.status === 422) {
        setLookupError(`该订单不能登记：${await readErrorDetail(res)}`);
        return;
      }
      if (!res.ok) {
        setLookupError(`查询订单失败：${await readErrorDetail(res)}`);
        return;
      }
      const body = (await res.json()) as OrderPreviewResponse;
      if (body.existing_id !== null && body.existing_id !== undefined) {
        if (group) {
          // Adding to an incident group: an already-recorded order cannot be
          // moved between groups, and switching to it would drop typed text.
          setLookupError(`该订单已登记（记录 #${body.existing_id}），不能加入当前事件组。`);
          return;
        }
        // Already recorded: open that record for editing instead of failing.
        loadRecord(await fetchIncident(body.existing_id, signal), true);
        return;
      }
      setActive(true);
      setOrder(body.order);
      setWarnings(body.warnings ?? []);
      // Seed 是否成功转账 from the order status. Applied to the baseline as
      // well, so a seeded default alone does not count as an unsaved edit.
      const seeded = defaultTransferSucceeded(body.order.txn_status);
      // Only while the field is untouched: a second lookup (更换订单) must not
      // overwrite an explicit choice.
      if (formRef.current.transferSucceeded === baselineRef.current.transferSucceeded) {
        setForm((f) => ({ ...f, transferSucceeded: seeded }));
        setBaseline((b) => ({ ...b, transferSucceeded: seeded }));
      }
    } catch (err) {
      if (isAbortError(err)) return;
      setLookupError(`查询订单失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      if (!signal.aborted) setLookupBusy(false);
    }
  };

  /** Pick a different order; what was typed in the form stays. */
  const handleChangeOrder = () => {
    setOrder(null);
    setWarnings([]);
    setTxnInput("");
    setLookupError(null);
    setDuplicateId(null);
    setFocusTick((t) => t + 1);
  };

  const handleOpenRecord = async (id: number, recorded: boolean) => {
    const signal = newReadSignal();
    setRecordLoading(true);
    try {
      loadRecord(await fetchIncident(id, signal), recorded);
    } catch (err) {
      if (isAbortError(err)) return;
      toast.error(`读取记录失败: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      if (!signal.aborted) setRecordLoading(false);
    }
  };

  // ── save ────────────────────────────────────────────────────────────────
  const handleSave = async (addAnother: boolean) => {
    if (saving) return;
    let res: Response;
    setDuplicateId(null);
    try {
      // Both builders throw a user-facing message on invalid input.
      let url: string;
      let method: "POST" | "PUT";
      let body: unknown;
      if (editing) {
        url = `${API_BASE}/incidents/${editing.id}`;
        method = "PUT";
        body = buildUpdatePayload(
          form,
          editing.version,
          applyToGroup && editing.group_size > 1,
        );
      } else {
        if (!order) return;
        url = `${API_BASE}/incidents`;
        method = "POST";
        body = buildCreatePayload(form, order.transaction_id, group?.id ?? null);
      }
      setSaving(true);
      // retries: 0 — an automatic retry after the server already wrote the
      // row would create a duplicate / bump the version twice.
      res = await apiFetch(
        url,
        {
          method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
        { retries: 0 },
      );
    } catch (err) {
      setSaving(false);
      toast.error(`保存失败: ${err instanceof Error ? err.message : String(err)}`);
      return;
    }

    try {
      if (res.status === 409) {
        const conflict = parseConflict(await res.json().catch(() => null));
        if (editing) {
          setStaleConflict(true);
        } else if (conflict.existing_id !== null) {
          setDuplicateId(conflict.existing_id);
        } else {
          toast.error(`保存失败: ${conflict.detail || "记录冲突"}`);
        }
        return;
      }
      if (!res.ok) {
        toast.error(`保存失败: ${await readErrorDetail(res)}`);
        return;
      }
      const saved = (await res.json()) as Incident;
      toast.success(
        editing
          ? `订单 ${saved.transaction_id ?? `#${saved.id}`} 的登记已更新`
          : `订单 ${saved.transaction_id} 已登记（事件组 #${saved.incident_group_id}）`,
      );
      setRecentTick((t) => t + 1);
      if (!addAnother) {
        resetToIdle();
        return;
      }
      // Next order of the same incident: keep the group and the
      // incident-level fields as the server stored them, clear the order.
      setEditing(null);
      setOrder(null);
      setWarnings([]);
      setTxnInput("");
      setLookupError(null);
      setAlreadyRecorded(false);
      setApplyToGroup(false);
      setStaleConflict(false);
        setGroup({
        id: saved.incident_group_id,
        nextIndex: Math.max(saved.group_size, 1) + 1,
      });
      resetForm(carryOverForm(incidentToForm(saved), new Date()));
      setFocusTick((t) => t + 1);
    } catch (err) {
      toast.error(`保存失败: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSaving(false);
    }
  };

  // ── render ──────────────────────────────────────────────────────────────
  const txnInputProps = {
    id: TXN_INPUT_ID,
    ref: txnInputRef,
    inputMode: "numeric" as const,
    autoComplete: "off",
    value: order ? String(order.transaction_id) : txnInput,
    disabled: order !== null,
    // Read-only rather than disabled while looking up, so the cursor stays put.
    readOnly: lookupBusy,
    "aria-invalid": lookupError !== null,
    "aria-describedby": lookupError ? TXN_ERROR_ID : undefined,
    onChange: (e: ChangeEvent<HTMLInputElement>) => {
      setTxnInput(e.target.value);
      if (lookupError) setLookupError(null);
    },
    onKeyDown: (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter" && !lookupBusy && !order) void handleLookup();
    },
  };

  const cancelConfirm = (
    <Dialog open={confirmCancel} onOpenChange={setConfirmCancel}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>放弃未保存的修改？</DialogTitle>
          <DialogDescription>
            表单里有还没保存的内容，取消后这些内容会丢失。
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => setConfirmCancel(false)}>
            继续填写
          </Button>
          <Button variant="destructive" onClick={resetToIdle}>
            放弃修改
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );

  // Idle: search-home layout.
  if (!active) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center">
        <div className="w-full max-w-2xl">
          <h2 className="mb-4 text-center text-sm font-medium text-muted-foreground">
            出入金风控登记
          </h2>
          {/* Input and button are one unit: same height, radius and text size. */}
          <div className="flex items-center gap-2">
            <Input
              {...txnInputProps}
              aria-label="订单号（CRM transaction id）"
              placeholder="输入订单号，例如 985343"
              className={cn(TXN_BAR_CLASS.hero, "flex-1 tabular-nums")}
            />
            <Button
              type="button"
              className={TXN_BAR_CLASS.hero}
              onClick={() => void handleLookup()}
              disabled={lookupBusy}
            >
              {lookupBusy ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Plus className="h-4 w-4" />
              )}
              新增风控登记
            </Button>
          </div>
          {lookupError ? (
            <p id={TXN_ERROR_ID} className="mt-2 text-center text-sm text-destructive">
              {lookupError}
            </p>
          ) : (
            <p className="mt-2 text-center text-sm text-muted-foreground">
              输入 CRM 订单号后回车，订单信息会自动读取；已登记过的订单会直接打开编辑。
            </p>
          )}
          <RecentList rows={recent} onOpen={startEdit} disabled={lookupBusy} />
        </div>
      </div>
    );
  }

  const title = isEdit
    ? `编辑登记 · 订单 ${editing.transaction_id ?? `（无订单号 #${editing.id}）`}`
    : recordLoading
      ? "编辑登记"
      : group
        ? "添加同事件订单"
        : "新增登记";
  const reportedDate = fromYmd(form.reportedDate);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h2 className="text-base font-semibold">{title}</h2>
        {(group || (isEdit && editing.group_size > 1)) && (
          <Badge variant="secondary" className="tabular-nums">
            {group
              ? `事件组 #${group.id} · 第 ${group.nextIndex} 单`
              : `事件组 #${editing!.incident_group_id} · 共 ${editing!.group_size} 单`}
          </Badge>
        )}
      </div>

      {/* Transaction id — compact, on top */}
      {!isEdit && !recordLoading && (
        <div className="space-y-1.5">
          <div className={cn("flex flex-wrap items-center gap-2", FORM_WIDTH)}>
            <Input
              {...txnInputProps}
              aria-label="订单号（CRM transaction id）"
              placeholder="订单号，例如 985343"
              className={cn(TXN_BAR_CLASS.compact, "min-w-[160px] flex-1 tabular-nums")}
            />
            {order ? (
              <Button
                type="button"
                variant="outline"
                className={cn(TXN_BAR_CLASS.compact, "w-[112px]")}
                onClick={handleChangeOrder}
                disabled={saving}
              >
                更换订单
              </Button>
            ) : (
              <>
                <Button
                  type="button"
                  className={cn(TXN_BAR_CLASS.compact, "w-[112px]")}
                  onClick={() => void handleLookup()}
                  disabled={lookupBusy}
                >
                  {lookupBusy ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Search className="h-4 w-4" />
                  )}
                  确认
                </Button>
                {/* No form below yet, so its 取消 button is not there either. */}
                <Button
                  type="button"
                  variant="ghost"
                  className={TXN_BAR_CLASS.compact}
                  onClick={requestCancel}
                >
                  取消
                </Button>
              </>
            )}
          </div>
          {lookupError && (
            <p id={TXN_ERROR_ID} className="text-sm text-destructive">
              {lookupError}
            </p>
          )}
        </div>
      )}

      {recordLoading && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在读取记录…
        </div>
      )}

      {alreadyRecorded && isEdit && (
        <p className={AMBER_NOTE}>
          该订单已登记过（由 {editing.created_by} 于 {formatHk(editing.created_at)}{" "}
          登记），已切换为编辑这条记录。
        </p>
      )}

      {staleConflict && isEdit && (
        <div className={AMBER_NOTE}>
          <p>
            这条记录在你打开之后被其他人修改过，本次保存没有生效。重新加载会读取最新内容，
            你在这里未保存的修改会丢失，请先把需要的文字复制出来。
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="mt-2"
            onClick={() => void handleOpenRecord(editing.id, alreadyRecorded)}
            disabled={recordLoading}
          >
            重新加载
          </Button>
        </div>
      )}

      {duplicateId !== null && (
        <div className={AMBER_NOTE}>
          <p>该订单刚刚已被登记，本次没有重复新建。</p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="mt-2"
            onClick={() => void handleOpenRecord(duplicateId, true)}
            disabled={recordLoading}
          >
            打开已有记录
          </Button>
        </div>
      )}

      {showForm && (
        <>
          {/* Read-only order block */}
          {order ? (
            <div className="space-y-2 rounded-xl border bg-muted/40 px-4 py-3 md:px-6">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold">订单信息（只读）</span>
                {order.is_test_account && (
                  <Badge
                    variant="outline"
                    className="border-amber-400 text-amber-700 dark:text-amber-300"
                  >
                    测试账户
                  </Badge>
                )}
                {isEdit && editing.source === "import" && (
                  <Badge variant="secondary">历史导入</Badge>
                )}
              </div>
              <dl className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3 lg:grid-cols-5">
                <OrderFact label="客户 ID">{order.client_id ?? "—"}</OrderFact>
                <OrderFact label="客户">{order.client_name ?? "—"}</OrderFact>
                <OrderFact label="场地">{order.venue ?? "—"}</OrderFact>
                <OrderFact label="方向">{labelOf(DIRECTION_LABELS, order.direction)}</OrderFact>
                <OrderFact label="渠道">{order.channel}</OrderFact>
                <OrderFact label="金额 CNY">
                  <span className="tabular-nums">{fmtAmount(order.amount_cny)}</span>
                </OrderFact>
                <OrderFact label="订单状态">{order.txn_status}</OrderFact>
                <OrderFact label="创建时间 (HK)">{formatHk(order.txn_created_at)}</OrderFact>
                <OrderFact label="处理时间 (HK)">{formatHk(order.txn_processed_at)}</OrderFact>
              </dl>
              {isEdit && !editing.order_is_live && (
                <p className="text-xs text-muted-foreground">
                  CRM 从库暂时读不到，以上是登记时保存的订单快照，客户姓名不显示。
                </p>
              )}
              {warnings.length > 0 && (
                <ul className={`${AMBER_NOTE} list-disc space-y-0.5 pl-6`}>
                  {warnings.map((w) => (
                    <li key={w}>{labelOf(ORDER_WARNING_LABELS, w)}</li>
                  ))}
                </ul>
              )}
            </div>
          ) : (
            <p className="rounded-xl border bg-muted/40 px-4 py-3 text-sm text-muted-foreground md:px-6">
              这是历史导入的记录，没有可核对的订单信息。
            </p>
          )}

          {/* The fields the CRM does not have: one field per row, one row shape. */}
          <div
            className={cn(
              "space-y-4 rounded-xl border bg-card px-4 py-4 md:px-6 md:py-5",
              FORM_WIDTH,
            )}
          >
            <div className="space-y-3">
              <Row label="登记日期" htmlFor="frl-date">
                <Popover open={dateOpen} onOpenChange={setDateOpen}>
                  <PopoverTrigger asChild>
                    <button
                      id="frl-date"
                      type="button"
                      className={cn(
                        SELECT_LIKE_TRIGGER_CLASS,
                        !form.reportedDate && "text-muted-foreground",
                      )}
                    >
                      <span className="tabular-nums">{form.reportedDate || "未填"}</span>
                      <CalendarIcon className={TRIGGER_ICON_CLASS} />
                    </button>
                  </PopoverTrigger>
                  <PopoverContent className="w-auto p-0" align="start">
                    <Calendar
                      mode="single"
                      selected={reportedDate}
                      defaultMonth={reportedDate}
                      onSelect={(d) => {
                        patch({ reportedDate: d ? toYmd(d) : "" });
                        setDateOpen(false);
                      }}
                    />
                  </PopoverContent>
                </Popover>
              </Row>

              <Row label="是否成功转账" htmlFor="frl-transfer">
                <EnumSelect<TransferSucceeded>
                  id="frl-transfer"
                  value={form.transferSucceeded}
                  options={TRANSFER_SUCCEEDED}
                  labels={TRANSFER_SUCCEEDED_LABELS}
                  onChange={(v) => patch({ transferSucceeded: v })}
                />
              </Row>

              <Row label="被封资料" htmlFor="frl-scope">
                <EnumSelect<FrozenScope>
                  id="frl-scope"
                  value={form.frozenScope}
                  options={FROZEN_SCOPES}
                  labels={FROZEN_SCOPE_LABELS}
                  onChange={(v) => patch({ frozenScope: v })}
                />
              </Row>

              <Row label="封卡天数" htmlFor="frl-days">
                <Input
                  id="frl-days"
                  inputMode="numeric"
                  autoComplete="off"
                  maxLength={5}
                  placeholder="留空 = 尚不知"
                  className={cn(CONTROL_CLASS, "tabular-nums")}
                  value={form.freezeDaysText}
                  onChange={(e) => patch({ freezeDaysText: e.target.value })}
                />
              </Row>

              <Row label="封卡备注" htmlFor="frl-freeze-note">
                <Input
                  id="frl-freeze-note"
                  className={CONTROL_CLASS}
                  maxLength={FREEZE_NOTE_MAX}
                  placeholder="天数说不清时写在这里，例如「半年后复核」"
                  value={form.freezeNote}
                  onChange={(e) => patch({ freezeNote: e.target.value })}
                />
              </Row>

              <Row label="封卡单位" htmlFor="frl-authority">
                <EnumSelect<Authority>
                  id="frl-authority"
                  value={form.authority}
                  options={AUTHORITIES}
                  labels={AUTHORITY_LABELS}
                  onChange={(v) => patch({ authority: v })}
                />
              </Row>

              <Row label="客户来源" htmlFor="frl-source">
                {/* Optional field: 「未填」 maps back to the empty value. */}
                <Select
                  value={form.clientSource === "" ? NONE_VALUE : form.clientSource}
                  onValueChange={(v) =>
                    patch({ clientSource: v === NONE_VALUE ? "" : (v as ClientSource) })
                  }
                >
                  <SelectTrigger
                    id="frl-source"
                    className={cn(
                      CONTROL_CLASS,
                      form.clientSource === "" && "text-muted-foreground",
                    )}
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NONE_VALUE}>未填</SelectItem>
                    {CLIENT_SOURCES.map((v) => (
                      <SelectItem key={v} value={v}>
                        {CLIENT_SOURCE_LABELS[v]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Row>

              <Row label="客户银行省市" htmlFor="frl-client-province">
                <div className="grid grid-cols-2 gap-2">
                  <ProvinceCombobox
                    id="frl-client-province"
                    placeholder="省"
                    value={form.clientBankProvince}
                    onChange={(v) => patch({ clientBankProvince: v })}
                  />
                  <Input
                    aria-label="客户银行所在市"
                    className={CONTROL_CLASS}
                    maxLength={SHORT_MAX}
                    placeholder="市"
                    value={form.clientBankCity}
                    onChange={(e) => patch({ clientBankCity: e.target.value })}
                  />
                </div>
              </Row>

              <Row label="交易员银行卡省市" htmlFor="frl-trader-province">
                <div className="grid grid-cols-2 gap-2">
                  <ProvinceCombobox
                    id="frl-trader-province"
                    placeholder="省"
                    value={form.traderBankProvince}
                    onChange={(v) => patch({ traderBankProvince: v })}
                  />
                  <Input
                    aria-label="交易员银行卡所在市"
                    className={CONTROL_CLASS}
                    maxLength={SHORT_MAX}
                    placeholder="市"
                    value={form.traderBankCity}
                    onChange={(e) => patch({ traderBankCity: e.target.value })}
                  />
                </div>
              </Row>

              <Row label="事件" htmlFor="frl-event">
                <Textarea
                  id="frl-event"
                  rows={4}
                  className={TEXTAREA_CLASS}
                  maxLength={TEXT_MAX}
                  placeholder="银行卡号和手机号保存时会自动遮盖；请不要填写客户姓名、出生日期和完整 IP。"
                  value={form.eventText}
                  onChange={(e) => patch({ eventText: e.target.value })}
                />
              </Row>

              <Row label="查询结果" htmlFor="frl-result">
                <Textarea
                  id="frl-result"
                  rows={4}
                  className={TEXTAREA_CLASS}
                  maxLength={TEXT_MAX}
                  placeholder="暂时没有结果可以留空，之后再补。"
                  value={form.resultText}
                  onChange={(e) => patch({ resultText: e.target.value })}
                />
              </Row>
            </div>

            {isEdit && editing.group_size > 1 && (
              <label className="flex items-start gap-2 text-sm sm:pl-36">
                <Checkbox
                  className="mt-0.5"
                  checked={applyToGroup}
                  onCheckedChange={(c) => setApplyToGroup(c === true)}
                />
                <span>
                  同步事件/查询结果/封卡单位到同组其他订单
                  <span className="block text-xs text-muted-foreground">
                    同组另外 {editing.group_size - 1} 笔订单的这三项会被本次内容覆盖。
                  </span>
                </span>
              </label>
            )}

            {isEdit && (
              <p className="text-xs text-muted-foreground">
                登记：{editing.created_by} · {formatHk(editing.created_at)}
                ；最近修改：{editing.updated_by} · {formatHk(editing.updated_at)}
              </p>
            )}

            <div className="flex flex-wrap items-center gap-2 border-t pt-4 [&>button]:min-w-[112px]">
              <Button
                type="button"
                onClick={() => void handleSave(false)}
                disabled={saving || recordLoading}
              >
                {saving ? "保存中..." : "保存"}
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => void handleSave(true)}
                disabled={saving || recordLoading}
                title="保存后继续登记同一事件的下一笔订单，事件、查询结果和封卡单位会带过去"
              >
                保存并添加同事件订单
              </Button>
              <Button type="button" variant="ghost" onClick={requestCancel} disabled={saving}>
                取消
              </Button>
            </div>
          </div>
        </>
      )}

      {cancelConfirm}
    </div>
  );
}

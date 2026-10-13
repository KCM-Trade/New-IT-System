/**
 * Single-select searchable province picker (34 province-level names).
 * Composed from the existing Command + Popover primitives; the trigger is
 * styled as a SelectTrigger so it sits next to the Selects of the form
 * unnoticed. 「不选」 at the top of the list clears the value.
 *
 * A stored value outside the list (possible on imported legacy rows) is shown
 * as-is and kept until the user picks something else.
 */
import { useState } from "react";
import { Check, ChevronDown } from "lucide-react";

import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

import { SELECT_LIKE_TRIGGER_CLASS, TRIGGER_ICON_CLASS } from "./controlStyles";
import { PROVINCES } from "./regions";
import { useLedgerT } from "./useLedgerT";

interface ProvinceComboboxProps {
  value: string; // "" = not selected
  onChange: (value: string) => void;
  id?: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
}

export function ProvinceCombobox({
  value,
  onChange,
  id,
  placeholder,
  disabled,
  className,
  "aria-label": ariaLabel,
}: ProvinceComboboxProps) {
  const t = useLedgerT();
  const [open, setOpen] = useState(false);
  const clearLabel = t("fundRiskLedgerPage.province.clear");
  const options =
    value && !PROVINCES.includes(value) ? [value, ...PROVINCES] : PROVINCES;
  const pick = (next: string) => {
    onChange(next);
    setOpen(false);
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          id={id}
          type="button"
          role="combobox"
          aria-expanded={open}
          aria-label={ariaLabel}
          disabled={disabled}
          className={cn(
            SELECT_LIKE_TRIGGER_CLASS,
            !value && "text-muted-foreground",
            className,
          )}
        >
          <span className="truncate">{value || (placeholder ?? t("fundRiskLedgerPage.province.placeholder"))}</span>
          <ChevronDown className={TRIGGER_ICON_CLASS} />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-[220px] p-0" align="start">
        <Command>
          <CommandInput placeholder={t("fundRiskLedgerPage.province.search")} />
          <CommandList>
            <CommandEmpty>{t("fundRiskLedgerPage.province.empty")}</CommandEmpty>
            <CommandGroup>
              {value && (
                <CommandItem value={clearLabel} onSelect={() => pick("")}>
                  <Check className="h-4 w-4 opacity-0" />
                  <span className="text-muted-foreground">{clearLabel}</span>
                </CommandItem>
              )}
              {options.map((p) => (
                <CommandItem key={p} value={p} onSelect={() => pick(p)}>
                  <Check
                    className={cn("h-4 w-4", value === p ? "opacity-100" : "opacity-0")}
                  />
                  {p}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

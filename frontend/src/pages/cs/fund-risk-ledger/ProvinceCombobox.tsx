/**
 * Single-select searchable province picker (34 province-level names).
 * Composed from the existing Command + Popover primitives.
 *
 * A stored value outside the list (possible on imported legacy rows) is shown
 * as-is and kept until the user picks something else.
 */
import { useState } from "react";
import { Check, ChevronsUpDown, X } from "lucide-react";

import { Button } from "@/components/ui/button";
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

import { PROVINCES } from "./regions";

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
  placeholder = "选择省份",
  disabled,
  className,
  "aria-label": ariaLabel,
}: ProvinceComboboxProps) {
  const [open, setOpen] = useState(false);
  const options =
    value && !PROVINCES.includes(value) ? [value, ...PROVINCES] : PROVINCES;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <div className={cn("relative", className)}>
        <PopoverTrigger asChild>
          <Button
            id={id}
            type="button"
            variant="outline"
            role="combobox"
            aria-expanded={open}
            aria-label={ariaLabel}
            disabled={disabled}
            className={cn(
              "h-9 w-full justify-between font-normal",
              // Room for the clear button so it never shifts the layout.
              value ? "pr-14" : "pr-3",
              !value && "text-muted-foreground",
            )}
          >
            <span className="truncate">{value || placeholder}</span>
            <ChevronsUpDown className="h-4 w-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        {value && !disabled && (
          <button
            type="button"
            aria-label="清除省份"
            className="absolute right-8 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
            onClick={() => onChange("")}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      <PopoverContent className="w-[220px] p-0" align="start">
        <Command>
          <CommandInput placeholder="搜索省份..." />
          <CommandList>
            <CommandEmpty>没有匹配的省份</CommandEmpty>
            <CommandGroup>
              {options.map((p) => (
                <CommandItem
                  key={p}
                  value={p}
                  onSelect={() => {
                    onChange(p);
                    setOpen(false);
                  }}
                >
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

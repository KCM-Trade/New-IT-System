"use client"

import * as React from "react"
import { Check, ChevronsUpDown } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"

interface Option {
  value: string
  label: string
}

interface SimpleMultiSelectProps {
  options: Option[]
  value: string[]
  onValueChange: (value: string[]) => void
  placeholder?: string
  searchPlaceholder?: string
  className?: string
}

export function SimpleMultiSelect({
  options,
  value,
  onValueChange,
  placeholder = "选择选项...",
  searchPlaceholder = "搜索...",
  className
}: SimpleMultiSelectProps) {
  const [open, setOpen] = React.useState(false)
  const [searchValue, setSearchValue] = React.useState("")
  
  // 移动端检测
  const isMobile = React.useMemo(() => {
    if (typeof window === 'undefined') return false
    return window.innerWidth < 768
  }, [])

  // 过滤搜索结果
  const filteredOptions = React.useMemo(() => {
    return options.filter(option => 
      option.label.toLowerCase().includes(searchValue.toLowerCase())
    )
  }, [options, searchValue])

  // 切换单个选项选择状态
  const toggleOption = (optionValue: string) => {
    let newValue: string[]
    
    if (value.includes("__ALL__")) {
      // 当前是全选状态，切换到具体选择模式
      if (optionValue === "__ALL__") {
        // 点击的是全选，取消全选
        newValue = []
      } else {
        // 点击的是具体选项，从全选状态中移除该选项
        const allValues = options.map(opt => opt.value)
        newValue = allValues.filter(v => v !== optionValue && v !== "__ALL__")
      }
    } else {
      // 正常的切换逻辑
      if (value.includes(optionValue)) {
        newValue = value.filter(v => v !== optionValue)
      } else {
        newValue = [...value, optionValue]
      }
    }
    
    onValueChange(newValue)
  }

  // 全选/取消全选
  const toggleAllOptions = () => {
    if (value.includes("__ALL__")) {
      // 取消全选
      onValueChange([])
    } else {
      // 全选
      onValueChange(["__ALL__"])
    }
  }

  // 计算显示文本
  const getDisplayText = () => {
    if (value.length === 0) return placeholder
    
    if (value.includes("__ALL__")) {
      return `全部品种 (${options.length})`
    }
    
    const selectedOptions = options.filter(opt => value.includes(opt.value))
    if (selectedOptions.length === 0) return placeholder
    
    return `已选择 ${selectedOptions.length} 个品种`
  }

  // 检查选项是否被选中
  const isOptionSelected = (optionValue: string) => {
    return value.includes("__ALL__") || value.includes(optionValue)
  }

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            role="combobox"
            aria-expanded={open}
            className="h-9 w-52 justify-between text-left touch-manipulation"
          >
            <span className="truncate">{getDisplayText()}</span>
            <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent 
          className="w-[calc(100vw-2rem)] md:w-80 p-0 max-h-[30vh] md:max-h-[50vh] overflow-hidden" 
          align={isMobile ? "center" : "start"}
          side="bottom"
          sideOffset={4}
          avoidCollisions={true}
          collisionPadding={isMobile ? 24 : 16}
          onOpenAutoFocus={(e: Event) => {
            // 移动端防止自动聚焦导致键盘弹起
            if (isMobile) {
              e.preventDefault()
            }
          }}
        >
          <Command>
            <CommandInput 
              placeholder={searchPlaceholder} 
              value={searchValue}
              onValueChange={setSearchValue}
              className="h-9" 
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="none"
              spellCheck={false}
            />
            <CommandList className="max-h-48 md:max-h-80 overflow-y-auto">
              <CommandEmpty>未找到品种。</CommandEmpty>
              
              <CommandGroup>
                {/* 全选选项 */}
                <CommandItem
                  onSelect={toggleAllOptions}
                  className="flex items-center space-x-2 min-h-10 touch-manipulation"
                >
                  <Checkbox
                    checked={value.includes("__ALL__")}
                    onChange={() => {}}
                    className="pointer-events-none"
                  />
                  <span className="font-medium">全部品种</span>
                  <Check
                    className={cn(
                      "ml-auto h-4 w-4",
                      value.includes("__ALL__") ? "opacity-100" : "opacity-0"
                    )}
                  />
                </CommandItem>

                {/* 具体选项 */}
                {filteredOptions.map((option) => (
                  <CommandItem
                    key={option.value}
                    onSelect={() => toggleOption(option.value)}
                    className="flex items-center space-x-2 min-h-10 touch-manipulation"
                  >
                    <Checkbox
                      checked={isOptionSelected(option.value)}
                      onChange={() => {}}
                      className="pointer-events-none"
                    />
                    <span>{option.label}</span>
                    <Check
                      className={cn(
                        "ml-auto h-4 w-4",
                        isOptionSelected(option.value) ? "opacity-100" : "opacity-0"
                      )}
                    />
                  </CommandItem>
                ))}
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
    </div>
  )
}

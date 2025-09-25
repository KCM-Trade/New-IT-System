"use client"

import * as React from "react"
import { Check, ChevronsUpDown, ChevronDown, ChevronRight } from "lucide-react"
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

interface GroupOption {
  value: string
  label: string
}

interface MultiSelectComboboxProps {
  options: GroupOption[]
  value: string[]
  onValueChange: (value: string[]) => void
  placeholder?: string
  searchPlaceholder?: string
  className?: string
}

export function MultiSelectCombobox({
  options,
  value,
  onValueChange,
  placeholder = "选择选项...",
  searchPlaceholder = "搜索组别...",
  className
}: MultiSelectComboboxProps) {
  const [open, setOpen] = React.useState(false)
  const [searchValue, setSearchValue] = React.useState("")
  const [expandedGroups, setExpandedGroups] = React.useState<Set<string>>(new Set())
  
  // 移动端检测
  const isMobile = React.useMemo(() => {
    if (typeof window === 'undefined') return false
    return window.innerWidth < 768
  }, [])
  

  // 处理选项分组逻辑
  const processedData = React.useMemo(() => {
    // 过滤搜索结果
    const filtered = options.filter(option => 
      option.label.toLowerCase().includes(searchValue.toLowerCase())
    )

    // 按组分类 - Test组别和所有组别
    const testGroups: GroupOption[] = []
    const allFilteredGroups: GroupOption[] = []

    filtered.forEach(option => {
      allFilteredGroups.push(option)
      if (option.label.toLowerCase().includes('test')) {
        testGroups.push(option)
      }
    })

    return {
      testGroups: testGroups.sort((a, b) => a.label.localeCompare(b.label)),
      allGroups: allFilteredGroups.sort((a, b) => a.label.localeCompare(b.label)),
      hasTestGroups: testGroups.length > 0,
      hasAllGroups: allFilteredGroups.length > 0
    }
  }, [options, searchValue])

  // 切换组展开状态
  const toggleGroupExpansion = (groupKey: string) => {
    const newExpanded = new Set(expandedGroups)
    if (newExpanded.has(groupKey)) {
      newExpanded.delete(groupKey)
    } else {
      newExpanded.add(groupKey)
    }
    setExpandedGroups(newExpanded)
  }

  // 切换单个选项选择状态
  const toggleOption = (optionValue: string) => {
    let newValue: string[]
    
    if (value.includes("__ALL__")) {
      // 当前是全选状态，切换到具体选择模式
      if (value.includes(optionValue)) {
        // 如果要取消选择这个选项，获取所有其他选项
        const allValues = processedData.allGroups.map(g => g.value)
        newValue = allValues.filter(v => v !== optionValue)
      } else {
        // 这种情况不应该发生，因为全选状态下所有选项都应该被选中
        const allValues = processedData.allGroups.map(g => g.value)
        newValue = allValues
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

  // 全选/取消全选所有选项
  const toggleAllOptions = () => {
    if (value.includes("__ALL__")) {
      // 取消全选，清空所有选择
      onValueChange([])
    } else {
      // 全选，只设置"__ALL__"标识符
      onValueChange(["__ALL__"])
    }
  }

  // Test组别全选/取消全选
  const toggleTestGroups = () => {
    const testValues = processedData.testGroups.map(g => g.value)
    let newValue: string[]
    
    if (value.includes("__ALL__")) {
      // 当前是全选状态，切换到取消Test组别的具体选择模式
      const allValues = processedData.allGroups.map(g => g.value)
      newValue = allValues.filter(v => !testValues.includes(v))
    } else {
      const allTestSelected = testValues.every(v => value.includes(v))
      
      if (allTestSelected) {
        // 取消选择所有Test组别
        newValue = value.filter(v => !testValues.includes(v))
      } else {
        // 选择所有Test组别
        newValue = [...new Set([...value, ...testValues])]
      }
    }
    
    onValueChange(newValue)
  }


  // 计算显示文本
  const getDisplayText = () => {
    if (value.length === 0) return placeholder
    if (value.includes("__ALL__")) return `全部组别 (${options.length})`
    
    const selectedOptions = options.filter(opt => value.includes(opt.value))
    if (selectedOptions.length === 0) return placeholder
    
    return `已选择 ${selectedOptions.length} 个组别`
  }

  // 检查选项是否被选中（考虑"__ALL__"状态）
  const isOptionSelected = (optionValue: string) => {
    return value.includes("__ALL__") || value.includes(optionValue)
  }

  // 检查Test组别的选择状态
  const getTestGroupsSelectionState = () => {
    const testValues = processedData.testGroups.map(g => g.value)
    if (testValues.length === 0) return { checked: false, indeterminate: false }
    
    if (value.includes("__ALL__")) {
      return { checked: true, indeterminate: false }
    }
    
    const selectedTestCount = testValues.filter(v => value.includes(v)).length
    if (selectedTestCount === 0) return { checked: false, indeterminate: false }
    if (selectedTestCount === testValues.length) return { checked: true, indeterminate: false }
    return { checked: false, indeterminate: true }
  }

  const testSelectionState = getTestGroupsSelectionState()

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
              <CommandEmpty>未找到组别。</CommandEmpty>
              
              {/* 全部组别选项 */}
              <CommandGroup>
                <CommandItem
                  onSelect={toggleAllOptions}
                  className="flex items-center space-x-2 min-h-10 touch-manipulation"
                >
                  <Checkbox
                    checked={value.includes("__ALL__")}
                    onChange={() => {}}
                    className="pointer-events-none"
                  />
                  <span className="font-medium">全部组别</span>
                  <Check
                    className={cn(
                      "ml-auto h-4 w-4",
                      value.includes("__ALL__") ? "opacity-100" : "opacity-0"
                    )}
                  />
                </CommandItem>
              </CommandGroup>

              {/* Test组别分组 */}
              {processedData.hasTestGroups && (
                <CommandGroup>
                  <div className="px-2 py-1.5 text-xs font-semibold text-muted-foreground">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-auto p-0 font-semibold justify-start w-full"
                      onClick={(e) => {
                        e.preventDefault()
                        toggleGroupExpansion("test")
                      }}
                    >
                      <div className="flex items-center space-x-2 w-full">
                        {expandedGroups.has("test") ? (
                          <ChevronDown className="h-3 w-3" />
                        ) : (
                          <ChevronRight className="h-3 w-3" />
                        )}
                        <div
                          className="flex items-center"
                          onClick={(e) => {
                            e.stopPropagation()
                            toggleTestGroups()
                          }}
                        >
                          <Checkbox
                            checked={testSelectionState.checked}
                            onChange={() => {}}
                            className="pointer-events-none"
                          />
                          {testSelectionState.indeterminate && (
                            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                              <div className="w-2 h-0.5 bg-current rounded" />
                            </div>
                          )}
                        </div>
                        <span>Test 组别 ({processedData.testGroups.length})</span>
                      </div>
                    </Button>
                  </div>

                  {expandedGroups.has("test") && processedData.testGroups.map((option) => (
                    <CommandItem
                      key={option.value}
                      onSelect={() => toggleOption(option.value)}
                      className="flex items-center space-x-2 pl-8 min-h-10 touch-manipulation"
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
              )}

              {/* 所有组别（除Test组别外的直接显示） */}
              {processedData.hasAllGroups && (
                <CommandGroup>
                  <div className="px-2 py-1.5 text-xs font-semibold text-muted-foreground">
                    所有组别
                  </div>

                  {/* 显示非Test组别 */}
                  {processedData.allGroups
                    .filter(option => !option.label.toLowerCase().includes('test'))
                    .map((option) => (
                    <CommandItem
                      key={option.value}
                      onSelect={() => toggleOption(option.value)}
                      className="flex items-center space-x-2 pl-4 min-h-10 touch-manipulation"
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
              )}
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>

    </div>
  )
}
